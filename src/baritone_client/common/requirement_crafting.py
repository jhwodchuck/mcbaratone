"""Adapters between supply shortfalls and absolute crafting targets."""

from __future__ import annotations

from typing import Any, Callable


def craft_shortfall_with_table(
    client: Any,
    item_id: str,
    shortfall: int,
    *,
    count_item: Callable[[Any, str], int],
    craft_with_table: Callable[[Any, str, int], bool],
) -> bool:
    """Add a requested shortfall to the carried count before crafting."""
    carried = count_item(client, item_id)
    return craft_with_table(client, item_id, carried + max(1, int(shortfall)))
