"""Manifest-driven bounded forestry worker for the industrial plantation."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

from baritone_client.common.base import robust_place
from baritone_client.common.inventory import get_inventory, select_item
from baritone_client.common.navigation import goto, goto_xz
from baritone_client.common.resources import LOG_BLOCKS, ensure_supplies
from baritone_client.operations.bridge_guard import NonOpClientGuard, WorkerSafetyError
from baritone_client.operations.camp_armor import provision_camp_armor
from baritone_client.operations.resilient_read_transport import ResilientReadTransport
from baritone_client.operations.shared_storage_ingress import serialized_deposit
from baritone_client.transport.transport import TcpTransport

BlockPosition = tuple[int, int, int]

AXES = (
    "minecraft:netherite_axe",
    "minecraft:diamond_axe",
    "minecraft:iron_axe",
    "minecraft:stone_axe",
    "minecraft:golden_axe",
    "minecraft:wooden_axe",
)

#: Replant stock by species. Defensive completeness only: the harvest path is
#: already restricted to `forestry.allowed_logs` (oak and birch today) at both
#: search and break time, so the extra rows never fire until that manifest list
#: is widened. They exist so widening it cannot silently produce trees this map
#: fails to describe, which reads as "no sapling" and skips them without a word.
SAPLING_BY_LOG = {
    "minecraft:oak_log": "minecraft:oak_sapling",
    "minecraft:birch_log": "minecraft:birch_sapling",
    "minecraft:spruce_log": "minecraft:spruce_sapling",
    "minecraft:jungle_log": "minecraft:jungle_sapling",
    "minecraft:acacia_log": "minecraft:acacia_sapling",
    "minecraft:dark_oak_log": "minecraft:dark_oak_sapling",
    "minecraft:cherry_log": "minecraft:cherry_sapling",
    "minecraft:mangrove_log": "minecraft:mangrove_propagule",
}
#: Every replant item, for stock checks and restocking.
SAPLING_ITEMS = tuple(sorted(set(SAPLING_BY_LOG.values())))
#: Standing position for shared-storage access, previously repeated inline.
STORAGE_DOCK = (574, 79, -303)

FORESTRY_KEEP_ITEMS = {
    *AXES,
    "minecraft:iron_sword",
    "minecraft:diamond_sword",
    "minecraft:netherite_sword",
    "minecraft:shield",
    "minecraft:torch",
    "minecraft:bread",
    "minecraft:cooked_beef",
    "minecraft:cooked_chicken",
    "minecraft:cooked_mutton",
    "minecraft:cooked_porkchop",
    "minecraft:baked_potato",
    "minecraft:golden_apple",
}


def _cycle_detail(
    *,
    trees_seen: int,
    harvested_now: int,
    skipped_no_sapling: int,
    saplings_on_hand: int,
    restocked: int,
    source: str,
    grid_planted: int = 0,
) -> str:
    """Describe one cycle's real outcome, naming the blocker when idle."""
    if grid_planted:
        return f"no wild trees in range; planted {grid_planted} plantation sapling(s)"
    if not trees_seen:
        return (
            "no trees found in range and no saplings to plant the grid"
            if saplings_on_hand == 0
            else "exploring assigned renewable world sector"
        )
    if harvested_now:
        note = f"{source} harvest and exact replant: {harvested_now}/{trees_seen} trees"
        return f"{note} (restocked {restocked} saplings)" if restocked else note
    if skipped_no_sapling:
        return (
            f"BLOCKED: skipped all {skipped_no_sapling} tree(s) with "
            f"{saplings_on_hand} saplings on hand; storage restock returned "
            f"{restocked}"
        )
    return f"saw {trees_seen} tree(s) but felled none"


def block_position(value: Any, label: str) -> BlockPosition:
    """Validate and normalize one manifest block position."""
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{label} must contain exactly X, Y, Z")
    return int(value[0]), int(value[1]), int(value[2])


def unwrap(value: Any) -> dict[str, Any]:
    """Normalize bridge responses that may wrap their payload in data."""
    if not isinstance(value, dict):
        return {}
    data = value.get("data", value)
    return data if isinstance(data, dict) else {}


class RuntimeState:
    """Minimal state facade required by protected storage helpers."""

    def __init__(self, home: BlockPosition, storage: BlockPosition) -> None:
        self.custom_data = {
            "base_location": list(home),
            "structures": {"starter_house": {"supply_chest": list(storage)}},
            "locations": {
                "chest": [
                    {
                        "dimension": "minecraft:overworld",
                        "x": storage[0],
                        "y": storage[1],
                        "z": storage[2],
                    }
                ]
            },
        }

    def get_locations(self, kind: str) -> dict[str, list[dict[str, Any]]]:
        return {kind: list(self.custom_data.get("locations", {}).get(kind, []))}


class BoundedForester:
    """One bot constrained to the shared, manifest-defined plantation."""

    def __init__(self, manifest_path: Path, bot_name: str) -> None:
        self.manifest_path = manifest_path.resolve()
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        try:
            bot = self.manifest["bots"][bot_name]
            forestry = self.manifest["forestry"]
        except KeyError as error:
            raise ValueError(f"Missing forestry manifest value: {error}") from error

        self.bot_name = bot_name
        self.bridge_port = int(bot["bridge_port"])
        self.home = block_position(bot["home"], f"{bot_name}.home")
        self.storage = block_position(self.manifest["storage_input"], "storage_input")
        self.provisioning_storage = tuple(
            block_position(position, "provisioning_storage")
            for position in self.manifest.get("provisioning_storage", [self.storage])
        )
        self.furnace = block_position(bot["furnace"], f"{bot_name}.furnace")
        self.crafting_table = block_position(
            bot["crafting_table"], f"{bot_name}.crafting_table"
        )
        self.staging = block_position(
            bot["forestry_staging"], f"{bot_name}.forestry_staging"
        )
        self.camp = block_position(self.manifest["camp_anchor"], "camp_anchor")
        self.protected_players = tuple(self.manifest.get("protected_players", ()))
        expedition_safety = self.manifest.get("surface_expedition_safety", {})
        self.minimum_armor_points = int(
            expedition_safety.get("minimum_armor_points", 0)
        )
        self.minimum_armor_pieces = int(
            expedition_safety.get("minimum_armor_pieces", 0)
        )
        self.minimum_torches = int(expedition_safety.get("minimum_torches", 0))
        self.bounds = {
            key: int(forestry["bounds"][key])
            for key in ("min_x", "max_x", "min_y", "max_y", "min_z", "max_z")
        }
        self.grid_y = int(forestry["grid_y"])
        self.grid = tuple(
            (int(x), self.grid_y, int(z))
            for z in forestry["grid_z"]
            for x in forestry["grid_x"]
        )
        self.allowed_logs = {str(item) for item in forestry["allowed_logs"]}
        self.allowed_saplings = tuple(
            str(item) for item in forestry["allowed_saplings"]
        )
        world = forestry["world"]
        self.camp_exclusion = {
            key: int(world["camp_exclusion"][key])
            for key in ("min_x", "max_x", "min_z", "max_z")
        }
        self.world_radius = int(world["max_distance_from_camp"])
        self.world_tree_limit = int(world["trees_per_cycle"])
        self.navigation_leg = int(world.get("max_navigation_leg", 32))
        # Sorted-output chests to fall back on when the transient input chest
        # holds no saplings. Configurable; defaults to the row directly below
        # the input, which is where the camp's sorter deposits.
        sources = self.manifest.get("sorted_storage") or [
            [self.storage[0], self.storage[1] - 1, self.storage[2] + 1]
        ]
        self.sapling_sources = tuple((int(p[0]), int(p[1]), int(p[2])) for p in sources)
        self.exploration_waypoints = tuple(
            (int(value[0]), int(value[1])) for value in bot["forestry_waypoints"]
        )
        self._validate_geometry()

    def _validate_geometry(self) -> None:
        for label, position in (
            ("staging", self.staging),
            *(("grid", position) for position in self.grid),
        ):
            x, y, z = position
            if not (
                self.bounds["min_x"] <= x <= self.bounds["max_x"]
                and self.bounds["min_y"] <= y <= self.bounds["max_y"]
                and self.bounds["min_z"] <= z <= self.bounds["max_z"]
            ):
                raise ValueError(
                    f"{label} position outside forestry bounds: {position}"
                )
        if not self.exploration_waypoints:
            raise ValueError(f"{self.bot_name} has no forestry exploration waypoints")
        for x, z in self.exploration_waypoints:
            distance_squared = (x - self.camp[0]) ** 2 + (z - self.camp[2]) ** 2
            if distance_squared > self.world_radius**2:
                raise ValueError(f"forestry waypoint exceeds world radius: {(x, z)}")
            if (
                self.camp_exclusion["min_x"] <= x <= self.camp_exclusion["max_x"]
                and self.camp_exclusion["min_z"] <= z <= self.camp_exclusion["max_z"]
            ):
                raise ValueError(f"forestry waypoint enters camp exclusion: {(x, z)}")
        if not 8 <= self.navigation_leg <= 64:
            raise ValueError("forestry max_navigation_leg must be between 8 and 64")

    def guard(self, client: Any) -> NonOpClientGuard:
        return NonOpClientGuard(client, self.bot_name, self.protected_players)

    def require_safe_server(self, client: Any) -> None:
        self.guard(client).require_safe()

    def require_survival(self, client: Any) -> None:
        self.guard(client).require_survival()

    def park(self, client: Any, position: BlockPosition) -> bool:
        """Walk to a camp position.

        This used a privileged server reposition. Walking is the job of a survival worker, and
        a free instant reposition also hides real problems: an unreachable
        storage dock read as success, so every deposit failure downstream
        looked like a container fault instead of a pathing one. Failure is now
        visible and the caller can react to it.
        """
        x, y, z = position
        try:
            if not self.walk_xz_in_legs(
                client,
                x,
                z,
                tolerance=float(self.navigation_leg + 4),
                purpose="return to storage",
            ):
                return False
            # A horizontal goal can finish underneath the warehouse. Send the
            # known-reachable interaction point as an exact goal, then verify
            # arrival through the non-operator bridge state stream.
            goto(client, x, y, z, timeout=20, tolerance=4.0)
            best_distance = float("inf")
            last_progress = time.monotonic()
            deadline = time.monotonic() + 45.0
            while time.monotonic() < deadline:
                px, py, pz = self.current_position(client)
                distance = math.dist((px, py, pz), (x, y, z))
                if distance <= 4.0:
                    return True
                if distance < best_distance - 0.75:
                    best_distance = distance
                    last_progress = time.monotonic()
                if time.monotonic() - last_progress >= 20.0:
                    break
                time.sleep(1.0)
            print(
                f"PARK: exact dock approach stalled {best_distance:.1f}m away",
                flush=True,
            )
            return False
        except WorkerSafetyError:
            raise
        except Exception as error:
            print(f"PARK: could not walk to {position} ({error})", flush=True)
            return False

    def state_ready(self, client: Any) -> tuple[bool, str]:
        state = self.guard(client).require_safe()
        if float(state.get("health", 0.0) or 0.0) < 16:
            return False, "health below 16"
        if float(state.get("food_level", 0.0) or 0.0) < 14:
            return False, "food below 14"
        if str(state.get("difficulty", "peaceful")).lower() != "peaceful":
            armor_points = int(state.get("armor_points", 0) or 0)
            armor_pieces = int(state.get("armor_count", 0) or 0)
            torches = int(get_inventory(client).get("minecraft:torch", 0) or 0)
            if (
                armor_points < self.minimum_armor_points
                or armor_pieces < self.minimum_armor_pieces
            ):
                return False, (
                    "surface expedition blocked: need armor "
                    f"({armor_points}/{self.minimum_armor_points} points, "
                    f"{armor_pieces}/{self.minimum_armor_pieces} pieces)"
                )
            if torches < self.minimum_torches:
                return False, (
                    "surface expedition blocked: need torches "
                    f"({torches}/{self.minimum_torches})"
                )
        return True, "ready"

    def provision_safety_kit(self, client: Any) -> tuple[bool, str]:
        return provision_camp_armor(
            client,
            storage_sources=self.provisioning_storage,
            furnace=self.furnace,
            crafting_table=self.crafting_table,
            target_pieces=self.minimum_armor_pieces,
            minimum_torches=self.minimum_torches,
        )

    def in_bounds(self, block: dict[str, Any]) -> bool:
        try:
            x, y, z = int(block["x"]), int(block["y"]), int(block["z"])
        except (KeyError, TypeError, ValueError):
            return False
        return (
            self.bounds["min_x"] <= x <= self.bounds["max_x"]
            and self.bounds["min_y"] <= y <= self.bounds["max_y"]
            and self.bounds["min_z"] <= z <= self.bounds["max_z"]
            and str(block.get("block", block.get("id", ""))) in self.allowed_logs
        )

    def in_world_harvest_zone(self, position: BlockPosition) -> bool:
        """Reject developed camp terrain and any tree beyond the local region."""
        x, _y, z = position
        if (
            self.camp_exclusion["min_x"] <= x <= self.camp_exclusion["max_x"]
            and self.camp_exclusion["min_z"] <= z <= self.camp_exclusion["max_z"]
        ):
            return False
        return (x - self.camp[0]) ** 2 + (z - self.camp[2]) ** 2 <= self.world_radius**2

    @staticmethod
    def connected_tree_bases(positions: set[BlockPosition]) -> list[BlockPosition]:
        """Collapse connected log components into one lowest trunk position."""
        remaining = set(positions)
        bases: list[BlockPosition] = []
        while remaining:
            start = remaining.pop()
            component = {start}
            frontier = [start]
            while frontier:
                x, y, z = frontier.pop()
                for neighbor in (
                    (x - 1, y, z),
                    (x + 1, y, z),
                    (x, y - 1, z),
                    (x, y + 1, z),
                    (x, y, z - 1),
                    (x, y, z + 1),
                ):
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        component.add(neighbor)
                        frontier.append(neighbor)
            bases.append(
                min(
                    component,
                    key=lambda position: (position[1], position[0], position[2]),
                )
            )
        return sorted(
            bases, key=lambda position: (position[0], position[2], position[1])
        )

    def plantation_tree_bases(self, client: Any) -> list[BlockPosition]:
        """Return the lowest trunk block of each connected plantation tree."""
        try:
            result = unwrap(
                client.transport.dispatch(
                    "find_blocks",
                    {"blocks": sorted(self.allowed_logs), "radius": 48, "limit": 512},
                )
            )
        except Exception:
            return []
        discovered = result.get("found", result.get("blocks", []))
        blocks = [
            block
            for block in discovered
            if isinstance(block, dict) and self.in_bounds(block)
        ]
        positions = {
            (int(block["x"]), int(block["y"]), int(block["z"])) for block in blocks
        }
        return self.connected_tree_bases(positions)

    def world_tree_bases(self, client: Any) -> list[BlockPosition]:
        """Find renewable trees near the current exploration frontier."""
        try:
            result = unwrap(
                client.transport.dispatch(
                    "find_blocks",
                    {"blocks": sorted(self.allowed_logs), "radius": 48, "limit": 512},
                )
            )
        except Exception:
            return []
        discovered = result.get("found", result.get("blocks", []))
        positions = {
            (int(block["x"]), int(block["y"]), int(block["z"]))
            for block in discovered
            if isinstance(block, dict)
            and str(block.get("block", block.get("id", ""))) in self.allowed_logs
            and all(axis in block for axis in ("x", "y", "z"))
            and self.in_world_harvest_zone(
                (int(block["x"]), int(block["y"]), int(block["z"]))
            )
        }
        return self.connected_tree_bases(positions)

    def current_position(self, client: Any) -> tuple[float, float, float]:
        """Read live position from the non-operator client bridge."""
        return self.guard(client).position()

    def current_xz(self, client: Any) -> tuple[float, float]:
        """Read the authoritative live horizontal position from the server."""
        x, _y, z = self.current_position(client)
        return x, z

    def walk_xz_in_legs(
        self,
        client: Any,
        target_x: int,
        target_z: int,
        *,
        tolerance: float,
        purpose: str,
    ) -> bool:
        """Reach a distant point through short, verified Baritone legs.

        The sector endpoints remain intentionally far from camp.  Limiting each
        individual request makes a failed route local and recoverable instead
        of stranding a worker after one 100-250 block command.
        """
        current_x, current_z = self.current_xz(client)
        initial_distance = math.hypot(target_x - current_x, target_z - current_z)
        max_legs = max(1, math.ceil(initial_distance / self.navigation_leg) * 3)
        for _leg in range(max_legs):
            current_x, current_z = self.current_xz(client)
            remaining = math.hypot(target_x - current_x, target_z - current_z)
            if remaining <= tolerance:
                return True
            step = min(float(self.navigation_leg), remaining)
            leg_x = round(current_x + ((target_x - current_x) / remaining) * step)
            leg_z = round(current_z + ((target_z - current_z) / remaining) * step)
            try:
                # `goto_xz` may return False solely because a bridge status
                # poll timed out after its fire-and-forget request arrived.
                # Start the bounded command, then observe its actual result
                # through client state below.
                goto_xz(
                    client,
                    int(leg_x),
                    int(leg_z),
                    timeout=30,
                    tolerance=min(6.0, tolerance),
                    check_interval=1.0,
                )
            except Exception as error:
                print(f"FORESTER: {purpose} leg failed ({error})", flush=True)
                return False

            best_remaining = remaining
            last_progress = time.monotonic()
            leg_deadline = time.monotonic() + max(
                25.0, min(90.0, float(self.navigation_leg * 3))
            )
            while time.monotonic() < leg_deadline:
                try:
                    after_x, after_z = self.current_xz(client)
                except WorkerSafetyError:
                    raise
                except Exception as error:
                    print(
                        f"FORESTER: {purpose} bridge progress poll failed ({error})",
                        flush=True,
                    )
                    time.sleep(1.0)
                    continue
                after_remaining = math.hypot(target_x - after_x, target_z - after_z)
                if after_remaining <= tolerance:
                    return True
                if after_remaining < best_remaining - 1.0:
                    best_remaining = after_remaining
                    last_progress = time.monotonic()
                if time.monotonic() - last_progress < 20.0:
                    time.sleep(1.0)
                    continue
                try:
                    client.transport.dispatch("cancel", {})
                except Exception:
                    pass
                print(
                    f"FORESTER: {purpose} leg made no progress "
                    f"({remaining:.1f}m -> {best_remaining:.1f}m)",
                    flush=True,
                )
                return False
            try:
                client.transport.dispatch("cancel", {})
            except Exception:
                pass
            print(
                f"FORESTER: {purpose} leg timed out after progress "
                f"({remaining:.1f}m -> {best_remaining:.1f}m)",
                flush=True,
            )
            return False
        print(f"FORESTER: {purpose} exhausted its verified leg budget", flush=True)
        return False

    def explore_for_trees(
        self,
        client: Any,
        cursor: int,
    ) -> tuple[list[BlockPosition], int]:
        """Walk to one assigned world sector and scan its loaded forest."""
        x, z = self.exploration_waypoints[cursor % len(self.exploration_waypoints)]
        self.require_safe_server(client)
        reached = self.walk_xz_in_legs(
            client,
            x,
            z,
            tolerance=8.0,
            purpose="exploration",
        )
        return (self.world_tree_bases(client) if reached else []), cursor + 1

    def carried_saplings(self, client: Any) -> int:
        """Replant stock on hand, for the species this plantation allows."""
        inventory = get_inventory(client)
        return sum(int(inventory.get(item, 0) or 0) for item in self.allowed_saplings)

    def restock_saplings(self, client: Any, state: Any, minimum: int = 8) -> int:
        """Draw replant stock from shared storage before felling anything.

        `matching_sapling` refuses to cut a tree the worker cannot replant,
        which is the right rule -- but nothing ever acquired saplings, so a
        worker that ran out simply skipped every tree forever while reporting
        success. Bot07 logged six such cycles with four trees in sight,
        carrying nothing but an axe and a sword.
        """
        from baritone_client.common.inventory import withdraw_required_from_chest

        if self.carried_saplings(client) >= minimum:
            return 0
        self.park(client, STORAGE_DOCK)
        want = {item: minimum for item in self.allowed_saplings}

        # Try the input chest first, then the sorted chests. `storage_input`
        # feeds an auto-sorter, so it is deliberately transient -- whatever the
        # bots deposit is routed out to a per-item chest within seconds. Asking
        # only the input chest therefore finds saplings only by luck: on
        # 2026-08-07 Bot16 stood beside four trees it refused to cut, holding
        # zero saplings, while 190 sat in the sorted chest one block below.
        # Sorted forestry outputs are the authoritative renewable supply.
        # Probe them before the general bot drop-off: a slow or inaccessible
        # intake chest can time out the bridge and poison the rest of the
        # worker session before a reachable sapling chest is attempted.
        sources = (*self.sapling_sources, self.storage)
        for chest in dict.fromkeys(sources):
            try:
                moved = withdraw_required_from_chest(client, chest, want, state=state)
            except WorkerSafetyError:
                raise
            except Exception as error:  # storage is advisory, never fatal here
                print(
                    f"FORESTER: sapling restock from {chest} failed ({error})",
                    flush=True,
                )
                continue
            if moved > 0:
                print(
                    f"FORESTER: drew {moved} sapling stack(s) from {chest}", flush=True
                )
                return moved
            if self.carried_saplings(client) >= minimum:
                return 0
        return 0

    def matching_sapling(self, client: Any, tree_base: BlockPosition) -> str | None:
        """Reserve the matching sapling before allowing a tree to be cut."""
        x, y, z = tree_base
        block = unwrap(client.transport.dispatch("get_block", {"x": x, "y": y, "z": z}))
        sapling = SAPLING_BY_LOG.get(str(block.get("id", "")))
        if sapling and int(get_inventory(client).get(sapling, 0) or 0) > 0:
            return sapling
        return None

    def replant_tree_base(
        self,
        client: Any,
        tree_base: BlockPosition,
        sapling: str,
    ) -> bool:
        """Replace a harvested wild or plantation tree at its exact base."""
        self.require_safe_server(client)
        self.require_survival(client)
        try:
            return bool(robust_place(client, *tree_base, sapling))
        finally:
            self.require_survival(client)

    @staticmethod
    def equip_best_axe(client: Any) -> str | None:
        """Equip the strongest carried axe without dropping the prior hotbar item."""
        for axe in AXES:
            if select_item(client, axe, allow_swap=True):
                return axe
        return None

    def break_exact_log(
        self,
        client: Any,
        position: BlockPosition,
        timeout: float = 45.0,
    ) -> bool:
        x, y, z = position
        block = unwrap(client.transport.dispatch("get_block", {"x": x, "y": y, "z": z}))
        if str(block.get("id", "")) not in self.allowed_logs:
            return False
        if self.equip_best_axe(client) is None:
            return False
        self.require_safe_server(client)
        self.require_survival(client)
        try:
            client.transport.dispatch("break_block", {"x": x, "y": y, "z": z})
            deadline = time.time() + timeout
            while time.time() < deadline:
                live = unwrap(
                    client.transport.dispatch("get_block", {"x": x, "y": y, "z": z})
                )
                if str(live.get("id", "")) not in self.allowed_logs:
                    return True
                time.sleep(1)
            client.transport.dispatch("cancel", {})
            return False
        finally:
            try:
                client.transport.dispatch("cancel", {})
            except Exception:
                pass
            self.require_survival(client)

    def tree_drops(
        self,
        client: Any,
        tree_base: BlockPosition,
        radius: float = 10.0,
    ) -> list[dict[str, Any]]:
        """Return nearby item entities produced inside the forestry boundary."""
        try:
            response = unwrap(client.transport.dispatch("get_entities", {"radius": 32}))
        except Exception:
            return []
        entities = response.get("entities", [])
        bx, by, bz = tree_base
        drops: list[tuple[float, dict[str, Any]]] = []
        for entity in entities:
            if not isinstance(entity, dict) or entity.get("type") != "minecraft:item":
                continue
            position = entity.get("position", {})
            if not isinstance(position, dict) or not all(
                axis in position for axis in ("x", "y", "z")
            ):
                continue
            x = float(position["x"])
            y = float(position["y"])
            z = float(position["z"])
            distance = ((x - bx) ** 2 + (y - by) ** 2 + (z - bz) ** 2) ** 0.5
            if distance > radius:
                continue
            rounded = (int(round(x)), int(round(y)), int(round(z)))
            in_plantation = (
                self.bounds["min_x"] - 1 <= x <= self.bounds["max_x"] + 1
                and self.bounds["min_y"] - 2 <= y <= self.bounds["max_y"] + 1
                and self.bounds["min_z"] - 1 <= z <= self.bounds["max_z"] + 1
            )
            if not in_plantation and not self.in_world_harvest_zone(rounded):
                continue
            drops.append((distance, entity))
        return [entity for _distance, entity in sorted(drops, key=lambda row: row[0])]

    def collect_tree_drops(self, client: Any, tree_base: BlockPosition) -> bool:
        """Walk across a bounded tree-drop pile before selecting another tree."""
        time.sleep(0.75)
        for _pass in range(3):
            drops = self.tree_drops(client, tree_base)
            if not drops:
                return True
            for drop in drops:
                self.require_safe_server(client)
                position = drop["position"]
                goto(
                    client,
                    int(round(float(position["x"]))),
                    int(round(float(position["y"]))),
                    int(round(float(position["z"]))),
                    timeout=12,
                    tolerance=1.25,
                    check_interval=0.5,
                )
                time.sleep(0.4)
        return not self.tree_drops(client, tree_base)

    def plantation_drops(self, client: Any) -> list[dict[str, Any]]:
        """Return every currently loaded item entity inside the plantation."""
        try:
            response = unwrap(client.transport.dispatch("get_entities", {"radius": 48}))
        except Exception:
            return []
        entities = response.get("entities", [])
        drops: list[dict[str, Any]] = []
        for entity in entities:
            if not isinstance(entity, dict) or entity.get("type") != "minecraft:item":
                continue
            position = entity.get("position", {})
            if not isinstance(position, dict) or not all(
                axis in position for axis in ("x", "y", "z")
            ):
                continue
            x = float(position["x"])
            y = float(position["y"])
            z = float(position["z"])
            if (
                self.bounds["min_x"] - 1 <= x <= self.bounds["max_x"] + 1
                and self.bounds["min_y"] - 2 <= y <= self.bounds["max_y"] + 1
                and self.bounds["min_z"] - 1 <= z <= self.bounds["max_z"] + 1
            ):
                drops.append(entity)
        return sorted(drops, key=lambda entity: float(entity.get("distance", 999)))

    def collect_plantation_drops(self, client: Any) -> bool:
        """Sweep orphaned drops even when no mature trees remain."""
        for _pass in range(3):
            drops = self.plantation_drops(client)
            if not drops:
                return True
            for drop in drops:
                self.require_safe_server(client)
                position = drop["position"]
                goto(
                    client,
                    int(round(float(position["x"]))),
                    int(round(float(position["y"]))),
                    int(round(float(position["z"]))),
                    timeout=12,
                    tolerance=1.25,
                    check_interval=0.5,
                )
                time.sleep(0.4)
        return not self.plantation_drops(client)

    def replant_grid(self, client: Any) -> int:
        inventory = get_inventory(client)
        available: list[str] = []
        for item_id in self.allowed_saplings:
            available.extend([item_id] * max(0, int(inventory.get(item_id, 0) or 0)))
        planted = 0
        self.require_safe_server(client)
        self.require_survival(client)
        try:
            for x, y, z in self.grid:
                if not available:
                    break
                block = unwrap(
                    client.transport.dispatch("get_block", {"x": x, "y": y, "z": z})
                )
                if str(block.get("id", "")) not in (
                    "minecraft:air",
                    "minecraft:cave_air",
                ):
                    continue
                item_id = available.pop(0)
                if robust_place(client, x, y, z, item_id):
                    planted += 1
        finally:
            self.require_survival(client)
        return planted

    def unload_nonforestry_cargo(self, client: Any, state: RuntimeState) -> int:
        """Bank everything not needed for safe harvesting and replanting."""
        inventory = get_inventory(client)
        keep = FORESTRY_KEEP_ITEMS | set(self.allowed_saplings)
        deposit = set(inventory) - keep
        if not deposit:
            return 0
        return serialized_deposit(
            client,
            self.storage,
            timeout=20.0,
            attempts=1,
            deposit_items=deposit,
            keep_items=keep,
            state=state,
        )


def write_status(path: Path, **values: Any) -> None:
    values["timestamp"] = time.time()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(values, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest", type=Path, default=REPO / "industrial_camp_manifest.json"
    )
    parser.add_argument("--bot", required=True)
    parser.add_argument("--hours", type=float, default=8.0)
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--max-banked", type=int, default=512)
    args = parser.parse_args()

    forester = BoundedForester(args.manifest, args.bot)
    run_dir = Path.cwd()
    status_path = run_dir / "bounded_forester_status.json"
    stop_path = run_dir / "bounded_forester.stop"
    pid_path = run_dir / "bounded_forester.pid"
    stop_path.unlink(missing_ok=True)
    pid_path.write_text(str(os.getpid()), encoding="ascii")

    tcp_transport = TcpTransport("localhost", forester.bridge_port, timeout=5.0)
    # Reads are idempotent. Keep TcpTransport's reconnecting retry policy and
    # add the longer outer backoff for bridge stalls during live work.
    transport = ResilientReadTransport(tcp_transport, attempts=4)
    client = SimpleNamespace(transport=transport)
    state = RuntimeState(forester.home, forester.storage)
    started = time.time()
    harvested_total = planted_total = banked_total = trees_total = cycles = 0
    exploration_cursor = 0
    reason = "time limit reached"
    write_status(
        status_path,
        bot=forester.bot_name,
        state="starting",
        cycles=0,
        trees_harvested=0,
        trees_replanted=0,
        logs_harvested=0,
        logs_banked=0,
        saplings_planted=0,
        detail="starting bounded forestry worker",
    )

    try:
        forester.require_safe_server(client)
        armored, armor_detail = forester.provision_safety_kit(client)
        if not armored:
            reason = armor_detail
            return 2
        ready, detail = forester.state_ready(client)
        if not ready:
            reason = detail
            return 2
        forester.require_survival(client)
        carried_logs = get_inventory(client)
        if any(int(carried_logs.get(item, 0) or 0) > 0 for item in LOG_BLOCKS):
            forester.park(client, STORAGE_DOCK)
            serialized_deposit(
                client,
                forester.storage,
                timeout=20.0,
                attempts=1,
                deposit_items=set(LOG_BLOCKS),
                state=state,
            )

        deadline = started + max(60.0, args.hours * 3600.0)
        while (
            time.time() < deadline
            and banked_total < args.max_banked
            and not stop_path.exists()
        ):
            forester.require_safe_server(client)
            ready, detail = forester.state_ready(client)
            if not ready:
                reason = detail
                break

            if not forester.park(client, STORAGE_DOCK):
                reason = "could not reach shared storage dock before exploration"
                write_status(
                    status_path,
                    bot=args.bot,
                    state="recovering",
                    cycles=cycles,
                    trees_harvested=harvested_total,
                    trees_replanted=planted_total,
                    logs_harvested=harvested_total,
                    saplings_planted=planted_total,
                    logs_banked=banked_total,
                    detail=reason,
                )
                time.sleep(max(5.0, min(float(args.poll_seconds), 60.0)))
                continue

            # Bank anything that is not forestry kit. This existed and was
            # called from nowhere, so cargo accumulated in pockets indefinitely:
            # on 2026-08-07 Bot16 was carrying 11 blaze rods, 14 obsidian and 6
            # string -- End-progression material the fleet needed in shared
            # storage -- while its forester reported healthy cycles. Anything a
            # bot holds is also lost on death.
            unloaded = forester.unload_nonforestry_cargo(client, state)
            if unloaded > 0:
                print(f"FORESTER: banked {unloaded} non-forestry stack(s)", flush=True)

            # Saplings are fleet seed stock, not private bot inventory. Bank
            # whole excess stacks through the shared input while retaining a
            # working reserve; other foresters can then restock before they
            # cut. This keeps the one shared supply chain and avoids dedicated
            # per-bot reserve barrels.
            shared_saplings = serialized_deposit(
                client,
                forester.storage,
                timeout=20.0,
                attempts=1,
                deposit_items=set(SAPLING_ITEMS),
                retain_counts={item: 16 for item in SAPLING_ITEMS},
                state=state,
            )
            if shared_saplings > 0:
                print(
                    f"FORESTER: shared {shared_saplings} sapling stack(s)",
                    flush=True,
                )

            # A worker with no saplings refuses every tree it finds, so top up
            # from shared storage before looking. Without this the cycle is a
            # silent no-op that still reports success.
            restocked = forester.restock_saplings(client, state)

            if forester.equip_best_axe(client) is None:
                print("FORESTER: obtaining a survival-crafted forestry axe", flush=True)
                ensure_supplies(client, {"minecraft:iron_axe": 1}, timeout=180)
                if forester.equip_best_axe(client) is None:
                    ensure_supplies(client, {"minecraft:wooden_axe": 1}, timeout=180)
                if forester.equip_best_axe(client) is None:
                    print("FORESTER: forestry axe is still unavailable", flush=True)
                    time.sleep(15.0)
                    continue

            before_harvest = get_inventory(client)
            before_harvest_logs = sum(
                int(before_harvest.get(item, 0) or 0) for item in LOG_BLOCKS
            )
            trees, exploration_cursor = forester.explore_for_trees(
                client,
                exploration_cursor,
            )
            harvest_source = "world"
            grid_planted = 0
            if not trees:
                # Nothing wild to cut. The camp sits in a cleared area -- a live
                # survey on 2026-08-07 found zero logs of *any* species within
                # 96 blocks -- so exploring forever yields nothing. Fall back to
                # the plantation the manifest already describes: harvest it if
                # it has grown, otherwise plant it. `replant_grid` existed and
                # was never called from anywhere, so the grid stayed empty and
                # the workers had no renewable source at all.
                grown = forester.plantation_tree_bases(client)
                if grown:
                    trees = grown
                    harvest_source = "plantation"
                else:
                    grid_planted = forester.replant_grid(client)
            trees = trees[: forester.world_tree_limit]
            trees_harvested = 0
            planted = 0
            skipped_no_sapling = 0
            for position in trees:
                forester.require_safe_server(client)
                ready, detail = forester.state_ready(client)
                if not ready:
                    reason = detail
                    break
                sapling = forester.matching_sapling(client, position)
                if sapling is None:
                    skipped_no_sapling += 1
                    continue
                if forester.break_exact_log(client, position):
                    forester.collect_tree_drops(client, position)
                    if not forester.replant_tree_base(client, position, sapling):
                        raise RuntimeError(
                            f"refusing further harvest after replant failed at {position}"
                        )
                    trees_harvested += 1
                    planted += 1
            after_harvest = get_inventory(client)
            harvested = max(
                0,
                sum(int(after_harvest.get(item, 0) or 0) for item in LOG_BLOCKS)
                - before_harvest_logs,
            )
            banked = 0
            if harvested:
                before = get_inventory(client)
                before_logs = sum(int(before.get(item, 0) or 0) for item in LOG_BLOCKS)
                forester.park(client, STORAGE_DOCK)
                result = serialized_deposit(
                    client,
                    forester.storage,
                    timeout=20.0,
                    attempts=1,
                    deposit_items=set(LOG_BLOCKS),
                    state=state,
                )
                after = get_inventory(client)
                after_logs = sum(int(after.get(item, 0) or 0) for item in LOG_BLOCKS)
                if result >= 0:
                    banked = max(0, before_logs - after_logs)
                forester.require_survival(client)
            cycles += 1
            trees_total += trees_harvested
            harvested_total += harvested
            planted_total += planted
            banked_total += banked
            write_status(
                status_path,
                bot=forester.bot_name,
                state="running",
                cycles=cycles,
                trees_visible=len(trees),
                trees_harvested=trees_total,
                trees_replanted=planted_total,
                logs_harvested=harvested_total,
                saplings_planted=planted_total,
                logs_banked=banked_total,
                # Report what happened, not what was attempted. This said
                # "harvest and exact replant completed" whenever a tree was
                # merely *visible*, so Bot07 logged six cycles of
                # trees_visible=4 / trees_harvested=0 / logs_banked=0 while
                # claiming success -- the failure was invisible for hours.
                detail=_cycle_detail(
                    trees_seen=len(trees),
                    harvested_now=trees_harvested,
                    skipped_no_sapling=skipped_no_sapling,
                    saplings_on_hand=forester.carried_saplings(client),
                    restocked=restocked,
                    source=harvest_source,
                    grid_planted=grid_planted,
                ),
            )
            time.sleep(max(10.0, args.poll_seconds))
    except Exception as error:
        reason = f"error: {type(error).__name__}: {error}"
        return_code = 1
    else:
        return_code = 0
    finally:
        try:
            client.transport.dispatch("cancel", {})
            forester.require_survival(client)
            forester.park(client, forester.home)
        except Exception:
            pass
        write_status(
            status_path,
            bot=forester.bot_name,
            state="stopped",
            cycles=cycles,
            trees_harvested=trees_total,
            trees_replanted=planted_total,
            logs_harvested=harvested_total,
            saplings_planted=planted_total,
            logs_banked=banked_total,
            detail=reason,
        )
        pid_path.unlink(missing_ok=True)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
