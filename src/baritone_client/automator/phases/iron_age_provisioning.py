"""Durable storage and provisioning support for FOOD_AND_IRON."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Tuple

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


def mining_workstation_targets(
    handler: "FoodAndIronHandler",
    client,
) -> tuple[Optional[Tuple[int, int, int]], Optional[Tuple[int, int, int]]]:
    """Return a reachable table and a bounded local placement fallback.

    The generic functional harness searches hundreds of candidate stand
    positions.  In a compact house it can select a table on the roof and spend
    minutes proving every route unreachable.  One small view gives enough
    evidence to reuse a table already in reach or place the carried table on
    verified support beside the player.
    """
    state = handler._read_state(client, "Local mining workstation") or {}
    position = state.get("block_position", state.get("position", {}))
    try:
        player = tuple(int(position[axis]) for axis in ("x", "y", "z"))
    except (KeyError, TypeError, ValueError):
        return None, None

    view = client.transport.dispatch("get_view", {"radius": 4})
    data = view.get("data", view) if isinstance(view, dict) else {}
    blocks = {
        (int(block["x"]), int(block["y"]), int(block["z"])): str(block["id"])
        for block in data.get("voxels", [])
        if isinstance(block, dict)
        and all(key in block for key in ("x", "y", "z", "id"))
    }
    px, py, pz = player
    tables = [
        target
        for target, block_id in blocks.items()
        if "crafting_table" in block_id
        and sum((target[index] - player[index]) ** 2 for index in range(3))
        <= 4.5**2
    ]
    reachable = min(
        tables,
        key=lambda target: sum(
            (target[index] - player[index]) ** 2 for index in range(3)
        ),
        default=None,
    )

    local = None
    for dx, dz in ((1, 0), (0, 1), (-1, 0), (0, -1)):
        target = (px + dx, py, pz + dz)
        below = blocks.get((target[0], target[1] - 1, target[2]), "")
        if target not in blocks and below and not any(
            token in below for token in ("air", "water", "lava")
        ):
            local = target
            break
    return reachable, local


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


# The starter farm is a small 5x5 patch. When its wheat keeps failing to
# mature fast enough for the T1204 food target, sitting in an endless
# pacing hold is the same starvation loop the older micro_farm deadlock
# caused. Instead of yielding forever, expand the farm: re-establish the
# same center at a larger size after a bounded streak of fruitless holds.
# Herd hunting is deliberately NOT a fallback here -- a hunted herd is a
# herd we later want alive for breeding.
_CROP_HOLD_HARD_CAP = 6
_FARM_SIZE_START = 5
_FARM_SIZE_MAX = 9
_FARM_SIZE_STEP = 2


def _crop_hold_budget(state) -> dict:
    """Return (and lazily create) the crop-hold escalation ledger."""
    custom = getattr(state, "custom_data", None)
    if not isinstance(custom, dict):
        custom = {}
        state.custom_data = custom
    ledger = custom.setdefault("crop_hold", {})
    if not isinstance(ledger, dict):
        ledger = {}
        custom["crop_hold"] = ledger
    ledger.setdefault("hold_streak", 0)
    ledger.setdefault("expansions", 0)
    return ledger


def _farm_size_for(state) -> int:
    ledger = _crop_hold_budget(state)
    current = int(ledger.get("farm_size") or _FARM_SIZE_START)
    return max(_FARM_SIZE_START, min(_FARM_SIZE_MAX, current))


def _expand_crop_farm(handler: "FoodAndIronHandler", client, farm_location) -> bool:
    """Re-establish the existing farm center at a larger size.

    Returns True when the expansion actually tilled/planted new soil
    (farm_size grew), False when it is already at the size ceiling or the
    center cannot be located/expanded.
    """
    from ...common.farming import establish_wheat_farm, relocate_wheat_farm

    if not isinstance(farm_location, (list, tuple)) or len(farm_location) != 3:
        return False
    ledger = _crop_hold_budget(handler.state)
    size = _farm_size_for(handler.state)
    if size >= _FARM_SIZE_MAX:
        return False
    next_size = size + _FARM_SIZE_STEP
    x, y, z = (int(value) for value in farm_location)
    established = establish_wheat_farm(client, x, y, z, size=next_size, state=handler.state)
    if established is None:
        established = relocate_wheat_farm(
            client,
            x,
            y,
            z,
            size=next_size,
            state=handler.state,
        )
    if established is None:
        return False
    x, y, z = established
    ledger["farm_size"] = next_size
    ledger["expansions"] = int(ledger.get("expansions", 0)) + 1
    print(f"  Crop farm expanded to {next_size}x{next_size} at {(x, y, z)}.")
    return True


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
        source = persisted_food_source(handler.state)
        farm_location = (
            handler.state.custom_data.get("farm_location")
            if handler.state is not None
            else None
        )
        has_farm = (
            isinstance(farm_location, (list, tuple))
            and len(farm_location) == 3
        )
        if has_farm and handler.state is not None:
            ledger = _crop_hold_budget(handler.state)
            ledger["hold_streak"] = int(ledger.get("hold_streak", 0)) + 1
            streak = int(ledger.get("hold_streak", 0))
            at_ceiling = _farm_size_for(handler.state) >= _FARM_SIZE_MAX
            if streak >= _CROP_HOLD_HARD_CAP and not at_ceiling:
                # Expand before yielding again. Iterative expansion means the
                # buddget resets only on success; a failed expansion attempt
                # is credit toward the next escalation rather than a stall.
                if _expand_crop_farm(handler, client, farm_location):
                    ledger["hold_streak"] = 0
                    raise PacingHoldRequired(
                        "renewable crop maturation before T1204 (farm expanded)"
                    )
            print(
                f"  Durable food: {bread} bread, {wheat} wheat — nothing more "
                "to bake yet (farm needs time to regrow)."
            )
        if source or has_farm:
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
