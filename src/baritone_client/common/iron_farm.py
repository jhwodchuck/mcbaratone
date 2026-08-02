"""Read-only iron-farm observations supported by the current bridge.

The bridge does not expose survival-safe commands for transporting villagers,
capturing zombies, or constructing an iron farm.  These helpers therefore
observe already-completed survival work and never substitute teleport, summon,
or magic-build commands for gameplay.
"""

from __future__ import annotations

from math import dist
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


Position = Tuple[int, int, int]


def _dispatch_payload(response: Any) -> Dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    nested = response.get("data")
    if isinstance(nested, dict):
        return nested
    return response


def _position(value: Any) -> Position | None:
    try:
        if isinstance(value, Mapping):
            return int(value["x"]), int(value["y"]), int(value["z"])
        if isinstance(value, (list, tuple)) and len(value) >= 3:
            return int(value[0]), int(value[1]), int(value[2])
    except (KeyError, TypeError, ValueError):
        return None
    return None


def get_nearby_entities(client: Any, radius: int = 32) -> List[Dict[str, Any]]:
    """Return the registered ``get_entities`` route's real entity schema."""
    response = _dispatch_payload(
        client.transport.dispatch(
            "get_entities",
            {"radius": max(1, min(64, int(radius)))},
        )
    )
    entities = response.get("entities", [])
    if not isinstance(entities, list):
        return []
    return [dict(entity) for entity in entities if isinstance(entity, Mapping)]


def entities_near(
    entities: Iterable[Mapping[str, Any]],
    farm_location: Iterable[int],
    entity_type: str,
    radius: float,
) -> List[Dict[str, Any]]:
    """Filter real bridge entities by namespaced type and world position."""
    anchor = _position(list(farm_location))
    if anchor is None:
        return []
    matches: List[Dict[str, Any]] = []
    for entity in entities:
        if str(entity.get("type", "")).lower() != entity_type.lower():
            continue
        location = _position(entity.get("position"))
        if location is None or dist(anchor, location) > float(radius):
            continue
        matches.append(dict(entity))
    return matches


def detect_farm_entities(
    client: Any,
    farm_location: Iterable[int],
    limit: int = 8,
) -> Dict[str, int]:
    """Count farm-adjacent entities using ``entities[*].type``."""
    anchor = _position(list(farm_location))
    if anchor is None:
        return {}
    try:
        entities = get_nearby_entities(client, radius=max(16, int(limit) + 4))
    except Exception:
        return {}
    counts: Dict[str, int] = {}
    for entity in entities:
        location = _position(entity.get("position"))
        entity_type = str(entity.get("type", "")).lower()
        if not entity_type or location is None or dist(anchor, location) > limit:
            continue
        counts[entity_type] = counts.get(entity_type, 0) + 1
    return counts


def adult_villagers_near(
    client: Any,
    farm_location: Iterable[int],
    limit: int = 8,
) -> List[Dict[str, Any]]:
    """Return adult villagers close enough to participate in a farm."""
    try:
        entities = get_nearby_entities(client, radius=max(16, int(limit) + 4))
    except Exception:
        return []
    return [
        entity
        for entity in entities_near(
            entities,
            farm_location,
            "minecraft:villager",
            limit,
        )
        if not bool(entity.get("is_baby", False))
    ]


def _block_id(client: Any, position: Sequence[int]) -> str:
    response = _dispatch_payload(
        client.transport.dispatch(
            "get_block",
            {"x": int(position[0]), "y": int(position[1]), "z": int(position[2])},
        )
    )
    return str(response.get("id") or response.get("type") or response.get("block") or "")


def _positions(value: Any) -> List[Position]:
    if not isinstance(value, list):
        return []
    return [position for item in value if (position := _position(item)) is not None]


def detect_farm_structure(
    client: Any,
    farm_location: Iterable[int],
    witnesses: Mapping[str, Any] | None = None,
) -> bool:
    """Verify live witness blocks for an existing producing farm.

    The witnesses are deliberately structural, not a caller-supplied list of
    arbitrary blocks: three beds, a hopper/chest collection point, and at
    least nine spawn-platform blocks must all be close to the farm anchor.
    """
    anchor = _position(list(farm_location))
    if anchor is None or not isinstance(witnesses, Mapping):
        return False
    beds = _positions(witnesses.get("beds"))
    platform = _positions(witnesses.get("spawn_platform"))
    hopper = _position(witnesses.get("hopper"))
    chest = _position(witnesses.get("chest"))
    all_positions = beds + platform + ([hopper] if hopper else []) + ([chest] if chest else [])
    if (
        len(set(beds)) < 3
        or len(set(platform)) < 9
        or hopper is None
        or chest is None
    ):
        return False
    if any(dist(anchor, position) > 16 for position in all_positions):
        return False
    if any(not _block_id(client, position).endswith("_bed") for position in beds):
        return False
    if _block_id(client, hopper) != "minecraft:hopper":
        return False
    if _block_id(client, chest) not in {"minecraft:chest", "minecraft:trapped_chest"}:
        return False
    invalid_platform = {
        "",
        "minecraft:air",
        "minecraft:cave_air",
        "minecraft:void_air",
        "minecraft:water",
        "minecraft:lava",
    }
    return all(_block_id(client, position) not in invalid_platform for position in platform)


def build_iron_farm(
    client: Any,
    x: int,
    y: int,
    z: int,
    witnesses: Mapping[str, Any] | None = None,
) -> bool:
    """Observe an existing structure; construction is not bridge-supported."""
    return detect_farm_structure(client, (x, y, z), witnesses)


def move_villagers_to_farm(
    client: Any,
    villager_locations: List[Position],
    farm_location: Position,
) -> bool:
    """Observe three adult villagers already at the farm; never teleport them."""
    required = max(3, len(villager_locations))
    return len(adult_villagers_near(client, farm_location)) >= required


def add_zombie_to_farm(client: Any, farm_location: Position) -> bool:
    """Observe an already-captured zombie; never summon or move one."""
    return detect_farm_entities(client, farm_location).get("minecraft:zombie", 0) >= 1


def start_iron_production(client: Any, farm_location: Position) -> bool:
    """Observe a farm-adjacent iron golem as production evidence."""
    return detect_farm_entities(client, farm_location, limit=16).get("minecraft:iron_golem", 0) >= 1


def get_player_farm_position(client: Any, fallback: Any = None) -> Position:
    """Use the player's real block position as a truthful candidate anchor."""
    if fallback is not None:
        parsed = _position(fallback)
        if parsed is not None:
            return parsed
    state = _dispatch_payload(client.transport.dispatch("get_state", {}))
    parsed = _position(state.get("block_position") or state.get("position"))
    if parsed is None:
        raise ValueError("Bridge state did not include a usable player position")
    return parsed
