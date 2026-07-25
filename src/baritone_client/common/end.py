"""
End-dimension helper routines built on top of mission macros.
"""

import logging
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


def _unwrap(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    nested = payload.get("data")
    if isinstance(nested, dict):
        merged = dict(payload)
        merged.pop("data", None)
        merged.update(nested)
        return merged
    return payload


def _entities(payload: Any) -> List[Dict[str, Any]]:
    return [
        entity
        for entity in _unwrap(payload).get("entities", [])
        if isinstance(entity, dict)
    ]


def _found(payload: Any) -> List[Dict[str, Any]]:
    return [
        block
        for block in _unwrap(payload).get("found", [])
        if isinstance(block, dict)
    ]


def _position(payload: Dict[str, Any]) -> Dict[str, Any]:
    value = payload.get("block_position", payload.get("position", {}))
    return value if isinstance(value, dict) else {}


def _get_eye_direction(client) -> Optional[float]:
    """Poll for a thrown eye and return its horizontal direction in radians."""
    import math
    start = time.time()
    while time.time() - start < 5.0:
        entities = client.transport.dispatch("get_entities", {"radius": 64})
        for ent in _entities(entities):
            if ent.get("type") == "minecraft:eye_of_ender":
                # Use velocity if available to determine heading
                vel = ent.get("velocity", {})
                vx, vz = vel.get("x", 0), vel.get("z", 0)
                if abs(vx) > 0.01 or abs(vz) > 0.01:
                    return math.atan2(vz, vx)
        time.sleep(0.1)
    return None


def triangulate_stronghold(client) -> Optional[Tuple[int, int]]:
    """
    Perform real triangulation by throwing two eyes.
    Returns (x, z) of estimated stronghold location.
    """
    import math
    from .inventory import select_item
    from .navigation import goto

    # 1. First throw
    if not select_item(client, "minecraft:ender_eye"):
        logger.error("No Eyes of Ender for triangulation")
        return None

    state1 = client.transport.dispatch("get_state", {})
    p1 = _position(state1)
    x1, z1 = p1.get("x", 0), p1.get("z", 0)

    print("Throwing first eye...")
    client.transport.dispatch("use_item", {"hand": "MAIN_HAND"})
    theta1 = _get_eye_direction(client)
    if theta1 is None:
        logger.error("Failed to track first eye")
        return None

    # 2. Travel a bit for a baseline
    # Move perpendicular to the eye direction (approx 100 blocks)
    dx = -math.sin(theta1) * 100
    dz = math.cos(theta1) * 100
    print(f"Moving to baseline location ({x1+dx}, {z1+dz})...")
    goto(client, int(x1 + dx), int(p1.get("y", 64)), int(z1 + dz))

    # 3. Second throw
    state2 = client.transport.dispatch("get_state", {})
    p2 = _position(state2)
    x2, z2 = p2.get("x", 0), p2.get("z", 0)

    print("Throwing second eye...")
    client.transport.dispatch("use_item", {"hand": "MAIN_HAND"})
    theta2 = _get_eye_direction(client)
    if theta2 is None:
        logger.error("Failed to track second eye")
        return None

    # 4. Math: Intersection of two lines
    # z - z1 = m1(x - x1)
    # z - z2 = m2(x - x2)
    m1 = math.tan(theta1)
    m2 = math.tan(theta2)

    if abs(m1 - m2) < 0.001:
        logger.error("Triangulation lines are parallel!")
        return None

    ix = (z2 - z1 + m1*x1 - m2*x2) / (m1 - m2)
    iz = z1 + m1 * (ix - x1)

    print(f"Estimated stronghold at: ({int(ix)}, {int(iz)})")
    return int(ix), int(iz)


def spiral_stronghold_search(client, max_radius: int = 2000, step_size: int = 200) -> Optional[Tuple[int, int]]:
    """
    Perform spiral search for stronghold when triangulation fails.
    Moves in expanding spiral pattern, throwing eyes to detect proximity.

    Args:
        client: Baritone client instance
        max_radius: Maximum search radius from start position
        step_size: Distance between spiral points

    Returns:
        (x, z) coordinates of potential stronghold or None if not found
    """
    import math
    from .inventory import select_item
    from .navigation import goto

    if not select_item(client, "minecraft:ender_eye"):
        logger.error("No Eyes of Ender for spiral search")
        return None

    # Get starting position
    start_state = client.transport.dispatch("get_state", {})
    start_pos = _position(start_state)
    start_x, start_z = start_pos.get("x", 0), start_pos.get("z", 0)
    start_y = start_pos.get("y", 64)

    print(f"Starting spiral search from ({start_x}, {start_z})")

    # Track best candidate
    best_coords = None
    best_distance = float('inf')

    # Spiral parameters
    angle = 0
    radius = step_size

    while radius <= max_radius:
        # Calculate next position in spiral
        x = start_x + radius * math.cos(angle)
        z = start_z + radius * math.sin(angle)

        print(f"Moving to spiral point ({int(x)}, {int(z)}) at radius {radius}")

        # Navigate to position
        if not goto(client, int(x), int(start_y), int(z), timeout=120):
            print(f"Failed to reach spiral point ({int(x)}, {int(z)}), skipping")
            # Try next angle
            angle += math.pi / 4  # 45 degrees
            if angle >= 2 * math.pi:
                angle -= 2 * math.pi
                radius += step_size
            continue

        # Throw eye at this position
        print("Throwing eye for proximity check...")
        client.transport.dispatch("use_item", {"hand": "MAIN_HAND"})

        # Monitor eye flight distance
        eye_distance = _monitor_eye_flight(client)
        if eye_distance is None:
            print("Failed to track eye flight")
        else:
            print(f"Eye flew {eye_distance:.1f} blocks")

            # Eyes fly shorter distances when near strongholds
            # Typical range: 1000-2000 blocks when far, much less when close
            if eye_distance < 300:  # Close to stronghold
                print("Eye flew short distance - potential stronghold nearby!")
                return int(x), int(z)
            elif eye_distance < best_distance:
                best_distance = eye_distance
                best_coords = (int(x), int(z))

        # Advance spiral
        angle += math.pi / 4  # 45 degrees
        if angle >= 2 * math.pi:
            angle -= 2 * math.pi
            radius += step_size

    print(f"Spiral search complete. Best candidate at {best_coords} with eye distance {best_distance:.1f}")
    return best_coords


def _monitor_eye_flight(client) -> Optional[float]:
    """
    Monitor an eye of ender flight to estimate distance to stronghold.
    Returns approximate flight distance or None if tracking failed.
    """
    import time
    import math

    start_time = time.time()
    max_flight_time = 30  # Eyes despawn after ~30 seconds

    initial_pos = None
    max_distance = 0

    while time.time() - start_time < max_flight_time:
        entities = client.transport.dispatch("get_entities", {"radius": 128})
        eye = None

        for ent in _entities(entities):
            if ent.get("type") == "minecraft:eye_of_ender":
                eye = ent
                break

        if eye is None:
            # Eye despawned or hit portal
            break

        pos = eye.get("position", {})
        x, z = pos.get("x", 0), pos.get("z", 0)

        if initial_pos is None:
            initial_pos = (x, z)
        else:
            # Calculate distance from initial position
            dx = x - initial_pos[0]
            dz = z - initial_pos[1]
            distance = math.sqrt(dx*dx + dz*dz)
            max_distance = max(max_distance, distance)

        time.sleep(0.2)

    return max_distance if max_distance > 0 else None


def find_end_portal(client, timeout: int = 600) -> Optional[Tuple[int, int, int]]:
    """Locate actual End portal frames and return their center."""
    start = time.time()
    while time.time() - start < timeout:
        response = client.transport.dispatch(
            "find_blocks",
            {
                "blocks": ["minecraft:end_portal_frame", "minecraft:end_portal"],
                "radius": 64,
                "limit": 64,
            },
        )
        blocks = _found(response)
        frames = [
            block for block in blocks
            if block.get("block") == "minecraft:end_portal_frame"
        ]
        if len(frames) >= 12:
            return (
                round(sum(int(block["x"]) for block in frames) / len(frames)),
                round(sum(int(block["y"]) for block in frames) / len(frames)),
                round(sum(int(block["z"]) for block in frames) / len(frames)),
            )
        time.sleep(2)
    return None


def activate_end_portal(client, timeout: int = 60) -> bool:
    """Fill every missing frame and verify active portal blocks in-world."""
    from .inventory import select_item
    from .navigation import goto

    scan = client.transport.dispatch(
        "find_blocks",
        {"blocks": ["minecraft:end_portal_frame"], "radius": 32, "limit": 16},
    )
    frames = _found(scan)
    if len(frames) < 12:
        return False
    for frame in frames:
        x, y, z = int(frame["x"]), int(frame["y"]), int(frame["z"])
        block = _unwrap(client.transport.dispatch("get_block", {"x": x, "y": y, "z": z}))
        properties = block.get("state", {})
        if str(properties.get("eye", "false")).lower() == "true":
            continue
        if not select_item(client, "minecraft:ender_eye"):
            return False
        if not goto(client, x, y, z, timeout=60, tolerance=4.0):
            return False
        client.transport.dispatch("interact_block", {"x": x, "y": y, "z": z})
        verified = _unwrap(
            client.transport.dispatch("get_block", {"x": x, "y": y, "z": z})
        )
        if str(verified.get("state", {}).get("eye", "false")).lower() != "true":
            return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        active = client.transport.dispatch(
            "find_blocks",
            {"blocks": ["minecraft:end_portal"], "radius": 16, "limit": 16},
        )
        if _found(active):
            return True
        time.sleep(1)
    return False


def enter_end_portal(
    client,
    portal: Optional[Sequence[int]] = None,
    timeout: int = 60,
) -> bool:
    """Walk into a verified active portal and confirm The End dimension."""
    from .navigation import goto

    active = _found(
        client.transport.dispatch(
            "find_blocks",
            {"blocks": ["minecraft:end_portal"], "radius": 32, "limit": 16},
        )
    )
    if active:
        target = active[0]
        coords = (int(target["x"]), int(target["y"]), int(target["z"]))
    elif portal is not None and len(portal) == 3:
        coords = tuple(int(value) for value in portal)
    else:
        return False
    if not goto(client, *coords, timeout=60, tolerance=0.5):
        return False
    start = time.time()
    while time.time() - start < timeout:
        state = client.transport.dispatch("get_state", {})
        dimension = state.get("dimension", "")
        if "the_end" in dimension.lower():
            return True
        time.sleep(2)
    return False


def fight_ender_dragon(client, timeout: int = 1200) -> bool:
    """
    Advanced dragon fight logic including crystal destruction and pillar climbing.
    """
    from .navigation import goto
    from .inventory import select_item
    
    print("Beginning Ender Dragon fight sequence...")
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        entities = _entities(
            client.transport.dispatch("get_entities", {"radius": 128})
        )
        crystals = [
            entity for entity in entities
            if entity.get("type") == "minecraft:end_crystal"
        ]
        dragon = next(
            (
                entity for entity in entities
                if entity.get("type") == "minecraft:ender_dragon"
            ),
            None,
        )
        if dragon is None and not crystals:
            exit_portal = _found(
                client.transport.dispatch(
                    "find_blocks",
                    {"blocks": ["minecraft:end_portal"], "radius": 64, "limit": 1},
                )
            )
            if exit_portal:
                return True

        # 1. Deal with crystal entities, never nonexistent crystal blocks.
        if crystals:
            crystal = min(crystals, key=lambda value: float(value.get("distance", 999)))
            crystal_pos = crystal.get("position", {})
            if not all(axis in crystal_pos for axis in ("x", "y", "z")):
                time.sleep(1)
                continue
            coords = (
                float(crystal_pos["x"]),
                float(crystal_pos["y"]),
                float(crystal_pos["z"]),
            )
            print(f"Targeting crystal at {crystal_pos}")
            # Climb if high up
            if coords[1] > 70:
                print("Climbing pillar...")
                # Pillar up or path to top
                goto(client, *coords, tolerance=10)
            
            # Use bow or snowballs if possible
            if select_item(client, "minecraft:bow") or select_item(client, "minecraft:snowball"):
                client.transport.dispatch("look_at", {"x": coords[0], "y": coords[1], "z": coords[2]})
                client.transport.dispatch("use_item", {})
            else:
                # Get close and hit (dangerous)
                goto(client, *coords, tolerance=3)
                client.transport.dispatch(
                    "attack_entity", {"entity_id": int(crystal["id"])}
                )
            continue

        # 2. Attack Dragon
        if dragon:
            dx, dy, dz = dragon["position"]["x"], dragon["position"]["y"], dragon["position"]["z"]
            print(f"Dragon spotted at ({dx}, {dy}, {dz})")
            
            # If perched (at center Y ~64)
            if abs(dx) < 10 and abs(dz) < 10 and dy < 80:
                print("Dragon is perched! Melee attack!")
                goto(client, 0, 64, 0, tolerance=2)
                client.transport.dispatch(
                    "attack_entity", {"entity_id": int(dragon["id"])}
                )
            else:
                # Snipe with bow
                if select_item(client, "minecraft:bow"):
                    client.transport.dispatch("look_at", {"x": dx, "y": dy, "z": dz})
                    client.transport.dispatch("use_item", {})
        
        time.sleep(1)
        
    return False


def traverse_end_gateway(client, timeout: int = 90) -> Optional[Tuple[int, int, int]]:
    """Pearl through an active gateway and verify outer-island displacement."""
    from .inventory import select_item
    from .navigation import goto

    state = _unwrap(client.transport.dispatch("get_state", {}))
    if "the_end" not in str(state.get("dimension", "")).lower():
        return None
    start = _position(state)
    gateways = _found(
        client.transport.dispatch(
            "find_blocks",
            {"blocks": ["minecraft:end_gateway"], "radius": 96, "limit": 20},
        )
    )
    if not gateways or not select_item(client, "minecraft:ender_pearl"):
        return None
    gateway = min(gateways, key=lambda block: float(block.get("distance", 999)))
    coords = (int(gateway["x"]), int(gateway["y"]), int(gateway["z"]))
    if not goto(client, *coords, timeout=180, tolerance=3.0):
        return None
    client.transport.dispatch(
        "look_at", {"x": coords[0] + 0.5, "y": coords[1] + 0.5, "z": coords[2] + 0.5}
    )
    client.transport.dispatch("use_item", {"hand": "MAIN_HAND"})
    deadline = time.time() + timeout
    while time.time() < deadline:
        live = _unwrap(client.transport.dispatch("get_state", {}))
        current = _position(live)
        if all(axis in start and axis in current for axis in ("x", "z")):
            moved = (
                (float(current["x"]) - float(start["x"])) ** 2
                + (float(current["z"]) - float(start["z"])) ** 2
            ) ** 0.5
            if moved >= 256.0:
                return coords
        time.sleep(1)
    return None


def find_end_city(
    client,
    timeout: int = 1800,
    max_distance: float = 4096.0,
) -> Optional[Tuple[int, int, int]]:
    """Explore outer islands until a loaded purpur structure is verified."""
    state = _unwrap(client.transport.dispatch("get_state", {}))
    start = _position(state)
    origin_x = float(start.get("x", 0))
    origin_z = float(start.get("z", 0))
    exploring = False
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            found = _found(
                client.transport.dispatch(
                    "find_blocks",
                    {
                        "blocks": [
                            "minecraft:purpur_block",
                            "minecraft:purpur_pillar",
                            "minecraft:end_stone_bricks",
                        ],
                        "radius": 64,
                        "limit": 256,
                    },
                )
            )
            purpur = [
                block for block in found
                if "purpur" in str(block.get("block", ""))
            ]
            if len(purpur) >= 20:
                return (
                    round(sum(int(block["x"]) for block in purpur) / len(purpur)),
                    round(sum(int(block["y"]) for block in purpur) / len(purpur)),
                    round(sum(int(block["z"]) for block in purpur) / len(purpur)),
                )
            live = _position(_unwrap(client.transport.dispatch("get_state", {})))
            distance = (
                (float(live.get("x", origin_x)) - origin_x) ** 2
                + (float(live.get("z", origin_z)) - origin_z) ** 2
            ) ** 0.5
            if distance > max_distance:
                return None
            if not exploring:
                client.transport.dispatch(
                    "explore", {"x": int(origin_x), "z": int(origin_z)}
                )
                exploring = True
            time.sleep(4)
    finally:
        if exploring:
            client.transport.dispatch("cancel", {})
            client.transport.dispatch("chat", {"message": "#stop"})
    return None


def acquire_shulker_boxes(client, target: int = 5, timeout: int = 1200) -> bool:
    """Collect shells from real shulkers and craft the requested boxes."""
    from .inventory import count_item
    from .combat import hunt_mobs
    from .resources import ensure_supplies

    if count_item(client, "minecraft:shulker_box") >= target:
        return True
    shells_needed = max(0, target * 2 - count_item(client, "minecraft:shulker_shell"))
    if shells_needed:
        hunted = hunt_mobs(
            client,
            mob_types=["shulker"],
            required_loot={"minecraft:shulker_shell": shells_needed},
            search_radius=96,
            timeout=timeout,
            heal_threshold=14.0,
            max_distance_from_origin=256.0,
        )
        if not hunted.success:
            return False
    if not ensure_supplies(client, {"minecraft:chest": target}, timeout=600).success:
        return False
    return ensure_supplies(
        client, {"minecraft:shulker_box": target}, timeout=600
    ).success and count_item(client, "minecraft:shulker_box") >= target


def acquire_elytra(client, timeout: int = 600) -> bool:
    """Break End-ship item frames by entity id until Elytra is collected."""
    from .inventory import count_item
    from .navigation import goto

    if count_item(client, "minecraft:elytra") >= 1:
        return True
    deadline = time.time() + timeout
    attacked = set()
    while time.time() < deadline:
        frames = [
            entity
            for entity in _entities(
                client.transport.dispatch("get_entities", {"radius": 96})
            )
            if entity.get("type") in {"minecraft:item_frame", "minecraft:glow_item_frame"}
            and entity.get("id") not in attacked
        ]
        if not frames:
            return False
        for frame in sorted(frames, key=lambda value: float(value.get("distance", 999))):
            position = frame.get("position", {})
            if not all(axis in position for axis in ("x", "y", "z")):
                continue
            if not goto(
                client,
                int(position["x"]),
                int(position["y"]),
                int(position["z"]),
                timeout=120,
                tolerance=3.0,
            ):
                continue
            attacked.add(frame["id"])
            client.transport.dispatch(
                "attack_entity", {"entity_id": int(frame["id"])}
            )
            time.sleep(2)
            if count_item(client, "minecraft:elytra") >= 1:
                return True
    return False


def return_from_end(client, timeout: int = 180) -> bool:
    """Enter the active central exit portal and verify the Overworld."""
    from .navigation import goto

    portals = _found(
        client.transport.dispatch(
            "find_blocks",
            {"blocks": ["minecraft:end_portal"], "radius": 96, "limit": 16},
        )
    )
    if not portals:
        return False
    target = min(portals, key=lambda block: float(block.get("distance", 999)))
    if not goto(
        client,
        int(target["x"]),
        int(target["y"]),
        int(target["z"]),
        timeout=180,
        tolerance=0.5,
    ):
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        dimension = str(
            _unwrap(client.transport.dispatch("get_state", {})).get("dimension", "")
        ).lower()
        if "overworld" in dimension:
            return True
        time.sleep(1)
    return False
