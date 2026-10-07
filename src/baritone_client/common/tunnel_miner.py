"""Dig planned tunnels with the bridge and bring the bot home alive.

``tunnel_planner`` decides what is safe to dig; this module does the digging
and every check that needs the live world: health, hostiles, the block that is
actually there right before it is broken, and whether the bot really arrived.
Anything unexpected raises ``MineAbort`` and the caller retraces its own trail
to the surface. It never uses Baritone's ``mine``, which dives into caves.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from .tunnel_planner import (
    FLUIDS, GRAVITY, HAZARDS, IRON_ORES, PLANTS, STONE, Cell, Move, View,
    approach_cells, coarse_waypoint, is_ore, loop_erase, ore_is_safe, plan,
    required_cells,
)

OPEN_BLOCKS = frozenset({"minecraft:air", "minecraft:cave_air"})
HOSTILES = frozenset(
    "minecraft:" + name
    for name in (
        "zombie", "zombie_villager", "husk", "drowned", "skeleton", "stray",
        "bogged", "spider", "cave_spider", "creeper", "silverfish", "witch",
        "pillager", "vindicator", "phantom", "slime",
    )
)
VIEW_RADIUS = 8
ABORT_HEALTH = 10.0
HOSTILE_RADIUS = 7.0
#: How long the defence reflex gets to deal with a mob before the trip ends.
HOSTILE_PATIENCE = 12.0
#: What digging fills the pack with. Safe to throw away mid-trip: it is rubble.
DIGGING_JUNK = frozenset(
    "minecraft:" + name
    for name in (
        "cobblestone", "dirt", "coarse_dirt", "rooted_dirt", "grass_block", "podzol",
        "mud", "clay", "andesite", "diorite", "granite", "tuff", "calcite",
        "cobbled_deepslate", "deepslate", "sand", "sandstone", "gravel",
        "dripstone_block", "smooth_basalt", "moss_block",
    )
)
TORCH_EVERY = 7
#: Kept inside the planner's window: a cell more than 6 blocks from the bot is unplannable.
PLAN_MOVES = 5
MAX_TRAIL = 400
MIN_FREE_SLOTS = 3


class MineAbort(Exception):
    """The trip must stop and retrace; ``reason`` says why."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class TripStats:
    moves: int = 0
    dug: int = 0
    torches: int = 0
    reason: str = ""


def _unwrap(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    data = value.get("data", value)
    return data if isinstance(data, dict) else {}


def own_cells_of(spine: Sequence[Cell]) -> Set[Cell]:
    """Every open cell a recorded spine implies, so old tunnels are not 'caves'."""
    own: Set[Cell] = set(spine)
    for frm, to in zip(spine, spine[1:]):
        if abs(frm[0] - to[0]) + abs(frm[2] - to[2]) == 1 and abs(frm[1] - to[1]) <= 1:
            own.update(required_cells(frm, to))
            own.add((frm[0], frm[1] + 1, frm[2]))
    return own


def free_slots(client: Any) -> int:
    """Empty main-inventory slots (the hotbar and the 27 above it)."""
    from ..inventory_evidence import valid_inventory

    raw = client.transport.dispatch("get_inventory", {})
    data = raw.get("data", raw) if isinstance(raw, dict) else None
    if not valid_inventory(data) or not isinstance(data.get("inventory"), list):
        # A missing/partial snapshot must never look like a completely empty
        # pack. Callers treat zero slots as a reason to stop before digging.
        return 0
    items = data["inventory"]
    occupied = set()
    for item in items:
        if item.get("id") in (None, "", "minecraft:air") or int(item.get("count", 0)) <= 0:
            continue
        slot = item.get("slot")
        if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot <= 35 or slot in occupied:
            return 0
        occupied.add(slot)
    used = len(occupied)
    return 36 - used


class TunnelMiner:
    def __init__(
        self,
        client: Any,
        *,
        surface_y: int,
        deadline: float,
        spine: Sequence[Cell] = (),
        entrance: Optional[Cell] = None,
        min_y: int = 12,
        count_fn: Optional[Callable[[str], int]] = None,
        patience: float = HOSTILE_PATIENCE,
    ) -> None:
        self.client = client
        self.surface_y = int(surface_y)
        self.deadline = float(deadline)
        self.min_y = int(min_y)
        self.patience = float(patience)
        self.trail: List[Cell] = list(spine) or ([entrance] if entrance else [])
        self.own: Set[Cell] = own_cells_of(self.trail)
        self.skip: Set[Cell] = set()
        self.stats = TripStats()
        self._since_torch = 0
        self._torch_failures = 0
        self._count = count_fn or self._default_count

    # -- bridge access ------------------------------------------------------
    def _call(self, route: str, payload: Optional[dict] = None) -> Dict[str, Any]:
        try:
            return _unwrap(self.client.transport.dispatch(route, payload or {}))
        except Exception as exc:
            raise MineAbort(f"bridge {route} failed: {exc}") from exc

    def _default_count(self, item: str) -> int:
        from .inventory import count_item

        return int(count_item(self.client, item) or 0)

    def state(self) -> Dict[str, Any]:
        live = self._call("get_state")
        position = live.get("block_position") or live.get("position") or {}
        try:
            live["cell"] = tuple(int(float(position[axis])) for axis in ("x", "y", "z"))
        except (KeyError, TypeError, ValueError) as exc:
            raise MineAbort("player position unavailable") from exc
        return live

    def cell(self) -> Cell:
        return self.state()["cell"]

    def block(self, cell: Cell) -> str:
        data = self._call("get_block", {"x": cell[0], "y": cell[1], "z": cell[2]})
        return str(data.get("id") or data.get("block") or "")

    def view(self) -> View:
        data = self._call("get_view", {"radius": VIEW_RADIUS})
        voxels = {
            (int(v["x"]), int(v["y"]), int(v["z"])): str(v.get("id", ""))
            for v in data.get("voxels", [])
        }
        return View(voxels, self.cell(), VIEW_RADIUS)

    def free_slots(self) -> int:
        return free_slots(self.client)

    def make_room(self) -> int:
        """Throw away dug rubble to free pack space; returns slots freed."""
        from .inventory import drop_items

        before = self.free_slots()
        try:
            drop_items(self.client, sorted(DIGGING_JUNK), max_stacks=8)
        except Exception as exc:
            print(f"TUNNEL MINER: could not drop rubble ({exc})")
        return max(0, self.free_slots() - before)

    # -- safety -------------------------------------------------------------
    def hostiles(self) -> int:
        entities = self._call("get_entities", {"radius": int(HOSTILE_RADIUS) + 3}).get("entities", [])
        return sum(
            1
            for e in entities
            if e.get("type") in HOSTILES and float(e.get("distance", 99) or 99) <= HOSTILE_RADIUS
        )

    def check(self, *, returning: bool = False) -> Dict[str, Any]:
        """Raise ``MineAbort`` unless it is still safe to keep digging."""
        live = self.state()
        if live.get("is_dead") or float(live.get("health", 0) or 0) <= 0:
            raise MineAbort("died")
        if "overworld" not in str(live.get("dimension", "minecraft:overworld")):
            raise MineAbort("left the overworld")
        if float(live.get("health", 20) or 0) < ABORT_HEALTH:
            raise MineAbort("health low")
        if not returning and time.monotonic() > self.deadline:
            raise MineAbort("time")
        if self.hostiles():
            # The safety system owns fighting; give it a moment, then leave.
            waited = 0.0
            while waited < self.patience and self.hostiles():
                time.sleep(1.0)
                waited += 1.0
                if float(self.state().get("health", 20) or 0) < ABORT_HEALTH:
                    raise MineAbort("health low")
            if self.hostiles():
                raise MineAbort("hostile mob")
        return live

    # -- actions ------------------------------------------------------------
    def dig(self, cell: Cell) -> None:
        """Break one cell, after confirming what is really there."""
        block = self.block(cell)
        if block in OPEN_BLOCKS:
            return
        if not block or block == "minecraft:void_air":
            raise MineAbort("terrain not loaded")
        if (
            block in FLUIDS or block in HAZARDS or block in GRAVITY
            or not (block in STONE or block in PLANTS or is_ore(block))
        ):
            raise MineAbort(f"unexpected block {block}")
        for attempt in range(2):
            if attempt:
                self.equip()  # eating mid-dig swaps the held item
            self._call("dig_block", {"x": cell[0], "y": cell[1], "z": cell[2], "max_ticks": 160})
            deadline = time.monotonic() + 9.0
            while time.monotonic() < deadline:
                time.sleep(0.3)
                if self.block(cell) in OPEN_BLOCKS:
                    self.stats.dug += 1
                    self.own.add(cell)  # what we dug is our tunnel, not a cave
                    return
        raise MineAbort(f"could not dig {block}")

    def equip(self) -> None:
        from .resources import equip_best_pickaxe

        if not equip_best_pickaxe(self.client):
            raise MineAbort("no pickaxe")

    def step(self, target: Cell) -> None:
        from .navigation import goto

        for tolerance in (0.8, 0.5):
            goto(self.client, *target, timeout=25, check_interval=0.5, tolerance=tolerance)
            if self.cell() == target:
                return
        raise MineAbort("did not arrive")

    def torch(self, cell: Cell, move_to: Cell) -> None:
        if self._since_torch < TORCH_EVERY or cell[1] >= self.surface_y - 3:
            return
        if self._torch_failures >= 3 or self._count("minecraft:torch") <= 0:
            return
        from . import harness_ops

        if harness_ops.place_block_exact(self.client, *cell, "minecraft:torch", allow_break=False):
            self.stats.torches += 1
            self._since_torch = 0
            self._torch_failures = 0
        else:
            self._torch_failures += 1  # stop trying after three misses in a row
        if self.cell() != move_to:
            self.step(move_to)  # placing may have shuffled the bot

    def advance(self, move: Move) -> None:
        """Dig and enter one cell of a planned path."""
        here = self.check()["cell"]
        if here != move.frm:
            raise MineAbort("moved off the planned path")
        if not self.trail or self.trail[-1] != here:
            self.trail = loop_erase(self.trail + [here])  # e.g. entered a cell off
        if move.dig:
            self.equip()
        for cell in move.dig:
            self.dig(cell)
        self.step(move.to)
        self.own.update(move.required)
        self.trail = loop_erase(self.trail + [move.to])[-MAX_TRAIL:]
        self.stats.moves += 1
        self._since_torch += 1
        self.torch(move.frm, move.to)

    def execute(self, path: Sequence[Move]) -> None:
        for move in path:
            self.advance(move)

    # -- movement over our own tunnel --------------------------------------
    def _hop(
        self, cells: Sequence[Cell], *, timeout: int = 40,
        returning: bool = False, return_deadline: Optional[float] = None,
    ) -> None:
        from .navigation import goto

        if not cells:
            return
        setting = self._call("settings", {"get": "allowBreak"}).get("value")
        if setting not in ("true", "false"):
            raise MineAbort("cannot verify digging is disabled for tunnel travel")
        if setting == "true":
            self._call("settings", {"set": "allowBreak", "value": "false"})
            if self._call("settings", {"get": "allowBreak"}).get("value") != "false":
                raise MineAbort("could not disable digging for tunnel travel")
        try:
            here = self.cell()
            for target in cells:
                if return_deadline is not None and time.monotonic() >= return_deadline:
                    raise MineAbort("return route time limit")
                dx, dy, dz = (target[i] - here[i] for i in range(3))
                if abs(dx) + abs(dz) != 1 or abs(dy) > 1:
                    raise MineAbort("recorded tunnel contains a non-adjacent waypoint")
                arrived = False
                for _attempt in range(2):
                    self.check(returning=returning)
                    if return_deadline is not None and time.monotonic() >= return_deadline:
                        raise MineAbort("return route time limit")
                    goto(
                        self.client, *target,
                        timeout=min(timeout, max(1, int(return_deadline - time.monotonic())))
                        if return_deadline is not None else timeout,
                        check_interval=0.5, tolerance=0.8,
                    )
                    observed = self.cell()
                    if observed == target:
                        arrived = True
                        break
                if not arrived:
                    raise MineAbort("blocked while following the tunnel")
                here = target
        finally:
            # Navigation must have stopped before restoring a setting that
            # could let Baritone break terrain on its next path.
            live = self._call("get_state")
            if live.get("is_pathing") is False and setting == "true":
                self._call("settings", {"set": "allowBreak", "value": "true"})
                if self._call("settings", {"get": "allowBreak"}).get("value") != "true":
                    raise MineAbort("could not restore allowBreak after tunnel travel")

    def descend(self, spine: Sequence[Cell]) -> None:
        """Walk a recorded spine from the entrance to its end."""
        if len(spine) > 1:
            if self.cell() != spine[0]:
                raise MineAbort("not at the recorded tunnel entrance")
            self._hop(spine[1:])

    def retreat(self) -> bool:
        """Retrace our own trail to the entrance; True when the bot got there."""
        path = list(reversed(loop_erase(self.trail)))
        try:
            here = self.cell()
            forward = list(reversed(path))
            if here not in forward:
                return False
            index = forward.index(here)
            route = list(reversed(forward[: index + 1]))
            if len(route) > 1:
                deadline = time.monotonic() + 240.0
                self._hop(route[1:], timeout=20, returning=True, return_deadline=deadline)
            return self.cell() == forward[0]
        except MineAbort as exc:
            print(f"TUNNEL MINER: retreat stopped ({exc.reason})")
            return False

    # -- the mining loop ----------------------------------------------------
    def pick_target(self, here: Cell) -> Optional[Cell]:
        found = self._call(
            "find_blocks", {"blocks": sorted(IRON_ORES), "radius": 48, "limit": 400}
        ).get("found", [])
        best: Optional[Tuple[float, Cell]] = None
        for entry in found:
            ore = (int(entry["x"]), int(entry["y"]), int(entry["z"]))
            if ore in self.skip or ore[1] < self.min_y or ore[1] > self.surface_y - 3:
                continue
            cost = abs(ore[0] - here[0]) + abs(ore[2] - here[2]) + 1.5 * abs(ore[1] - here[1])
            if best is None or cost < best[0]:
                best = (cost, ore)
        return best[1] if best else None

    def mine_adjacent(self, view: View, here: Cell, raw_now: Callable[[], int]) -> bool:
        """Mine any safe iron ore touching the bot's feet or head cell."""
        standing = self.own | {here, (here[0], here[1] + 1, here[2])}
        for ore in sorted(view.iron_ores() - self.skip):
            touching = (
                abs(ore[0] - here[0]) + abs(ore[2] - here[2]) == 1 and ore[1] in (here[1], here[1] + 1)
            )
            if not touching:
                continue
            if not ore_is_safe(view, ore, standing, self.surface_y):
                self.skip.add(ore)
                continue
            before = raw_now()
            self.equip()
            self.dig(ore)
            time.sleep(1.2)  # the drop pops out and is picked up from the next cell
            self.skip.add(ore)
            if raw_now() <= before:
                print(f"TUNNEL MINER: {ore} dug but no raw iron arrived")
            return True
        return False

    def mine(self, raw_goal: int, raw_now: Callable[[], int]) -> str:
        """Dig toward iron until the goal, the clock or a safety limit."""
        misses = 0
        while True:
            self.check()
            if raw_now() >= raw_goal:
                return "quota"
            if self.free_slots() < MIN_FREE_SLOTS:
                self.make_room()
                if self.free_slots() < MIN_FREE_SLOTS:
                    return "inventory full"
            view = self.view()
            here = view.center
            print(
                f"TUNNEL MINER: at {here}, {self.surface_y - here[1]} below the entrance; "
                f"{self.stats.moves} moves, {self.stats.dug} dug, {self.stats.torches} torches, "
                f"{raw_now()} raw iron"
            )
            if self.mine_adjacent(view, here, raw_now):
                continue
            ores = set()
            for ore in view.iron_ores() - self.skip:
                if ore_is_safe(view, ore, self.own, self.surface_y):
                    ores.add(ore)
                else:
                    self.skip.add(ore)
            goals = {cell for ore in ores for cell in approach_cells(ore)}
            path = plan(view, here, goals, self.own, self.surface_y) if goals else None
            if goals and not path:
                # Unreachable from here: forget ores we could have planned to.
                self.skip.update(o for o in ores if any(view.inside(c) for c in approach_cells(o)))
            if not path:
                target = self.pick_target(here)
                if target is None:
                    return "no reachable iron"
                if self._count("minecraft:torch") <= 0 and here[1] < self.surface_y - 12:
                    return "out of torches"
                for steps, prefer_z in ((PLAN_MOVES, False), (PLAN_MOVES, True), (3, False)):
                    waypoint = coarse_waypoint(here, target, steps, prefer_z=prefer_z)
                    if waypoint != here:
                        path = plan(view, here, {waypoint}, self.own, self.surface_y)
                        if path:
                            break
                if not path:
                    self.skip.add(target)
                    misses += 1
                    if misses >= 3:
                        return "no safe route"
                    continue
            misses = 0
            self.execute(path[:PLAN_MOVES])


__all__ = [
    "DIGGING_JUNK", "HOSTILES", "MineAbort", "TripStats", "TunnelMiner", "free_slots", "own_cells_of",
]
