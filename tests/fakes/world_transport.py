"""A small in-memory Minecraft world plus a bridge transport that answers
from it, for testing terraform.py's verification without a live bridge.

The whole point is that a fake which always says "yes" cannot test code whose
job is to detect "no". FakeWorld holds real block state; WorldTransport
answers get_block/get_inventory/get_state/build{status} from that state and
interprets "#sel ..." chat commands as a real (if simplified) builder, using
a pluggable fill_policy to decide what actually happens when "#sel set
<block>" fires -- including doing nothing at all, which is the defect this
whole module exists to make reproducible.
"""

from __future__ import annotations

import re
from typing import Callable, Dict, Iterable, Optional, Sequence, Tuple

Position = Tuple[int, int, int]
Box = Tuple[Position, Position]
FillPolicy = Callable[["FakeWorld", Box, str, int], None]

AIR_FAMILY = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}
UNBREAKABLE = {"minecraft:bedrock"}

_SEL_POS1 = re.compile(r"#sel pos1 (-?\d+) (-?\d+) (-?\d+)")
_SEL_POS2 = re.compile(r"#sel pos2 (-?\d+) (-?\d+) (-?\d+)")
_SEL_SET = re.compile(r"#sel set (\S+)")


def _iter_box(box: Box) -> Iterable[Position]:
    (x1, y1, z1), (x2, y2, z2) = box
    for x in range(min(x1, x2), max(x1, x2) + 1):
        for y in range(min(y1, y2), max(y1, y2) + 1):
            for z in range(min(z1, z2), max(z1, z2) + 1):
                yield (x, y, z)


class FakeWorld:
    """A sparse block map. Unset positions read as `default_block`."""

    def __init__(self, default_block: str = "minecraft:air"):
        self.default_block = default_block
        self.blocks: Dict[Position, str] = {}

    def get(self, x: int, y: int, z: int) -> str:
        return self.blocks.get((x, y, z), self.default_block)

    def set(self, x: int, y: int, z: int, block_id: str) -> None:
        self.blocks[(x, y, z)] = block_id

    def fill_box(
        self, x1: int, y1: int, z1: int, x2: int, y2: int, z2: int, block_id: str
    ) -> None:
        for pos in _iter_box(((x1, y1, z1), (x2, y2, z2))):
            self.blocks[pos] = block_id


# ---------------------------------------------------------------------------
# Fill policies. Each decides what "#sel set <block>" actually does to the
# world for one invocation. `attempt` is the 0-based index of this #sel set
# call (across both clear and fill calls) so a policy can vary by call count.
# ---------------------------------------------------------------------------


def apply_honestly() -> FillPolicy:
    """Write `block` everywhere in the box except unbreakable positions."""

    def policy(world: FakeWorld, box: Box, block: str, attempt: int) -> None:
        for x, y, z in _iter_box(box):
            if world.get(x, y, z) not in UNBREAKABLE:
                world.set(x, y, z, block)

    return policy


def silent_noop() -> FillPolicy:
    """Write nothing. THE defect under test: an idle builder that did nothing."""

    def policy(world: FakeWorld, box: Box, block: str, attempt: int) -> None:
        return None

    return policy


def noop_then_apply(after: int = 1) -> FillPolicy:
    """No-op for the first `after` calls, then apply honestly."""
    honest = apply_honestly()

    def policy(world: FakeWorld, box: Box, block: str, attempt: int) -> None:
        if attempt >= after:
            honest(world, box, block, attempt)

    return policy


def apply_with_budget(n: int) -> FillPolicy:
    """Place at most `n` blocks total across every call -- simulates running
    out of a carried material mid-fill."""
    remaining = [n]

    def policy(world: FakeWorld, box: Box, block: str, attempt: int) -> None:
        for x, y, z in _iter_box(box):
            if remaining[0] <= 0:
                return
            if world.get(x, y, z) not in UNBREAKABLE:
                world.set(x, y, z, block)
                remaining[0] -= 1

    return policy


def apply_except(positions: Iterable[Position]) -> FillPolicy:
    """Apply honestly except at the given positions -- a partial fill."""
    excluded = {tuple(p) for p in positions}

    def policy(world: FakeWorld, box: Box, block: str, attempt: int) -> None:
        for pos in _iter_box(box):
            if pos in excluded:
                continue
            if world.get(*pos) not in UNBREAKABLE:
                world.set(*pos, block)

    return policy


def apply_then_reflow(liquid: str = "minecraft:water") -> FillPolicy:
    """Apply honestly, then immediately let a liquid reclaim the result --
    the coastline case, where a neighbouring chunk keeps re-flooding this one.
    Simplified: the reflow happens right after the write rather than on a
    timer, which is sufficient to exercise "verification must not treat this
    as flat" without modelling real tick timing.
    """
    honest = apply_honestly()

    def policy(world: FakeWorld, box: Box, block: str, attempt: int) -> None:
        honest(world, box, block, attempt)
        for x, y, z in _iter_box(box):
            if world.get(x, y, z) not in UNBREAKABLE:
                world.set(x, y, z, liquid)

    return policy


def never_starts() -> FillPolicy:
    """The builder accepts "#sel set" but is_building never observes True --
    a rejected block id or an immediately-refused selection."""

    def policy(world: FakeWorld, box: Box, block: str, attempt: int) -> None:
        return None

    policy._never_starts = True  # type: ignore[attr-defined]
    return policy


class WorldTransport:
    """Answers bridge routes from a FakeWorld; chat "#sel ..." drives a
    simplified builder simulation gated by a FillPolicy.

    building_delay controls how many status/state polls the simulated
    builder takes to finish a fill after "#sel set" is issued -- enough for
    _await_build_complete's poll loop to run more than once, matching how a
    live builder is never done on the very first check.
    """

    def __init__(
        self,
        world: Optional[FakeWorld] = None,
        fill_policy: Optional[FillPolicy] = None,
        inventory: Optional[Dict[str, int]] = None,
        building_delay: int = 2,
        position: Position = (0, 64, 0),
    ):
        self.world = world if world is not None else FakeWorld()
        self.fill_policy = fill_policy if fill_policy is not None else apply_honestly()
        self.inventory = dict(inventory) if inventory is not None else {"minecraft:stone": 10_000}
        self.building_delay = building_delay
        self.position = position

        self.calls: list[tuple[str, dict]] = []
        self.state_reads: list[dict] = []
        self.chat_messages: list[str] = []

        self._pos1: Optional[Position] = None
        self._pos2: Optional[Position] = None
        self._is_building = False
        self._building_ticks = 0
        self._pending: Optional[Tuple[str, Box, int]] = None
        self._set_call_index = 0
        self.dead = False

    # -- helpers -----------------------------------------------------------

    def route_count(self, route: str) -> int:
        return sum(1 for r, _ in self.calls if r == route)

    def chat_count(self, message: str) -> int:
        return self.chat_messages.count(message)

    @property
    def selection(self) -> Optional[Box]:
        if self._pos1 is not None and self._pos2 is not None:
            return (self._pos1, self._pos2)
        return None

    def _advance_builder(self) -> None:
        if self._pending is None:
            return
        self._building_ticks -= 1
        if self._building_ticks > 0:
            return
        block, box, attempt = self._pending
        self.fill_policy(self.world, box, block, attempt)
        self._is_building = False
        self._pending = None

    # -- dispatch ------------------------------------------------------------

    def dispatch(self, route: str, payload: Optional[dict] = None) -> dict:
        payload = payload or {}
        self.calls.append((route, payload))

        if route == "get_block":
            block_id = self.world.get(
                int(payload["x"]), int(payload["y"]), int(payload["z"])
            )
            return {"id": block_id}

        if route == "get_inventory":
            return {
                "status": "ok",
                "data": {
                    "inventory": [
                        {"id": item_id, "count": count, "slot": i}
                        for i, (item_id, count) in enumerate(self.inventory.items())
                        if count > 0
                    ],
                    "armor": [],
                    "offhand": [],
                },
            }

        if route == "get_state":
            self._advance_builder()
            state = {
                "dimension": "minecraft:overworld",
                "world_time": 6000,
                "health": 20.0,
                "food_level": 20,
                "is_pathing": self._is_building,
                "is_dead": self.dead,
                "block_position": {
                    "x": self.position[0],
                    "y": self.position[1],
                    "z": self.position[2],
                },
            }
            self.state_reads.append(state)
            return state

        if route == "build":
            if payload.get("action") == "status":
                self._advance_builder()
                sel = self.selection
                return {
                    "is_building": self._is_building,
                    "selection_count": 1 if sel else 0,
                    "current_selection": (
                        [list(sel[0]), list(sel[1])] if sel else None
                    ),
                }
            return {"status": "ok"}

        if route == "chat":
            message = str(payload.get("message", ""))
            self.chat_messages.append(message)
            self._handle_chat(message)
            return {}

        return {"status": "ok"}

    def _handle_chat(self, message: str) -> None:
        if message == "#sel clear":
            self._pos1 = None
            self._pos2 = None
            return

        m = _SEL_POS1.match(message)
        if m:
            self._pos1 = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return

        m = _SEL_POS2.match(message)
        if m:
            self._pos2 = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return

        m = _SEL_SET.match(message)
        if m:
            block = m.group(1)
            box = self.selection
            attempt = self._set_call_index
            self._set_call_index += 1
            if box is None:
                return
            if getattr(self.fill_policy, "_never_starts", False):
                self._is_building = False
                self._pending = None
                return
            self._is_building = True
            self._building_ticks = self.building_delay
            self._pending = (block, box, attempt)
