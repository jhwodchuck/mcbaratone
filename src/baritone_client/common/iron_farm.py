"""Bounded survival construction, transport, and iron-farm observations."""

from __future__ import annotations

from math import dist
from typing import Any, Callable, Dict, Iterable, List, Mapping, Sequence, Tuple


Position = Tuple[int, int, int]

BUILDING_MATERIALS = (
    "minecraft:cobblestone",
    "minecraft:stone",
    "minecraft:stone_bricks",
    "minecraft:deepslate",
    "minecraft:cobbled_deepslate",
)
BOAT_ITEMS = (
    "minecraft:oak_boat",
    "minecraft:spruce_boat",
    "minecraft:birch_boat",
    "minecraft:jungle_boat",
    "minecraft:acacia_boat",
    "minecraft:dark_oak_boat",
    "minecraft:mangrove_boat",
    "minecraft:cherry_boat",
    "minecraft:pale_oak_boat",
)


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
    return verify_farm_structure_witnesses(
        lambda position: _block_id(client, position),
        farm_location,
        witnesses,
    )


def verify_farm_structure_witnesses(
    block_id: Callable[[Sequence[int]], str],
    farm_location: Iterable[int],
    witnesses: Mapping[str, Any] | None,
) -> bool:
    """Validate fixed farm witnesses through a caller-provided block lookup."""
    anchor = _position(list(farm_location))
    if anchor is None or not isinstance(witnesses, Mapping):
        return False
    beds = _positions(witnesses.get("beds"))
    platform = _positions(witnesses.get("spawn_platform"))
    hopper = _position(witnesses.get("hopper"))
    chest = _position(witnesses.get("chest"))
    water = _position(witnesses.get("water_source"))
    lava = _position(witnesses.get("lava_source"))
    all_positions = (
        beds
        + platform
        + ([hopper] if hopper else [])
        + ([chest] if chest else [])
        + ([water] if water else [])
        + ([lava] if lava else [])
    )
    if (
        len(set(beds)) < 3
        or len(set(platform)) < 9
        or hopper is None
        or chest is None
        or water is None
        or lava is None
    ):
        return False
    if any(dist(anchor, position) > 16 for position in all_positions):
        return False
    if any(not block_id(position).endswith("_bed") for position in beds):
        return False
    if block_id(hopper) != "minecraft:hopper":
        return False
    if block_id(chest) not in {"minecraft:chest", "minecraft:trapped_chest"}:
        return False
    if block_id(water) != "minecraft:water":
        return False
    if block_id(lava) != "minecraft:lava":
        return False
    invalid_platform = {
        "",
        "minecraft:air",
        "minecraft:cave_air",
        "minecraft:void_air",
        "minecraft:water",
        "minecraft:lava",
    }
    return all(block_id(position) not in invalid_platform for position in platform)


def iron_farm_witnesses(farm_location: Position) -> Dict[str, Any]:
    """Return the fixed witness coordinates for the bounded farm layout."""
    x, y, z = farm_location
    return {
        "beds": [[x - 1, y + 1, z - 1], [x, y + 1, z - 1], [x + 1, y + 1, z - 1]],
        "hopper": [x, y + 1, z + 3],
        "chest": [x, y, z + 3],
        "water_source": [x, y + 4, z - 2],
        "lava_source": [x, y + 3, z + 2],
        "spawn_platform": [
            [px, y + 4, pz]
            for px in range(x - 2, x + 3)
            for pz in range(z - 2, z + 3)
            if (px, pz) not in {(x, z - 2), (x, z + 2), (x, z + 3)}
        ],
    }


def _choose_building_material(client: Any, required: int) -> str | None:
    from .inventory import count_item

    for item_id in BUILDING_MATERIALS:
        if count_item(client, item_id) >= required:
            return item_id
    return None


def _place_expected(client: Any, position: Position, item_id: str, expected: str) -> bool:
    from .base import robust_place

    current = _block_id(client, position)
    if current == expected or (expected == "#bed" and current.endswith("_bed")):
        return True
    if current not in {"", "minecraft:air", "minecraft:cave_air", "minecraft:void_air"}:
        return False
    if not robust_place(client, *position, item_id):
        return False
    placed = _block_id(client, position)
    return placed == expected or (expected == "#bed" and placed.endswith("_bed"))


def _place_fluid(client: Any, position: Position, bucket_id: str, expected: str) -> bool:
    """Empty a selected bucket through the bridge's vanilla use-on-block path."""
    from .inventory import select_item
    from .navigation import goto

    if _block_id(client, position) == expected:
        return True
    if _block_id(client, position) not in {
        "",
        "minecraft:air",
        "minecraft:cave_air",
        "minecraft:void_air",
    }:
        return False
    if not goto(client, *position, timeout=120, tolerance=3.0):
        return False
    if not select_item(client, bucket_id, allow_swap=True):
        return False
    response = _dispatch_payload(
        client.transport.dispatch(
            "place_block",
            {
                "x": position[0],
                "y": position[1],
                "z": position[2],
                "item": bucket_id,
            },
        )
    )
    if response.get("error"):
        return False
    return _block_id(client, position) == expected


def construct_iron_farm(
    client: Any,
    farm_location: Position,
) -> Tuple[bool, Dict[str, Any], str]:
    """Build a fixed, inventory-backed farm shell with bounded placements."""
    from .inventory import count_item

    witnesses = iron_farm_witnesses(farm_location)
    x, y, z = farm_location
    floor = [(px, y, pz) for px in range(x - 2, x + 3) for pz in range(z - 2, z + 3)]
    walls = [
        (px, py, pz)
        for py in (y + 1, y + 2)
        for px in range(x - 2, x + 3)
        for pz in range(z - 2, z + 3)
        if px in {x - 2, x + 2} or pz in {z - 2, z + 2}
    ]
    shade = [(px, y + 3, pz) for px in range(x - 1, x + 2) for pz in range(z - 1, z + 2)]
    platform = [tuple(position) for position in witnesses["spawn_platform"]]
    structural = list(dict.fromkeys(floor + walls + shade + platform))
    material = _choose_building_material(client, len(structural))
    requirements = {
        "building_blocks": len(structural),
        "minecraft:white_bed": 3,
        "minecraft:hopper": 1,
        "minecraft:chest": 1,
        "minecraft:water_bucket": 1,
        "minecraft:lava_bucket": 1,
    }
    missing = []
    if material is None:
        missing.append(f"{len(structural)} matching solid building blocks")
    for item_id, required in requirements.items():
        if item_id == "building_blocks":
            continue
        if count_item(client, item_id) < required:
            missing.append(f"{required} {item_id}")
    if missing:
        return False, witnesses, "missing farm inventory: " + ", ".join(missing)

    for position in structural:
        if not _place_expected(client, position, material, material):
            return False, witnesses, f"failed survival block placement at {position}"
    for position in _positions(witnesses["beds"]):
        if not _place_expected(client, position, "minecraft:white_bed", "#bed"):
            return False, witnesses, f"failed bed placement at {position}"
    special = (
        (_position(witnesses["chest"]), "minecraft:chest", "minecraft:chest"),
        (_position(witnesses["hopper"]), "minecraft:hopper", "minecraft:hopper"),
    )
    for position, item_id, expected in special:
        if position is None or not _place_expected(client, position, item_id, expected):
            return False, witnesses, f"failed {item_id} placement at {position}"
    fluids = (
        (_position(witnesses["water_source"]), "minecraft:water_bucket", "minecraft:water"),
        (_position(witnesses["lava_source"]), "minecraft:lava_bucket", "minecraft:lava"),
    )
    for position, bucket_id, expected in fluids:
        if position is None or not _place_fluid(client, position, bucket_id, expected):
            return False, witnesses, f"failed {bucket_id} use at {position}"
    if not detect_farm_structure(client, farm_location, witnesses):
        return False, witnesses, "farm witness verification failed after construction"
    return True, witnesses, "bounded survival farm structure constructed"


def build_iron_farm(
    client: Any,
    x: int,
    y: int,
    z: int,
    witnesses: Mapping[str, Any] | None = None,
) -> bool:
    """Verify an existing layout or build the bounded survival layout."""
    if witnesses is not None:
        return detect_farm_structure(client, (x, y, z), witnesses)
    try:
        built, _witnesses, _reason = construct_iron_farm(client, (x, y, z))
        return built
    except Exception:
        return False


def transport_entity_to_farm(
    client: Any,
    entity: Mapping[str, Any],
    farm_location: Position,
    *,
    vehicle_type: str,
    tolerance: float = 6.0,
    timeout_ms: int = 120_000,
    release_at_destination: bool = True,
) -> Tuple[bool, Dict[str, Any], str]:
    """Capture, physically transport, release, and independently re-observe an entity."""
    target_id = entity.get("id")
    if not isinstance(target_id, int) or vehicle_type != "boat":
        return False, {}, "invalid entity transport target or vehicle"
    source_position = _position(entity.get("position"))
    if source_position is None:
        return False, {}, "entity transport target has no usable position"
    common = {
        "entity_id": target_id,
        "vehicle_type": vehicle_type,
        "max_distance": 6.0,
    }
    evidence: Dict[str, Any] = {"target_entity_id": target_id, "vehicle_type": vehicle_type}
    try:
        from .inventory import count_item, craft, select_item
        from .navigation import goto

        if not goto(client, *source_position, timeout=120, tolerance=4.0):
            return False, evidence, "could not navigate within capture range of entity"
        boat_item = next((item for item in BOAT_ITEMS if count_item(client, item) > 0), None)
        if boat_item is None:
            for candidate in BOAT_ITEMS:
                if craft(client, candidate, 1) and count_item(client, candidate) > 0:
                    boat_item = candidate
                    break
        if boat_item is None or not select_item(client, boat_item, allow_swap=True):
            return False, evidence, "non-chest boat unavailable for entity transport"
        evidence["boat_item"] = boat_item
        capture = _dispatch_payload(
            client.transport.dispatch("entity_transport", {"action": "capture", **common})
        )
        evidence["capture"] = capture
        if not capture.get("success") or not capture.get("passenger_verified"):
            return False, evidence, "bridge did not verify entity capture"
        status = _dispatch_payload(
            client.transport.dispatch("entity_transport", {"action": "status", **common})
        )
        evidence["captured_status"] = status
        if not status.get("success") or not status.get("passenger_verified"):
            return False, evidence, "captured passenger was not retained"
        movement = _dispatch_payload(
            client.transport.dispatch(
                "entity_transport",
                {
                    "action": "transport",
                    **common,
                    "destination": {
                        "x": int(farm_location[0]),
                        "y": int(farm_location[1]),
                        "z": int(farm_location[2]),
                    },
                    "tolerance": float(tolerance),
                    "timeout_ms": int(timeout_ms),
                },
            )
        )
        evidence["transport"] = movement
        if not movement.get("success") or not movement.get("passenger_verified"):
            return False, evidence, "bridge did not verify physical passenger transport"
        if release_at_destination:
            released = _dispatch_payload(
                client.transport.dispatch("entity_transport", {"action": "release", **common})
            )
            evidence["release"] = released
            if not released.get("success"):
                return False, evidence, "bridge failed to release transported passenger"
        else:
            retained = _dispatch_payload(
                client.transport.dispatch("entity_transport", {"action": "status", **common})
            )
            evidence["retained_status"] = retained
            if not retained.get("success") or not retained.get("passenger_verified"):
                return False, evidence, "zombie was not retained as a boat passenger"
        observed = next(
            (
                value
                for value in get_nearby_entities(client, radius=64)
                if value.get("id") == target_id
            ),
            None,
        )
        observed_position = _position(observed.get("position")) if observed else None
        evidence["observed_position"] = list(observed_position) if observed_position else None
        if observed_position is None or dist(observed_position, farm_location) > tolerance:
            return False, evidence, "transported passenger is outside the farm tolerance"
        outcome = "released" if release_at_destination else "retained in boat"
        return True, evidence, f"physical entity transport independently verified ({outcome})"
    except Exception as exc:
        return False, evidence, f"entity transport failed: {exc}"


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
    """Choose a bounded candidate floor near the player's real position."""
    if fallback is not None:
        parsed = _position(fallback)
        if parsed is not None:
            return parsed
    state = _dispatch_payload(client.transport.dispatch("get_state", {}))
    parsed = _position(state.get("block_position") or state.get("position"))
    if parsed is None:
        raise ValueError("Bridge state did not include a usable player position")
    return parsed[0] + 12, parsed[1] - 1, parsed[2]
