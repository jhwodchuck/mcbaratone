"""Durable storage and provisioning support for FOOD_AND_IRON."""

from __future__ import annotations

from typing import TYPE_CHECKING, Tuple

from ..phase_verifier_support import FOOD_ITEMS
from ...common import harness_ops
from ...common.farming import harvest_wheat_farm
from ...common.inventory import (
    _ensure_raw_planks,
    count_item,
    craft,
    withdraw_required_from_chest,
)
from ...common.resources import ensure_supplies, gather_wood
from ...common.tasks import IncrementalProgressRequired, PacingHoldRequired
from .iron_age_food import persisted_food_source

if TYPE_CHECKING:
    from ..state_manager import StateManager
    from .iron_age import FoodAndIronHandler


WOOD_TYPES = (
    "oak",
    "birch",
    "spruce",
    "dark_oak",
    "acacia",
    "jungle",
    "mangrove",
    "cherry",
    "pale_oak",
)


def withdraw_banked_iron(
    handler: "FoodAndIronHandler",
    client,
    state: "StateManager",
    chest_pos: Tuple[int, int, int],
) -> bool:
    """Restore a missing supply chest and retry the required withdrawal."""
    required = {
        "minecraft:iron_ingot": handler._IRON_BANK_TARGET,
        "minecraft:raw_iron": handler._IRON_BANK_TARGET,
    }

    def withdraw(position: Tuple[int, int, int]) -> bool:
        return withdraw_required_from_chest(
            client,
            position,
            required,
            state=state,
        ) >= 0

    if withdraw(chest_pos):
        return True
    if handler._reestablish_supply_chest(client, chest_pos):
        return withdraw(chest_pos)

    if count_item(client, "minecraft:chest") < 1:
        total_planks = sum(
            count_item(client, f"minecraft:{wood}_planks")
            for wood in WOOD_TYPES
        )
        if total_planks < 8:
            if not gather_wood(client, count=3, timeout=180):
                print("  Could not gather wood to craft a replacement chest.")
                return False
            if not _ensure_raw_planks(client, 8):
                print("  Could not produce 8 planks for a replacement chest.")
                return False
        if handler._reestablish_supply_chest(client, chest_pos):
            return withdraw(chest_pos)

    house = state.custom_data.get("structures", {}).get("starter_house", {})
    alternate = handler._normalize_position(house.get("supply_chest"))
    if (
        alternate is not None
        and alternate != chest_pos
        and handler._reestablish_supply_chest(client, alternate)
    ):
        return withdraw(alternate)

    print("  Withdraw banked iron failed: no recoverable supply chest.")
    return False


def reestablish_supply_chest(
    handler: "FoodAndIronHandler",
    client,
    chest_pos: Tuple[int, int, int],
) -> bool:
    """Place and verify a missing checkpointed supply chest."""
    cx, cy, cz = (int(value) for value in chest_pos)

    def block_id() -> str:
        return str(
            client.transport.dispatch(
                "get_block",
                {"x": cx, "y": cy, "z": cz},
            ).get("id", "")
        )

    if "chest" in block_id():
        return True

    snapshot = handler._read_state(client, "Reestablish supply chest distance check")
    if snapshot is None:
        return False
    position = snapshot.get("block_position", snapshot.get("position", {}))
    near = all(axis in position for axis in ("x", "y", "z")) and (
        (float(position["x"]) - cx) ** 2
        + (float(position["y"]) - cy) ** 2
        + (float(position["z"]) - cz) ** 2
    ) ** 0.5 <= 4.5
    if not near and not harness_ops.move_near(client, cx, cy, cz, timeout=30.0):
        print("  Could not reach supply-chest location to re-place it.")
        return False

    current_block = block_id()
    if "chest" in current_block:
        return True
    if current_block == "minecraft:void_air":
        return False
    if count_item(client, "minecraft:chest") < 1 and not craft(
        client,
        "minecraft:chest",
        1,
    ):
        print("  No chest available and could not craft one to re-place.")
        return False

    print(f"  Re-placing missing supply chest at {(cx, cy, cz)}.")
    client.transport.dispatch("chat", {"message": "#set allowBreak false"})
    try:
        harness_ops.place_block_exact(
            client,
            cx,
            cy,
            cz,
            "minecraft:chest",
            allow_break=False,
        )
    finally:
        client.transport.dispatch("cancel", {})
        client.transport.dispatch("chat", {"message": "#set allowBreak true"})
    return "chest" in block_id()


def bake_durable_food(handler: "FoodAndIronHandler", client) -> bool:
    """Advance durable provisions without spending retries on crop growth."""
    target = 16
    durable_food = sum(count_item(client, item_id) for item_id in FOOD_ITEMS)
    if durable_food >= target:
        return True

    bread = count_item(client, "minecraft:bread")
    shortfall = target - durable_food
    needed_wheat = shortfall * 3
    if count_item(client, "minecraft:wheat") < needed_wheat:
        harvest_persisted_crop_farm(handler, client)

    wheat = count_item(client, "minecraft:wheat")
    if wheat < 3:
        print(
            f"  Durable food: {bread} bread, {wheat} wheat — nothing more "
            "to bake yet (farm needs time to regrow)."
        )
        source = persisted_food_source(handler.state)
        farm_location = (
            handler.state.custom_data.get("farm_location")
            if handler.state is not None
            else None
        )
        if source or (
            isinstance(farm_location, (list, tuple))
            and len(farm_location) == 3
        ):
            raise PacingHoldRequired("renewable crop maturation before T1204")
        return False

    affordable = min(wheat // 3, shortfall)
    result = ensure_supplies(client, {"minecraft:bread": bread + affordable})
    if not result.success:
        return False
    after = count_item(client, "minecraft:bread")
    print(f"  Durable food: baked to {after} bread (was {bread}).")
    if affordable < shortfall:
        raise IncrementalProgressRequired(
            f"prepared {affordable} bread toward T1204"
        )
    return True


def harvest_persisted_crop_farm(
    handler: "FoodAndIronHandler",
    client,
) -> bool:
    """Harvest the checkpointed crop farm when it can be located."""
    if handler.state is None:
        return False
    farm_location = handler.state.custom_data.get("farm_location")
    if not isinstance(farm_location, (list, tuple)) or len(farm_location) != 3:
        source = handler.state.custom_data.get("structures", {}).get(
            "food_source",
            {},
        )
        location = source.get("location")
        if isinstance(location, (list, tuple)) and len(location) == 3:
            farm_location = location
    if not isinstance(farm_location, (list, tuple)) or len(farm_location) != 3:
        print("  Durable food: no persisted crop farm location to harvest.")
        return False

    x, y, z = (int(value) for value in farm_location)
    before = count_item(client, "minecraft:wheat")
    if not harvest_wheat_farm(client, x, y, z, range_=8):
        return False
    return count_item(client, "minecraft:wheat") > before


def craft_shield(client) -> bool:
    """Craft the T1204 shield using any supported plank family."""
    if count_item(client, "minecraft:shield") >= 1:
        return True
    total_planks = sum(
        count_item(client, f"minecraft:{wood}_planks")
        for wood in WOOD_TYPES
    )
    if total_planks < 6 and not _ensure_raw_planks(client, 6):
        print("  Shield: could not ensure 6 planks.")
        return False
    return ensure_supplies(client, {"minecraft:shield": 1}).success
