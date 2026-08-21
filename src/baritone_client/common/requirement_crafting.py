"""Adapters between supply shortfalls and absolute crafting targets."""

from __future__ import annotations

from typing import Any, Callable


def absolute_requirement(
    carried: int,
    shortfall: int,
) -> int:
    """Recover an absolute requirement from a carried-item shortfall."""
    return int(carried) + max(1, int(shortfall))


def craft_shortfall_with_table(
    client: Any,
    item_id: str,
    shortfall: int,
    *,
    count_item: Callable[[Any, str], int],
    craft_with_table: Callable[[Any, str, int], bool],
) -> bool:
    """Add a requested shortfall to the carried count before crafting."""
    target = absolute_requirement(count_item(client, item_id), shortfall)
    return craft_with_table(client, item_id, target)
