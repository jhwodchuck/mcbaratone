"""Durable capability and survival checks shared by boot actions."""

from __future__ import annotations

from typing import Any

from ..common.inventory import count_item
from ..common.tasks import SurvivalRecoveryRequired


_INFRASTRUCTURE_BLOCKS = {
    "crafting_table": {"minecraft:crafting_table"},
    "furnace": {"minecraft:furnace", "minecraft:blast_furnace"},
    "supply_chest": {"minecraft:chest", "minecraft:trapped_chest"},
}
_DURABLE_TOOL_FAMILIES = {
    role: tuple(
        f"minecraft:{material}_{role}"
        for material in ("stone", "iron", "diamond", "netherite")
    )
    for role in ("pickaxe", "axe", "shovel", "sword")
}


def has_durable_tool_set(client: Any) -> bool:
    """Return whether every basic tool role is covered by stone or better."""
    return all(
        any(count_item(client, item_id) > 0 for item_id in family)
        for family in _DURABLE_TOOL_FAMILIES.values()
    )


def nearby_infrastructure_record(
    context: Any,
    *,
    max_distance: float = 96.0,
) -> dict:
    """Return checkpointed infrastructure only when its area is locally loaded."""
    custom_data = getattr(context.state, "custom_data", {})
    structures = custom_data.get("structures", {}) if isinstance(custom_data, dict) else {}
    try:
        state = context.client.transport.dispatch("get_state", {})
    except Exception:
        return {}
    position = state.get("block_position", state.get("position", {})) or {}
    if not all(axis in position for axis in ("x", "y", "z")):
        return {}

    for name in ("starter_house", "bootstrap_base"):
        record = structures.get(name, {})
        if not isinstance(record, dict):
            continue
        anchor = record.get("crafting_table") or record.get("origin")
        if not isinstance(anchor, (list, tuple)) or len(anchor) != 3:
            continue
        distance = sum(
            (float(position[axis]) - float(anchor[index])) ** 2
            for index, axis in enumerate(("x", "y", "z"))
        ) ** 0.5
        if distance <= float(max_distance):
            return record
    return {}


def has_live_infrastructure(context: Any) -> bool:
    """Require nearby world blocks rather than a stale checkpoint flag."""
    record = nearby_infrastructure_record(context)
    if not record:
        return False
    for key, accepted in _INFRASTRUCTURE_BLOCKS.items():
        position = record.get(key)
        if not isinstance(position, (list, tuple)) or len(position) != 3:
            return False
        try:
            block = context.client.transport.dispatch(
                "get_block",
                {
                    "x": int(position[0]),
                    "y": int(position[1]),
                    "z": int(position[2]),
                },
            ).get("id", "")
        except Exception:
            return False
        if block not in accepted:
            return False
    return True


def durable_boot_capabilities(context: Any) -> bool:
    """Return whether gathering bootstrap wood would recreate owned assets."""
    return has_durable_tool_set(context.client) and has_live_infrastructure(context)


def require_survival_margin(client: Any) -> None:
    """Yield boot work until its verifier's health and hunger margin is met."""
    state = client.transport.dispatch("get_state", {})
    health = float(state.get("health", 20) or 0)
    food = int(state.get("food_level", state.get("food", 20)) or 0)
    if health < 12.0 or food < 14:
        raise SurvivalRecoveryRequired(
            "boot survival margin requires "
            f"health>=12 and food>=14 (health={health:.1f}, food={food})"
        )
