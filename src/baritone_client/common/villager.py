"""Villager infrastructure built from registered bridge primitives."""

import time
from typing import Any, Dict, List, Optional, Tuple

from .base import place_bed
from .combat import entity_position, get_nearby_entities
from .inventory import count_item, craft, select_item
from .navigation import goto


BED_BLOCKS = tuple(
    f"minecraft:{color}_bed"
    for color in (
        "white", "orange", "magenta", "light_blue", "yellow", "lime", "pink",
        "gray", "light_gray", "cyan", "purple", "blue", "brown", "green",
        "red", "black",
    )
)


def _villagers(client, radius: int = 32) -> List[Dict[str, Any]]:
    return [
        entity
        for entity in get_nearby_entities(client, radius, raise_on_error=True)
        if entity.get("type") == "minecraft:villager"
    ]


def _find_blocks(client, block_ids, radius: int = 32) -> List[Dict[str, Any]]:
    response = client.transport.dispatch(
        "find_blocks",
        {"blocks": list(block_ids), "radius": min(128, max(1, int(radius))), "limit": 256},
    )
    found = response.get("found") if isinstance(response, dict) else None
    return [value for value in found or [] if isinstance(value, dict)]


def _bed_count(client) -> int:
    return sum(count_item(client, item_id) for item_id in BED_BLOCKS)


def capture_villager(client, count: int = 1) -> None:
    """Fail closed: the bridge cannot prove boat/minecart passenger capture."""
    return None


def build_villager_breeder(
    client,
    x: int,
    y: int,
    z: int,
    *,
    required_beds: int = 3,
) -> Optional[Dict[str, Any]]:
    """Ensure and verify enough nearby beds for a breeding population.

    One Minecraft bed occupies two blocks. Existing village beds are reused;
    missing beds are placed with the repository's verified placement primitive.
    """
    required_beds = max(3, int(required_beds))
    required_blocks = required_beds * 2
    if not goto(client, int(x), int(y), int(z), timeout=120, tolerance=6.0):
        return None

    bed_positions = _find_blocks(client, BED_BLOCKS, radius=24)
    offsets = ((3, 0), (-3, 0), (0, 3), (0, -3), (3, 3), (-3, -3))
    for dx, dz in offsets:
        if len(bed_positions) >= required_blocks:
            break
        if _bed_count(client) < 1 and not craft(client, "minecraft:white_bed", 1):
            return None
        if place_bed(client, int(x) + dx, int(y), int(z) + dz):
            bed_positions = _find_blocks(client, BED_BLOCKS, radius=24)

    if len(bed_positions) < required_blocks:
        return None
    return {
        "location": [int(x), int(y), int(z)],
        "required_beds": required_beds,
        "bed_blocks": [
            [int(block["x"]), int(block["y"]), int(block["z"])]
            for block in bed_positions
            if all(axis in block for axis in ("x", "y", "z"))
        ],
        "verified": True,
    }


def lock_librarian(client, enchantment: str = None) -> None:
    """Fail closed: current inventory/screen schemas expose no trade details."""
    return None


def start_villager_multiplication(
    client,
    *,
    timeout: float = 45.0,
    poll_interval: float = 1.0,
) -> Optional[Dict[str, Any]]:
    """Feed two adult villagers and require a newly observed offspring."""
    before = _villagers(client, radius=32)
    adults = [entity for entity in before if not bool(entity.get("is_baby"))]
    if len(adults) < 2:
        return None

    positions = [entity_position(entity) for entity in adults[:2]]
    if any(position is None for position in positions):
        return None
    center = tuple(round(sum(position[i] for position in positions) / 2) for i in range(3))
    if not goto(client, *center, timeout=120, tolerance=5.0):
        return None

    bread_before = count_item(client, "minecraft:bread")
    if bread_before < 6:
        if not craft(client, "minecraft:bread", 6 - bread_before):
            return None
        bread_before = count_item(client, "minecraft:bread")
    if bread_before < 6 or not select_item(client, "minecraft:bread", allow_swap=True):
        return None

    for villager in adults[:2]:
        position = entity_position(villager)
        if position is None:
            return None
        client.transport.dispatch(
            "look_at",
            {"x": position[0], "y": position[1] + 1.0, "z": position[2]},
        )
        for _ in range(3):
            response = client.transport.dispatch("throw_item", {"all": False})
            if not isinstance(response, dict) or not response.get("thrown", False):
                return None
            time.sleep(0.1)

    bread_after = count_item(client, "minecraft:bread")
    if bread_before - bread_after < 6:
        return None

    known = {str(entity.get("uuid")) for entity in before if entity.get("uuid")}
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        after = _villagers(client, radius=32)
        offspring = next(
            (
                entity for entity in after
                if bool(entity.get("is_baby"))
                and str(entity.get("uuid")) not in known
            ),
            None,
        )
        if offspring is not None:
            return {
                "before_count": len(before),
                "after_count": len(after),
                "bread_consumed": bread_before - bread_after,
                "offspring_uuid": str(offspring.get("uuid")),
                "offspring_observed": True,
            }
        if time.monotonic() >= deadline:
            return None
        time.sleep(max(0.01, float(poll_interval)))


def find_villager_workstation(
    client, profession: str = "librarian"
) -> Optional[Tuple[int, int, int]]:
    """Find the nearest registered workstation block for a known profession."""
    workstation = {"librarian": "minecraft:lectern"}.get(profession)
    if workstation is None:
        return None
    found = _find_blocks(client, [workstation], radius=32)
    if not found:
        return None
    nearest = min(found, key=lambda block: float(block.get("distance", 999999)))
    return (int(nearest["x"]), int(nearest["y"]), int(nearest["z"]))


__all__ = [
    "BED_BLOCKS",
    "build_villager_breeder",
    "capture_villager",
    "find_villager_workstation",
    "lock_librarian",
    "start_villager_multiplication",
]
