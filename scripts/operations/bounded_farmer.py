"""Manifest-driven crop worker for the industrial camp's protected farm."""

from __future__ import annotations

import argparse
import json
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
from baritone_client.common.inventory import get_inventory
from baritone_client.common.navigation import goto, goto_xz
from baritone_client.operations.bridge_guard import NonOpClientGuard, WorkerSafetyError
from baritone_client.operations.camp_armor import provision_camp_armor
from baritone_client.operations.resilient_read_transport import ResilientReadTransport
from baritone_client.operations.shared_storage_ingress import serialized_deposit
from baritone_client.transport.transport import TcpTransport

BlockPosition = tuple[int, int, int]
ESSENTIAL_ITEMS = {
    "minecraft:iron_hoe",
    "minecraft:diamond_hoe",
    "minecraft:netherite_hoe",
    "minecraft:iron_sword",
    "minecraft:diamond_sword",
    "minecraft:netherite_sword",
    "minecraft:shield",
    "minecraft:torch",
    "minecraft:bread",
}

#: Consecutive failed cycles before the worker really gives up. A farm that is
#: briefly unwalkable is not a broken farm.
MAX_CONSECUTIVE_CYCLE_FAILURES = 6

CROP_PLANTING_ITEMS = {
    "minecraft:wheat": "minecraft:wheat_seeds",
    "minecraft:carrots": "minecraft:carrot",
    "minecraft:potatoes": "minecraft:potato",
    "minecraft:beetroots": "minecraft:beetroot_seeds",
}
MATURE_AGES = {
    "minecraft:wheat": "7",
    "minecraft:carrots": "7",
    "minecraft:potatoes": "7",
    "minecraft:beetroots": "3",
}


def unwrap(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    data = value.get("data", value)
    return data if isinstance(data, dict) else {}


class RuntimeState:
    """Minimal storage state required by the shared deposit helper."""

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


class BoundedFarmer:
    """Harvest and replant only the manifest-defined four-crop field."""

    def __init__(self, manifest_path: Path, bot_name: str) -> None:
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        bot = self.manifest["bots"][bot_name]
        farm = self.manifest["farming"]
        self.bot_name = bot_name
        self.bridge_port = int(bot["bridge_port"])
        self.home = tuple(int(v) for v in bot["home"])
        self.storage = tuple(int(v) for v in self.manifest["storage_input"])
        self.provisioning_storage = tuple(
            tuple(int(v) for v in position)
            for position in self.manifest.get("provisioning_storage", [self.storage])
        )
        self.furnace = tuple(int(v) for v in bot["furnace"])
        self.crafting_table = tuple(int(v) for v in bot["crafting_table"])
        self.center = tuple(int(v) for v in farm["center"])
        self.crop_y = self.center[1] - 1
        self.staging = tuple(int(v) for v in farm["staging"])
        self.entrance_gates = tuple(
            tuple(int(v) for v in point) for point in farm["entrance_gates"]
        )
        self.route_from_housing = tuple(
            tuple(int(v) for v in point) for point in farm["route_from_housing"]
        )
        self.route_to_storage = tuple(
            tuple(int(v) for v in point) for point in farm["route_to_storage"]
        )
        self.protected_box = tuple(int(v) for v in farm["protected_box"])
        self.farm_range = int(farm["range"])
        self.crops_per_cycle = int(farm["crops_per_cycle"])
        self.minimum_replant_ratio = float(farm["minimum_replant_ratio"])
        self.crop_blocks = tuple(str(v) for v in farm["crop_blocks"])
        self.produce = tuple(str(v) for v in farm["produce"])
        self.planting_items = tuple(str(v) for v in farm["planting_items"])
        self.protected_players = tuple(self.manifest.get("protected_players", ()))
        self.crop_scan_cursor = 0
        expedition_safety = self.manifest.get("surface_expedition_safety", {})
        self.minimum_armor_points = int(
            expedition_safety.get("minimum_armor_points", 0)
        )
        self.minimum_armor_pieces = int(
            expedition_safety.get("minimum_armor_pieces", 0)
        )
        self.minimum_torches = int(expedition_safety.get("minimum_torches", 0))
        min_x, _min_y, min_z, max_x, _max_y, max_z = self.protected_box
        x, _y, z = self.center
        if not (min_x <= x <= max_x and min_z <= z <= max_z):
            raise ValueError("farm center is outside the protected farm")

    def guard(self, client: Any) -> NonOpClientGuard:
        return NonOpClientGuard(client, self.bot_name, self.protected_players)

    def require_safe_server(self, client: Any) -> None:
        self.guard(client).require_safe()

    def require_survival(self, client: Any) -> None:
        self.guard(client).require_survival()

    def park(self, client: Any, position: BlockPosition) -> bool:
        """Walk to a camp position without privileged repositioning.

        This used a privileged server reposition. A survival farmer must walk
        to its own field, and the free reposition also masked failures --
        an unwalkable staging point still reported success.
        """
        x, y, z = position
        try:
            return bool(goto(client, x, y, z, timeout=120, tolerance=3.0))
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
                    "farm route blocked: need armor "
                    f"({armor_points}/{self.minimum_armor_points} points, "
                    f"{armor_pieces}/{self.minimum_armor_pieces} pieces)"
                )
            if torches < self.minimum_torches:
                return False, (
                    "farm route blocked: need torches "
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

    def set_entrance_gate(self, client: Any, open_: bool) -> bool:
        """Open or close the farm gates by pushing them, like a player.

        These were previously rewritten by a privileged server command. That
        also silently replaced whatever was actually there with a north-facing
        oak gate, so a gate the operator had built facing another way could be
        quietly reoriented.

        A fence gate toggles on interact, so this reads the current state and
        only pushes the ones that need to change.
        """
        self.require_safe_server(client)
        changed = True
        for x, y, z in self.entrance_gates:
            live = unwrap(
                client.transport.dispatch("get_block", {"x": x, "y": y, "z": z})
            )
            block_id = str(live.get("id", ""))
            if "fence_gate" not in block_id:
                print(
                    f"GATE: {(x, y, z)} is {block_id!r}, not a fence gate", flush=True
                )
                changed = False
                continue
            already = bool(live.get("properties", {}).get("open") in (True, "true"))
            if already == open_:
                continue
            try:
                client.transport.dispatch("interact_block", {"x": x, "y": y, "z": z})
            except Exception as error:
                print(
                    f"GATE: could not operate gate at {(x, y, z)} ({error})", flush=True
                )
                changed = False
        return changed

    def crop_count(self, client: Any) -> int:
        return len(self.crop_positions(client))

    def crop_positions(self, client: Any) -> list[tuple[int, int, int, str]]:
        """Find protected crop blocks in one bounded bridge search."""
        min_x, _min_y, min_z, max_x, _max_y, max_z = self.protected_box
        result = unwrap(
            client.transport.dispatch(
                "find_blocks",
                {
                    "blocks": list(self.crop_blocks),
                    "radius": self.farm_range,
                    "limit": 1024,
                },
            )
        )
        discovered = result.get("found", result.get("blocks", []))
        positions = []
        for block in discovered:
            if not isinstance(block, dict):
                continue
            try:
                x, y, z = int(block["x"]), int(block["y"]), int(block["z"])
            except (KeyError, TypeError, ValueError):
                continue
            crop = str(block.get("block", block.get("id", "")))
            if (
                crop in self.crop_blocks
                and min_x <= x <= max_x
                and min_z <= z <= max_z
                and y == self.crop_y
            ):
                positions.append((x, y, z, crop))
        return sorted(positions)

    def mature_crops(self, client: Any) -> list[tuple[int, int, int, str]]:
        mature: list[tuple[int, int, int, str]] = []
        positions = self.crop_positions(client)
        if not positions:
            return mature
        scan_budget = min(len(positions), max(32, self.crops_per_cycle * 4))
        start = self.crop_scan_cursor % len(positions)
        self.crop_scan_cursor = (start + scan_budget) % len(positions)
        for offset in range(scan_budget):
            x, y, z, _crop = positions[(start + offset) % len(positions)]
            live = unwrap(
                client.transport.dispatch("get_block", {"x": x, "y": y, "z": z})
            )
            crop = str(live.get("id", ""))
            age = str(live.get("properties", {}).get("age", ""))
            if crop in MATURE_AGES and age == MATURE_AGES[crop]:
                mature.append((x, y, z, crop))
            if len(mature) >= self.crops_per_cycle:
                return mature
        return mature

    def collect_crop_drops(self, client: Any, position: BlockPosition) -> None:
        time.sleep(0.75)
        try:
            result = unwrap(client.transport.dispatch("get_entities", {"radius": 12}))
        except Exception:
            return
        x0, y0, z0 = position
        for entity in result.get("entities", []):
            if not isinstance(entity, dict) or entity.get("type") != "minecraft:item":
                continue
            point = entity.get("position", {})
            if not isinstance(point, dict):
                continue
            x = float(point.get("x", 9999))
            y = float(point.get("y", 9999))
            z = float(point.get("z", 9999))
            if (x - x0) ** 2 + (y - y0) ** 2 + (z - z0) ** 2 > 16:
                continue
            goto(
                client,
                int(round(x)),
                int(round(y)),
                int(round(z)),
                timeout=12,
                tolerance=1.25,
                check_interval=0.5,
            )
            time.sleep(0.4)

    def harvest_exact_crop(
        self,
        client: Any,
        position: BlockPosition,
        crop: str,
    ) -> bool:
        planting_item = CROP_PLANTING_ITEMS[crop]
        if int(get_inventory(client).get(planting_item, 0) or 0) < 1:
            return False
        x, y, z = position
        client.transport.dispatch("break_block", {"x": x, "y": y, "z": z})
        deadline = time.monotonic() + 45.0
        while time.monotonic() < deadline:
            live = unwrap(
                client.transport.dispatch("get_block", {"x": x, "y": y, "z": z})
            )
            if str(live.get("id", "")) in ("minecraft:air", "minecraft:cave_air"):
                break
            time.sleep(0.75)
        else:
            return False
        # Plant it through the client. The old implementation deleted a seed
        # and rewrote the crop through privileged server operations. It looked
        # resource-neutral, but every verification only checked the server's
        # own write. This check reads the world after a real placement.
        self.require_safe_server(client)
        self.require_survival(client)
        try:
            planted = bool(robust_place(client, x, y, z, planting_item))
        finally:
            self.require_survival(client)
        if not planted:
            return False

        live = unwrap(client.transport.dispatch("get_block", {"x": x, "y": y, "z": z}))
        if str(live.get("id", "")) != crop:
            raise RuntimeError(
                f"replant verification failed at {(x, y, z)}: "
                f"expected {crop}, found {live.get('id')!r}"
            )
        return True

    def plant_empty_farmland(self, client: Any) -> int:
        """Plant carried crop items into verified empty tilled cells."""
        min_x, _min_y, min_z, max_x, _max_y, max_z = self.protected_box
        result = unwrap(
            client.transport.dispatch(
                "find_blocks",
                {
                    "blocks": ["minecraft:farmland"],
                    "radius": self.farm_range,
                    "limit": 1024,
                },
            )
        )
        discovered = result.get("found", result.get("blocks", []))
        occupied = {(x, z) for x, _y, z, _crop in self.crop_positions(client)}
        farmland = []
        for block in discovered:
            if not isinstance(block, dict):
                continue
            try:
                x, y, z = int(block["x"]), int(block["y"]), int(block["z"])
            except (KeyError, TypeError, ValueError):
                continue
            if (
                min_x <= x <= max_x
                and min_z <= z <= max_z
                and y == self.crop_y - 1
                and (x, z) not in occupied
            ):
                farmland.append((x, z))
        planted = 0
        for x, z in sorted(farmland):
            if planted >= self.crops_per_cycle:
                return planted
            inventory = get_inventory(client)
            planting = next(
                (
                    (crop, item)
                    for crop, item in CROP_PLANTING_ITEMS.items()
                    if int(inventory.get(item, 0) or 0) > 0
                ),
                None,
            )
            if planting is None:
                return planted
            crop, item = planting
            self.require_safe_server(client)
            self.require_survival(client)
            try:
                placed = bool(robust_place(client, x, self.crop_y, z, item))
            finally:
                self.require_survival(client)
            if not placed:
                continue
            live = unwrap(
                client.transport.dispatch(
                    "get_block", {"x": x, "y": self.crop_y, "z": z}
                )
            )
            if str(live.get("id", "")) == crop:
                planted += 1
        return planted

    def unload_nonfarm_cargo(self, client: Any, state: RuntimeState) -> int:
        inventory = get_inventory(client)
        keep = ESSENTIAL_ITEMS | set(self.produce) | set(self.planting_items)
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

    def harvest_cycle(self, client: Any) -> tuple[int, int, int]:
        self.require_safe_server(client)
        self.park(client, self.staging)
        self.set_entrance_gate(client, True)
        if not goto_xz(
            client,
            self.center[0],
            self.center[2],
            timeout=120,
            tolerance=4.0,
        ):
            raise RuntimeError("crop farm is unreachable")
        self.set_entrance_gate(client, False)
        before_crops = self.crop_count(client)
        before_inventory = get_inventory(client)
        before_produce = sum(int(before_inventory.get(v, 0) or 0) for v in self.produce)
        self.require_survival(client)
        try:
            harvested_tiles = 0
            for x, y, z, crop in self.mature_crops(client):
                self.require_safe_server(client)
                if self.harvest_exact_crop(client, (x, y, z), crop):
                    harvested_tiles += 1
            self.plant_empty_farmland(client)
        finally:
            try:
                client.transport.dispatch("cancel", {})
            except Exception:
                pass
            finally:
                self.require_survival(client)
        after_crops = self.crop_count(client)
        required = int(before_crops * self.minimum_replant_ratio)
        if before_crops and after_crops < required:
            raise RuntimeError(
                f"replant verification failed: {after_crops}/{before_crops} crops remain"
            )
        after_inventory = get_inventory(client)
        after_produce = sum(int(after_inventory.get(v, 0) or 0) for v in self.produce)
        return (
            max(harvested_tiles, after_produce - before_produce),
            before_crops,
            after_crops,
        )

    def bank_surplus(self, client: Any, state: RuntimeState) -> int:
        self.set_entrance_gate(client, True)
        self.park(client, self.storage)
        self.set_entrance_gate(client, False)
        inventory = get_inventory(client)
        before = sum(
            int(inventory.get(v, 0) or 0)
            for v in set(self.produce) | set(self.planting_items)
        )
        moved = serialized_deposit(
            client,
            self.storage,
            timeout=20.0,
            attempts=1,
            deposit_items=set(self.produce) | set(self.planting_items),
            state=state,
            retain_counts={item: 16 for item in self.planting_items},
        )
        after_inventory = get_inventory(client)
        after = sum(
            int(after_inventory.get(v, 0) or 0)
            for v in set(self.produce) | set(self.planting_items)
        )
        return max(0, before - after) if moved >= 0 else 0


def write_status(path: Path, **values: Any) -> None:
    values["timestamp"] = time.time()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(values, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest", type=Path, default=REPO / "industrial_camp_manifest.json"
    )
    parser.add_argument("--bot", default="Bot17")
    parser.add_argument("--hours", type=float, default=8.0)
    parser.add_argument("--poll-seconds", type=float, default=120.0)
    args = parser.parse_args()

    farmer = BoundedFarmer(args.manifest, args.bot)
    run_dir = Path.cwd()
    status_path = run_dir / "bounded_farmer_status.json"
    stop_path = run_dir / "bounded_farmer.stop"
    pid_path = run_dir / "bounded_farmer.pid"
    stop_path.unlink(missing_ok=True)
    pid_path.write_text(str(os.getpid()), encoding="ascii")
    tcp_transport = TcpTransport("localhost", farmer.bridge_port, timeout=5.0)
    # The bridge stalls on get_state periodically. ResilientReadTransport
    # exists to ride that out -- it retries only on
    # "Timeout waiting for bridge response" with escalating backoff -- but
    # every worker pinned it to attempts=1, disabling it. On 2026-08-07 all
    # three foresters were stopped simultaneously on that one error, five
    # hours after the last log was banked. Reads are idempotent; retrying
    # them is free.
    tcp_transport._READ_RETRY_ATTEMPTS = 1
    client = SimpleNamespace(
        transport=ResilientReadTransport(tcp_transport, attempts=4)
    )
    state = RuntimeState(farmer.home, farmer.storage)
    started = time.time()
    cycles = harvested_total = banked_total = 0
    reason = "time limit reached"
    return_code = 0
    try:
        farmer.require_safe_server(client)
        armored, armor_detail = farmer.provision_safety_kit(client)
        if not armored:
            reason = armor_detail
            return 2
        ready, detail = farmer.state_ready(client)
        if not ready:
            reason = detail
            return 2
        farmer.require_survival(client)
        write_status(
            status_path,
            bot=farmer.bot_name,
            state="starting",
            cycles=0,
            detail="unloading non-farming cargo",
        )
        deadline = started + max(60.0, args.hours * 3600.0)
        consecutive_failures = 0
        while time.time() < deadline and not stop_path.exists():
            ready, detail = farmer.state_ready(client)
            if not ready:
                reason = detail
                break
            # One bad cycle must not end an eight-hour shift. Every failure
            # here used to propagate to the top-level handler and exit the
            # worker: after privileged repositioning was removed, the walks to staging and
            # to the field became long enough for the bridge's periodic
            # get_state stall to land inside one, and Bot17 restarted 16 times
            # without ever completing a harvest. The cycle is retried instead.
            try:
                harvested, before_crops, after_crops = farmer.harvest_cycle(client)
                banked = farmer.bank_surplus(client, state) if harvested else 0
            except WorkerSafetyError:
                raise
            except Exception as cycle_error:
                consecutive_failures += 1
                write_status(
                    status_path,
                    bot=farmer.bot_name,
                    state="recovering",
                    cycles=cycles,
                    harvested=harvested_total,
                    banked=banked_total,
                    detail=(
                        f"cycle failed ({type(cycle_error).__name__}: "
                        f"{cycle_error}); retry {consecutive_failures}"
                        f"/{MAX_CONSECUTIVE_CYCLE_FAILURES}"
                    ),
                )
                if consecutive_failures >= MAX_CONSECUTIVE_CYCLE_FAILURES:
                    reason = f"error: {type(cycle_error).__name__}: {cycle_error}"
                    return_code = 1
                    break
                try:
                    client.transport.dispatch("cancel", {})
                except Exception:
                    pass
                time.sleep(15.0)
                continue
            consecutive_failures = 0
            farmer.park(client, farmer.staging)
            cycles += 1
            harvested_total += harvested
            banked_total += banked
            write_status(
                status_path,
                bot=farmer.bot_name,
                state="running",
                cycles=cycles,
                harvested=harvested_total,
                banked=banked_total,
                crops_before=before_crops,
                crops_after=after_crops,
                detail="bounded harvest, replant verification, and warehouse deposit completed",
            )
            time.sleep(max(30.0, args.poll_seconds))
    except Exception as error:
        reason = f"error: {type(error).__name__}: {error}"
        return_code = 1
    finally:
        try:
            client.transport.dispatch("cancel", {})
            farmer.require_survival(client)
            farmer.set_entrance_gate(client, False)
            farmer.park(client, farmer.staging if return_code == 0 else farmer.home)
        except Exception:
            pass
        if (
            return_code
            or stop_path.exists()
            or time.time() >= started + args.hours * 3600.0
        ):
            write_status(
                status_path,
                bot=farmer.bot_name,
                state="stopped",
                cycles=cycles,
                harvested=harvested_total,
                banked=banked_total,
                detail=reason,
            )
        pid_path.unlink(missing_ok=True)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
