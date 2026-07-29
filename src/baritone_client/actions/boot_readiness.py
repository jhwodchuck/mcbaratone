"""Durable capability and survival checks shared by boot actions."""

from __future__ import annotations

from typing import Any

from ..common.inventory import count_item
from ..common.tasks import SurvivalRecoveryRequired


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


def has_verified_infrastructure(state: Any) -> bool:
    """Return whether checkpointed boot infrastructure is reusable."""
    custom_data = getattr(state, "custom_data", {})
    structures = custom_data.get("structures", {}) if isinstance(custom_data, dict) else {}
    bootstrap = structures.get("bootstrap_base", {})
    if isinstance(bootstrap, dict) and bootstrap.get("verified") is True:
        return True

    house = structures.get("starter_house", {})
    if not isinstance(house, dict):
        return False
    return all(
        isinstance(house.get(key), (list, tuple)) and len(house[key]) == 3
        for key in ("origin", "crafting_table", "furnace", "supply_chest")
    )


def durable_boot_capabilities(context: Any) -> bool:
    """Return whether gathering bootstrap wood would recreate owned assets."""
    return has_durable_tool_set(context.client) and has_verified_infrastructure(
        context.state
    )


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
