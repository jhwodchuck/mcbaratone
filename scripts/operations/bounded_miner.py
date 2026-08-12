"""Run one industrial-camp miner in bounded, recoverable ore-search cycles."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

from baritone_client.common.inventory import (
    get_inventory,
    select_item,
    withdraw_required_from_chest,
)
from baritone_client.common.navigation import goto
from baritone_client.operations.bridge_guard import NonOpClientGuard, WorkerSafetyError
from baritone_client.operations.resilient_read_transport import ResilientReadTransport
from baritone_client.operations.shared_storage_ingress import serialized_deposit
from baritone_client.transport.transport import TcpTransport

PICKAXES = (
    "minecraft:netherite_pickaxe",
    "minecraft:diamond_pickaxe",
    "minecraft:iron_pickaxe",
    "minecraft:stone_pickaxe",
    "minecraft:wooden_pickaxe",
)
KEEP_ITEMS = {
    *PICKAXES,
    "minecraft:torch",
    "minecraft:shield",
    "minecraft:bread",
    "minecraft:baked_potato",
    "minecraft:cooked_beef",
    "minecraft:cooked_chicken",
    "minecraft:cooked_mutton",
    "minecraft:cooked_porkchop",
    "minecraft:potato",
    "minecraft:carrot",
    "minecraft:beetroot",
    "minecraft:apple",
    "minecraft:iron_helmet",
    "minecraft:iron_chestplate",
    "minecraft:iron_leggings",
    "minecraft:iron_boots",
}
ORE_BLOCKS = (
    "minecraft:coal_ore",
    "minecraft:deepslate_coal_ore",
    "minecraft:copper_ore",
    "minecraft:deepslate_copper_ore",
    "minecraft:iron_ore",
    "minecraft:deepslate_iron_ore",
    "minecraft:gold_ore",
    "minecraft:deepslate_gold_ore",
    "minecraft:redstone_ore",
    "minecraft:deepslate_redstone_ore",
    "minecraft:lapis_ore",
    "minecraft:deepslate_lapis_ore",
    "minecraft:diamond_ore",
    "minecraft:deepslate_diamond_ore",
)


def write_status(path: Path, **values: Any) -> None:
    values["timestamp"] = time.time()
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(values, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def position(state: dict[str, Any]) -> tuple[float, float, float]:
    value = state.get("block_position") or state.get("position") or {}
    return float(value["x"]), float(value["y"]), float(value["z"])


def inside_box(point: tuple[float, float, float], box: list[int]) -> bool:
    x, y, z = point
    return box[0] <= x <= box[3] and box[1] <= y <= box[4] and box[2] <= z <= box[5]


def equip_pickaxe(client: Any) -> str | None:
    for item_id in PICKAXES:
        if select_item(client, item_id, allow_swap=True):
            return item_id
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bot", default="Bot15")
    parser.add_argument("--hours", type=float, default=8.0)
    parser.add_argument("--slice-seconds", type=int, default=180)
    parser.add_argument("--quantity", type=int, default=24)
    args = parser.parse_args()

    manifest = json.loads((REPO / "industrial_camp_manifest.json").read_text())
    bot = manifest["bots"][args.bot]
    mining = manifest["mining"]
    landing = tuple(int(value) for value in mining["landing"])
    descent = [
        tuple(int(value) for value in point) for point in mining["descent_waypoints"]
    ]
    entry = tuple(int(value) for value in manifest["mine_entry"])
    storage = tuple(int(value) for value in manifest["storage_input"])
    storage_overflow = [
        tuple(int(value) for value in point)
        for point in manifest.get("storage_overflow", [])
    ]
    storage_targets = [storage, *storage_overflow]
    storage_staging = (storage[0] - 1, storage[1] - 1, storage[2] - 1)
    protected = [list(map(int, box)) for box in mining["protected_boxes"]]
    max_radius = float(mining["max_distance_from_landing"])
    protected_players = tuple(manifest["protected_players"])
    expected_dimension = str(manifest["dimension"]).strip()

    run_dir = REPO / bot["worker"]["working_directory"]
    run_dir.mkdir(parents=True, exist_ok=True)
    status_path = run_dir / "bounded_miner_status.json"
    stop_path = run_dir / "bounded_miner.stop"
    pid_path = run_dir / "bounded_miner.pid"
    stop_path.unlink(missing_ok=True)
    pid_path.write_text(str(os.getpid()), encoding="ascii")

    tcp = TcpTransport("localhost", int(bot["bridge_port"]), timeout=5.0)
    # The bridge stalls on get_state periodically. ResilientReadTransport
    # exists to ride that out -- it retries only on
    # "Timeout waiting for bridge response" with escalating backoff -- but
    # every worker pinned it to attempts=1, disabling it. On 2026-08-07 all
    # three foresters were stopped simultaneously on that one error, five
    # hours after the last log was banked. Reads are idempotent; retrying
    # them is free.
    tcp._READ_RETRY_ATTEMPTS = 1
    client = SimpleNamespace(transport=ResilientReadTransport(tcp, attempts=4))
    cycles = banked_stacks = 0
    detail = "starting"

    with NonOpClientGuard(
        client,
        args.bot,
        protected_players,
        expected_dimension=expected_dimension,
    ) as guard:

        def require_safe() -> None:
            guard.require_safe()

        def require_survival() -> None:
            guard.require_survival()

        def report(state_name: str, message: str) -> None:
            write_status(
                status_path,
                bot=args.bot,
                role="miner",
                state=state_name,
                cycles=cycles,
                banked_stacks=banked_stacks,
                detail=message,
            )

        def live_position() -> tuple[float, float, float]:
            return guard.position()

        def walk_route(points: Any) -> bool:
            route = list(points)
            if not route:
                return True
            current = live_position()
            start_index = min(
                range(len(route)),
                key=lambda index: math.dist(current, route[index]),
            )
            for point in route[start_index:]:
                reached = goto(client, *point, timeout=120, tolerance=4.0)
                if math.dist(live_position(), point) <= 6.0:
                    continue
                if not reached:
                    return False
            return True

        def withdraw_pickaxe_supplies() -> int:
            """Visit the public input and withdraw a tool or craft materials."""
            moved = withdraw_required_from_chest(
                client,
                storage,
                {"minecraft:stone_pickaxe": 1},
                open_attempts=3,
                allow_recovery_access=True,
            )
            if equip_pickaxe(client) not in (None, "minecraft:wooden_pickaxe"):
                return max(0, moved)
            materials = withdraw_required_from_chest(
                client,
                storage,
                {
                    "minecraft:iron_ingot": 3,
                    "minecraft:cobblestone": 3,
                    "minecraft:cobbled_deepslate": 3,
                    "minecraft:stick": 2,
                },
                open_attempts=3,
                allow_recovery_access=True,
            )
            return max(0, moved) + max(0, materials)

        def bank_cargo(stage: str) -> tuple[bool, int]:
            """Deposit cargo through primary intake, then approved overflow."""
            moved_total = 0
            for target in storage_targets:
                inventory = get_inventory(client)
                deposit_items = set(inventory) - KEEP_ITEMS
                if not deposit_items:
                    return True, moved_total
                report(stage, f"banking carried cargo at {target}")
                moved = serialized_deposit(
                    client,
                    target,
                    timeout=20.0,
                    attempts=1,
                    deposit_items=deposit_items,
                    keep_items=KEEP_ITEMS,
                    state=None,
                )
                moved_total += max(0, moved)
            remaining = set(get_inventory(client)) - KEEP_ITEMS
            return not remaining, moved_total

        def preflight_bank_cargo() -> tuple[bool, int]:
            """Bank non-survival cargo before any route can enter the mine.

            A miner may restart at a bed carrying a full ore run.  Descending
            first turns that recovery state into another loss risk, and also
            makes the shared inventory appear empty while valuable resources
            remain stranded on the worker.  The public input is therefore a
            mandatory first stop whenever bankable cargo is present.
            """
            inventory = get_inventory(client)
            deposit_items = set(inventory) - KEEP_ITEMS
            if not deposit_items:
                return True, 0
            report("returning", "preflight: banking carried cargo before mining")
            require_survival()
            return bank_cargo("returning")

        started = time.time()
        try:
            require_safe()
            client.transport.dispatch("cancel", {})
            require_survival()
            report("running", "routing to mid-shaft mining landing")

            while time.time() - started < max(60.0, args.hours * 3600.0):
                if stop_path.exists():
                    detail = "stop requested"
                    break
                require_safe()
                banked, moved = preflight_bank_cargo()
                if moved > 0:
                    banked_stacks += moved
                if not banked:
                    detail = "could not bank carried cargo before mining"
                    report("recovering", detail)
                    time.sleep(15.0)
                    continue
                if float(guard.require_safe().get("health", 0.0) or 0.0) < 16.0:
                    detail = "cargo is safe; holding at storage until health recovers"
                    report("recovering", detail)
                    time.sleep(15.0)
                    continue
                if not walk_route(descent):
                    detail = "could not reach mid-shaft mining landing"
                    report("recovering", detail)
                    client.transport.dispatch("cancel", {})
                    require_survival()
                    # A privileged reposition after a failed walk hides the
                    # failure -- the
                    # descent never gets fixed because it always "recovers".
                    # Walk back to the entry instead and let a genuinely
                    # unreachable shaft stay visible.
                    goto(client, *entry, timeout=120, tolerance=4.0)
                    time.sleep(10)
                    continue

                if equip_pickaxe(client) in (None, "minecraft:wooden_pickaxe"):
                    # Earn the tool rather than being handed one. Return through
                    # the established shaft, use the public warehouse input, and
                    # only craft locally when storage cannot supply a finished
                    # pickaxe. This deliberately refuses privileged item
                    # injection.
                    report(
                        "recovering",
                        "no pickaxe; sourcing one from storage or crafting",
                    )
                    try:
                        from baritone_client.common.resources import ensure_supplies

                        require_survival()
                        if not walk_route(reversed(descent[:-1])):
                            raise RuntimeError("could not walk from mine to entry")
                        if not goto(
                            client,
                            *storage_staging,
                            timeout=180,
                            tolerance=3.0,
                        ):
                            raise RuntimeError("could not reach public storage input")
                        withdraw_pickaxe_supplies()
                        if not goto(client, *entry, timeout=180, tolerance=4.0):
                            raise RuntimeError(
                                "could not return from storage to mine entry"
                            )
                        if not walk_route(descent):
                            raise RuntimeError("could not return down the mine route")
                        require_survival()
                        if equip_pickaxe(client) not in (
                            None,
                            "minecraft:wooden_pickaxe",
                        ):
                            requested_pickaxe = None
                        else:
                            requested_pickaxe = ""
                        carried = get_inventory(client)
                        if requested_pickaxe is None:
                            pass
                        elif (
                            int(carried.get("minecraft:diamond", 0) or 0) >= 3
                            and int(carried.get("minecraft:stick", 0) or 0) >= 2
                        ):
                            requested_pickaxe = "minecraft:diamond_pickaxe"
                        elif (
                            int(carried.get("minecraft:iron_ingot", 0) or 0) >= 3
                            and int(carried.get("minecraft:stick", 0) or 0) >= 2
                        ):
                            requested_pickaxe = "minecraft:iron_pickaxe"
                        elif (
                            int(carried.get("minecraft:cobblestone", 0) or 0)
                            + int(carried.get("minecraft:cobbled_deepslate", 0) or 0)
                            >= 3
                        ):
                            requested_pickaxe = "minecraft:stone_pickaxe"
                        else:
                            requested_pickaxe = "minecraft:wooden_pickaxe"
                        if requested_pickaxe:
                            ensure_supplies(client, {requested_pickaxe: 1}, timeout=180)
                    except WorkerSafetyError:
                        raise
                    except Exception as error:
                        print(f"MINER: pickaxe sourcing failed ({error})", flush=True)
                    if equip_pickaxe(client) is None:
                        detail = (
                            "no usable pickaxe and none could be crafted or "
                            "withdrawn"
                        )
                        report("stopped", detail)
                        return 2

                equipped = equip_pickaxe(client)
                if equipped == "minecraft:wooden_pickaxe":
                    report(
                        "recovering",
                        "wooden-pick bootstrap: mining stone for a stone pickaxe",
                    )
                    client.transport.dispatch(
                        "mine",
                        {
                            "blocks": ["minecraft:stone", "minecraft:deepslate"],
                            "quantity": 3,
                        },
                    )
                    bootstrap_deadline = time.time() + 90.0
                    while time.time() < bootstrap_deadline:
                        carried = get_inventory(client)
                        stone_material = int(
                            carried.get("minecraft:cobblestone", 0) or 0
                        ) + int(carried.get("minecraft:cobbled_deepslate", 0) or 0)
                        if stone_material >= 3:
                            break
                        time.sleep(5.0)
                    client.transport.dispatch("cancel", {})
                    if stone_material >= 3:
                        ensure_supplies(
                            client, {"minecraft:stone_pickaxe": 1}, timeout=180
                        )
                        equipped = equip_pickaxe(client)
                    if equipped == "minecraft:wooden_pickaxe":
                        detail = "wooden-pick bootstrap could not obtain stone"
                        report("recovering", detail)
                        time.sleep(15.0)
                        continue

                require_survival()
                report(
                    "mining", "running bounded ore-search slice from starter branches"
                )
                client.transport.dispatch(
                    "mine",
                    {"blocks": list(ORE_BLOCKS), "quantity": max(1, args.quantity)},
                )
                deadline = time.time() + max(30, args.slice_seconds)
                idle_polls = 0
                while time.time() < deadline and not stop_path.exists():
                    require_safe()
                    state = guard.require_safe()
                    point = position(state)
                    health = float(state.get("health", 20.0) or 0.0)
                    horizontal = math.dist(
                        (point[0], point[2]), (landing[0], landing[2])
                    )
                    if health < 12.0:
                        detail = f"survival abort at health {health:.1f}"
                        break
                    if horizontal > max_radius:
                        detail = f"radius abort at {horizontal:.1f} blocks"
                        break
                    if any(inside_box(point, box) for box in protected):
                        detail = "protected-box abort"
                        break
                    idle_polls = 0 if state.get("is_pathing") else idle_polls + 1
                    if idle_polls >= 3:
                        detail = "ore-search slice completed"
                        break
                    time.sleep(5)
                client.transport.dispatch("cancel", {})
                require_survival()

                report("returning", detail or "returning ore cargo to shared input")
                if not goto(client, *landing, timeout=120, tolerance=4.0):
                    # No privileged reposition fallback: a cargo run that cannot walk back
                    # is a real problem worth seeing, not one to paper over.
                    client.transport.dispatch("cancel", {})
                    report("recovering", "could not walk back to the mining landing")
                if not walk_route(reversed(descent[:-1])):
                    client.transport.dispatch("cancel", {})
                    report(
                        "recovering",
                        "could not walk the descent route back to the entry",
                    )
                require_survival()
                _banked, moved = bank_cargo("returning")
                if moved > 0:
                    banked_stacks += moved
                cycles += 1
                detail = "ore slice returned and shared-storage deposit attempted"
                report("running", detail)
                time.sleep(10)
        except Exception as error:
            detail = f"error: {type(error).__name__}: {error}"
            report("stopped", detail)
            return 1
        finally:
            try:
                client.transport.dispatch("cancel", {})
                require_survival()
            except Exception:
                pass
            write_status(
                status_path,
                bot=args.bot,
                role="miner",
                state="stopped",
                cycles=cycles,
                banked_stacks=banked_stacks,
                detail=detail,
            )
            pid_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
