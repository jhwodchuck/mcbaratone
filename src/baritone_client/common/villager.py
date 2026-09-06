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

#: A village generates wherever the world seed put it, not near spawn, so a
#: bot that only ever scans its own feet (_villagers' 32-block radius) can
#: run an entire campaign without ever seeing one. Same eight-direction
#: push idiom nether.find_nether_fortress uses to escape a stalled search.
_SEARCH_HEADINGS = (
    (1, 0), (0, 1), (-1, 0), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1),
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


def locate_village(
    client,
    max_distance: int = 800,
    timeout: int = 600,
) -> Optional[Tuple[int, int, int]]:
    """Wander outward, scanning for two adult villagers, until one is found.

    Unlike a Nether fortress, a village has no distinctive block the bridge
    can search for at range -- so this scans for the villagers themselves,
    the same signal VillagerInfraHandler already checks at close range, and
    pushes to a new heading (via explore + goto) whenever a scan produces no
    movement, so the search does not stall at a single stationary point.
    Read-only until an anchor is returned: no capture, no transport.
    """
    try:
        state = client.transport.dispatch("get_state", {})
    except Exception:
        return None
    if not isinstance(state, dict):
        return None
    origin = state.get("block_position", state.get("position", {}))
    try:
        start_x = int(origin.get("x", 0))
        start_z = int(origin.get("z", 0))
    except (TypeError, ValueError):
        return None

    try:
        client.transport.dispatch("explore", {"x": start_x, "z": start_z})
    except Exception:
        pass

    deadline = time.monotonic() + max(0.0, float(timeout))
    scan_interval = 10.0
    next_scan = 0.0
    last_position = None
    stationary_scans = 0
    heading_index = 0

    while time.monotonic() < deadline:
        now = time.monotonic()
        if now < next_scan:
            time.sleep(min(1.0, next_scan - now))
            continue
        next_scan = now + scan_interval

        try:
            current_state = client.transport.dispatch("get_state", {})
        except Exception:
            continue
        if not isinstance(current_state, dict):
            continue
        position = current_state.get("block_position", current_state.get("position", {}))
        try:
            current_x = int(position.get("x", start_x))
            current_y = int(position.get("y", 64))
            current_z = int(position.get("z", start_z))
        except (TypeError, ValueError):
            continue

        distance = ((current_x - start_x) ** 2 + (current_z - start_z) ** 2) ** 0.5
        if distance > max_distance:
            return None

        try:
            adults = [entity for entity in _villagers(client, radius=48) if not bool(entity.get("is_baby"))]
        except Exception:
            adults = []
        positions = [
            position for position in (entity_position(entity) for entity in adults)
            if position is not None
        ]
        if len(positions) >= 2:
            return tuple(
                round(sum(pos[index] for pos in positions) / len(positions))
                for index in range(3)
            )

        if (
            last_position is not None
            and abs(current_x - last_position[0]) <= 3
            and abs(current_z - last_position[1]) <= 3
        ):
            stationary_scans += 1
        else:
            stationary_scans = 0
        last_position = (current_x, current_z)

        if stationary_scans >= 2:
            stationary_scans = 0
            heading = _SEARCH_HEADINGS[heading_index % len(_SEARCH_HEADINGS)]
            heading_index += 1
            leg = min(192, max(64, int(max_distance) // 4))
            waypoint = (
                start_x + int(heading[0] * leg),
                current_y,
                start_z + int(heading[1] * leg),
            )
            try:
                client.transport.dispatch(
                    "goto",
                    {"x": waypoint[0], "y": waypoint[1], "z": waypoint[2], "radius": 12},
                )
                client.transport.dispatch("explore", {"x": waypoint[0], "z": waypoint[2]})
            except Exception:
                pass

    return None


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
    "locate_village",
    "lock_librarian",
    "start_villager_multiplication",
]
