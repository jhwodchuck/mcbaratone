"""Delta-verified bounded crop work."""

from __future__ import annotations

import time
from typing import Any, Callable

from ...common.inventory import get_inventory
from ...common.navigation import find_nearby_block, goto
from ..local_opportunity import LocalOpportunity

CROP_BLOCKS = ("minecraft:wheat", "minecraft:carrots", "minecraft:potatoes", "minecraft:beetroots")
CROP_ITEMS = ("minecraft:wheat", "minecraft:carrot", "minecraft:potato", "minecraft:beetroot")
PLANTABLE_ITEMS = ("minecraft:wheat_seeds", "minecraft:carrot", "minecraft:potato", "minecraft:beetroot_seeds")


def run_crop_opportunity(
    client: Any, opportunity: LocalOpportunity, timeout: float,
    *, inventory_reader: Callable[[Any], Any] = get_inventory,
    traveler: Callable[..., bool] = goto,
    block_finder: Callable[..., Any] = find_nearby_block,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[bool, str, int, int]:
    """Farm once, accepting only a harvest or world-verified replant delta."""
    location = opportunity.location
    if location is None or not traveler(client, *location, timeout=90, tolerance=5.0):
        return False, "crop patch was unreachable", 0, 0
    before_inventory = inventory_reader(client)
    before = sum(int(before_inventory.get(item, 0) or 0) for item in CROP_ITEMS)
    before_plantable = sum(int(before_inventory.get(item, 0) or 0) for item in PLANTABLE_ITEMS)
    client.transport.dispatch("farm", {"range": 8, "x": location[0], "y": location[1], "z": location[2], "replant": True})
    deadline = time.monotonic() + max(1.0, float(timeout))
    after = before
    try:
        while time.monotonic() < deadline:
            sleeper(min(2.0, max(0.05, float(timeout))))
            current = inventory_reader(client)
            after = sum(int(current.get(item, 0) or 0) for item in CROP_ITEMS)
            plantable = sum(int(current.get(item, 0) or 0) for item in PLANTABLE_ITEMS)
            if after > before:
                return True, "crop produce increased", before, after
            if plantable < before_plantable and block_finder(client, list(CROP_BLOCKS), radius=12):
                return True, "planting was verified in the world", before, plantable
    finally:
        try:
            client.transport.dispatch("cancel", {})
        except Exception:
            pass
    return False, "no harvest or planting change was observed", before, after
