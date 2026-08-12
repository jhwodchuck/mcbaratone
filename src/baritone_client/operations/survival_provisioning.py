"""Reusable survival-valid armor and torch provisioning.

The caller owns world-specific landmark discovery.  This module only uses
normal container, furnace, and crafting interactions so it can be shared by
industrial workers and ordinary spawn-to-dragon progression.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

from ..automator.armor_upkeep import equipped_pieces, missing_pieces, run_armor_upkeep
from ..common import harness_ops
from ..common.container_transfer import withdraw_bounded_food
from ..common.emergency_food import (
    EMERGENCY_FOOD_ITEMS,
    craft_emergency_bread_from_carried_wheat,
    emergency_food_count,
)
from ..common.inventory import count_item, equip_best_armor, withdraw_required_from_chest
from ..common.navigation import find_nearby_block
from ..common.resources import _smelt_with_furnace, ensure_supplies


Position = tuple[int, int, int]

# Preserve rare combat food for dragon support. Ordinary and emergency foods
# are interchangeable here because the postcondition is a carried reserve, not
# a particular item ID.
STORAGE_FOOD_ITEMS = tuple(
    item_id
    for item_id in EMERGENCY_FOOD_ITEMS
    if item_id not in {"minecraft:golden_apple", "minecraft:golden_carrot"}
)


def _survival_material_requirements(
    iron_to_smelt: int,
    torch_to_craft: int,
) -> dict[str, int]:
    """Return exact raw inputs for the missing ingots and torch batches."""
    iron_to_smelt = max(0, int(iron_to_smelt))
    torch_to_craft = max(0, int(torch_to_craft))
    requirements = {
        "minecraft:raw_iron": iron_to_smelt,
        "minecraft:coal": int(math.ceil(iron_to_smelt / 8.0))
        + int(math.ceil(torch_to_craft / 4.0)),
        # One stick crafts four torches; requiring one per torch falsely
        # rejects otherwise complete shared-storage supplies.
        "minecraft:stick": int(math.ceil(torch_to_craft / 4.0)),
    }
    return {item_id: count for item_id, count in requirements.items() if count}


def _withdraw_required(
    client: Any,
    sources: Iterable[Position],
    requirements: Mapping[str, int],
) -> dict[str, int]:
    """Withdraw only the outstanding materials through normal chest UI."""
    outstanding = {
        item_id: max(0, int(required) - count_item(client, item_id))
        for item_id, required in requirements.items()
    }
    outstanding = {item_id: count for item_id, count in outstanding.items() if count}
    for chest in sources:
        if not outstanding:
            break
        try:
            harness_ops.move_near(client, *chest, timeout=45.0)
            withdraw_required_from_chest(
                client,
                chest,
                outstanding,
                open_attempts=3,
                allow_recovery_access=True,
            )
        except Exception:
            continue
        outstanding = {
            item_id: max(0, int(required) - count_item(client, item_id))
            for item_id, required in requirements.items()
        }
        outstanding = {item_id: count for item_id, count in outstanding.items() if count}
    return outstanding


def provision_food_reserve(
    client: Any,
    *,
    storage_sources: Sequence[Position],
    minimum_reserve: int = 8,
    crafting_table: Position | None = None,
) -> tuple[bool, str]:
    """Withdraw or craft a verified carried food reserve from shared storage.

    The function accepts any ordinary food used by emergency recovery. If no
    ready-to-eat stack is available, it withdraws wheat and crafts only the
    bread needed to reach ``minimum_reserve``. This keeps the behavior useful
    for expedition preflight, industrial workers, and future campaign loops.
    """
    minimum_reserve = max(1, int(minimum_reserve))
    reserve = emergency_food_count(client)
    if reserve >= minimum_reserve:
        return True, f"food reserve ready ({reserve}/{minimum_reserve})"
    if not storage_sources:
        return False, "no storage source supplied for food provisioning"

    for chest in storage_sources:
        try:
            withdraw_bounded_food(
                client,
                chest,
                STORAGE_FOOD_ITEMS,
                target_total=minimum_reserve,
                open_attempts=3,
                allow_recovery_access=True,
            )
        except Exception:
            continue
        reserve = emergency_food_count(client)
        if reserve >= minimum_reserve:
            return True, f"food reserve withdrawn ({reserve}/{minimum_reserve})"

    reserve = emergency_food_count(client)
    bread_needed = minimum_reserve - reserve
    wheat_target = bread_needed * 3
    for chest in storage_sources:
        try:
            withdraw_bounded_food(
                client,
                chest,
                ("minecraft:wheat",),
                target_total=wheat_target,
                open_attempts=3,
                allow_recovery_access=True,
            )
        except Exception:
            continue
        if count_item(client, "minecraft:wheat") >= wheat_target:
            break
    if count_item(client, "minecraft:wheat") < wheat_target:
        return False, (
            "food storage could not supply a usable reserve "
            f"({reserve}/{minimum_reserve}; wheat shortfall)"
        )
    if crafting_table is not None:
        try:
            table = client.transport.dispatch(
                "get_block",
                {
                    "x": crafting_table[0],
                    "y": crafting_table[1],
                    "z": crafting_table[2],
                },
            ).get("id", "")
        except Exception:
            table = ""
        if table != "minecraft:crafting_table":
            return False, "configured food-provisioning crafting table is missing"
    crafted = craft_emergency_bread_from_carried_wheat(
        client,
        maximum_bread=bread_needed,
        minimum_reserve=minimum_reserve,
    )
    reserve = emergency_food_count(client)
    if not crafted or reserve < minimum_reserve:
        return False, f"bread craft left food reserve at {reserve}/{minimum_reserve}"
    return True, f"food reserve crafted from wheat ({reserve}/{minimum_reserve})"


def provision_survival_kit(
    client: Any,
    *,
    storage_sources: Sequence[Position],
    furnace: Position,
    crafting_table: Position,
    target_pieces: int,
    minimum_torches: int,
) -> tuple[bool, str]:
    """Use saved supplies to make and verify a minimal survival kit."""
    if not storage_sources:
        return False, "no storage source supplied for survival provisioning"

    equip_best_armor(client)
    try:
        live_state = client.transport.dispatch("get_state", {})
        live_state = live_state.get("data", live_state)
        difficulty = str(live_state.get("difficulty", "")).lower()
    except Exception:
        difficulty = ""
    if difficulty == "peaceful":
        return True, "peaceful difficulty; combat-kit provisioning deferred"

    before = equipped_pieces(client)
    needed_pieces = max(0, int(target_pieces) - before)
    costs = [cost for _piece, cost in missing_pieces(client)[:needed_pieces]]
    iron_target = sum(costs)
    torch_missing = max(0, int(minimum_torches) - count_item(client, "minecraft:torch"))

    remaining_torches = _withdraw_required(
        client, storage_sources, {"minecraft:torch": torch_missing}
    )
    remaining_ingots = _withdraw_required(
        client, storage_sources, {"minecraft:iron_ingot": iron_target}
    )
    iron_to_smelt = int(remaining_ingots.get("minecraft:iron_ingot", 0) or 0)
    torch_to_craft = int(remaining_torches.get("minecraft:torch", 0) or 0)
    requirements = _survival_material_requirements(iron_to_smelt, torch_to_craft)
    outstanding = _withdraw_required(client, storage_sources, requirements)
    if outstanding:
        detail = ", ".join(f"{item_id}:{count}" for item_id, count in outstanding.items())
        return False, f"survival kit supplies unavailable ({detail})"

    if iron_to_smelt and not _smelt_with_furnace(
        client,
        "minecraft:iron_ingot",
        count_item(client, "minecraft:iron_ingot") + iron_to_smelt,
        furnace,
    ):
        return False, "could not smelt the withdrawn raw iron"
    if not harness_ops.move_near(client, *crafting_table, timeout=45.0):
        return False, "could not reach assigned crafting table"
    _success, detail, _before, after = run_armor_upkeep(client, None)
    if torch_to_craft and not ensure_supplies(
        client, {"minecraft:torch": int(minimum_torches)}, timeout=120
    ).success:
        return False, "could not craft the required torches"
    if after < target_pieces:
        return False, f"armor remained at {after}/{target_pieces} after provisioning ({detail})"
    if count_item(client, "minecraft:torch") < minimum_torches:
        return False, f"torches remained below {minimum_torches} after provisioning"
    return True, f"survival kit ready ({before}->{after} armor pieces)"


def provision_checkpointed_survival_kit(
    client: Any,
    state: Any,
    *,
    target_pieces: int = 2,
    minimum_torches: int = 16,
) -> tuple[bool, str]:
    """Provision from a run's saved home storage, if a base is established.

    A new spawn has no chest or workstations yet.  That is a normal deferred
    state, not an error and not a reason to fabricate supplies.
    """
    from ..common.inventory import resolve_storage_location

    storage = resolve_storage_location(client, state=state, verify=True)
    if storage is None:
        return True, "survival kit deferred until home storage exists"
    if not harness_ops.move_near(client, *storage, timeout=45.0):
        return False, "could not reach checkpointed home storage"
    crafting_table = find_nearby_block(client, ["minecraft:crafting_table"], radius=12)
    furnace = find_nearby_block(
        client,
        ["minecraft:blast_furnace", "minecraft:furnace"],
        radius=12,
    )
    if crafting_table is None or furnace is None:
        return True, "survival kit deferred until home workstations exist"
    return provision_survival_kit(
        client,
        storage_sources=[storage],
        furnace=tuple(int(axis) for axis in furnace),
        crafting_table=tuple(int(axis) for axis in crafting_table),
        target_pieces=target_pieces,
        minimum_torches=minimum_torches,
    )


__all__ = [
    "STORAGE_FOOD_ITEMS",
    "provision_checkpointed_survival_kit",
    "provision_food_reserve",
    "provision_survival_kit",
]
