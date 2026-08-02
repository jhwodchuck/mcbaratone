"""Spawner-backed XP utilities using registered bridge routes."""

import time
from typing import Any, Dict, Optional, Tuple

from .combat import entity_position, get_nearby_entities, safe_combat
from .navigation import goto


HOSTILE_TYPES = {
    "minecraft:zombie",
    "minecraft:husk",
    "minecraft:drowned",
    "minecraft:skeleton",
    "minecraft:stray",
    "minecraft:spider",
    "minecraft:cave_spider",
    "minecraft:silverfish",
}


def _block_id(client, location: Tuple[int, int, int]) -> str:
    response = client.transport.dispatch(
        "get_block",
        {"x": int(location[0]), "y": int(location[1]), "z": int(location[2])},
    )
    return str(response.get("id") or response.get("block") or "")


def _experience(client) -> Tuple[int, int]:
    state = client.transport.dispatch("get_state", {})
    try:
        return (
            int(state.get("experience_level", 0) or 0),
            int(state.get("experience_total", 0) or 0),
        )
    except (AttributeError, TypeError, ValueError):
        return (0, 0)


def find_spawner(
    client, max_distance: int = 100
) -> Optional[Tuple[int, int, int]]:
    """Find the nearest loaded dungeon spawner with ``find_blocks``."""
    response = client.transport.dispatch(
        "find_blocks",
        {
            "blocks": ["minecraft:spawner"],
            "radius": min(128, max(1, int(max_distance))),
            "limit": 32,
        },
    )
    found = response.get("found") if isinstance(response, dict) else None
    candidates = [value for value in found or [] if isinstance(value, dict)]
    if not candidates:
        return None
    nearest = min(candidates, key=lambda block: float(block.get("distance", 999999)))
    try:
        return (int(nearest["x"]), int(nearest["y"]), int(nearest["z"]))
    except (KeyError, TypeError, ValueError):
        return None


def build_simple_mob_farm(client, x: int, y: int, z: int) -> None:
    """Fail closed: no registered primitive can verify a generic farm design."""
    return None


def grind_xp_at_location(
    client,
    x: int,
    y: int,
    z: int,
    target_level: int = 30,
    *,
    timeout: float = 600.0,
    poll_interval: float = 1.0,
) -> Optional[Dict[str, Any]]:
    """Fight spawned hostiles until the live player level reaches the target."""
    location = (int(x), int(y), int(z))
    target_level = max(0, int(target_level))
    if _block_id(client, location) != "minecraft:spawner":
        return None
    if not goto(client, *location, timeout=180, tolerance=7.0):
        return None

    start_level, start_total = _experience(client)
    encounters = 0
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        level, total = _experience(client)
        xp_gained = max(0, total - start_total)
        if level >= target_level and encounters > 0 and xp_gained > 0:
            return {
                "location": list(location),
                "start_level": start_level,
                "achieved_level": level,
                "target_level": target_level,
                "encounters": encounters,
                "xp_gained": xp_gained,
            }
        if time.monotonic() >= deadline:
            return None

        entities = get_nearby_entities(client, radius=16, raise_on_error=True)
        targets = []
        for entity in entities:
            position = entity_position(entity)
            if (
                entity.get("type") not in HOSTILE_TYPES
                or entity.get("id") is None
                or position is None
            ):
                continue
            source_distance = sum(
                (float(position[index]) - float(location[index])) ** 2
                for index in range(3)
            ) ** 0.5
            if source_distance <= 16.0:
                targets.append(entity)
        if targets:
            target = min(targets, key=lambda entity: float(entity.get("distance", 999999)))
            if safe_combat(client, int(target["id"]), max_duration=30):
                encounters += 1
                if not goto(client, *location, timeout=120, tolerance=7.0):
                    return None
        time.sleep(max(0.01, float(poll_interval)))


def enchant_tool_perfectly(client, tool_slot: int, enchantments: list) -> None:
    """Fail closed: bridge inventory schemas do not expose enchantment data."""
    return None


__all__ = [
    "HOSTILE_TYPES",
    "build_simple_mob_farm",
    "enchant_tool_perfectly",
    "find_spawner",
    "grind_xp_at_location",
]
