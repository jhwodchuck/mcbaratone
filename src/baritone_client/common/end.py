"""
End-dimension helper routines built on top of mission macros.
"""

import logging
import time
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def _get_eye_direction(client) -> Optional[float]:
    """Poll for a thrown eye and return its horizontal direction in radians."""
    import math
    start = time.time()
    while time.time() - start < 5.0:
        entities = client.transport.dispatch("get_entities", {"radius": 64})
        for ent in entities.get("entities", []):
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
    p1 = state1.get("position", {})
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
    p2 = state2.get("position", {})
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


def find_end_portal(client, timeout: int = 600) -> bool:
    """
    Placeholder portal search loop. Relies on the stronghold macro and polls for
    phase advancement.
    """
    start = time.time()
    while time.time() - start < timeout:
        status = client.mission.status()
        mission = status.get("mission", {})
        phase = mission.get("phase", "")
        if phase == "stronghold_hunt":
            return True
        time.sleep(5)
    return False


def activate_end_portal(client) -> bool:
    """
    Activation is handled on the bridge/macros for now; this placeholder simply
    records the checkpoint.
    """
    client.mission.checkpoint("end_portal", "Portal assumed activated")
    return True


def enter_end_portal(client, timeout: int = 60) -> bool:
    """Wait for the dimension to become The End."""
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
    from .navigation import goto, find_nearby_block
    from .inventory import select_item
    
    print("Beginning Ender Dragon fight sequence...")
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        # Check if dragon is dead
        status = client.mission.status()
        if status.get("mission", {}).get("note") == "dragon_defeated":
            return True
            
        # 1. Deal with Crystals
        crystal_pos = find_nearby_block(client, ["minecraft:end_crystal"], radius=128)
        if crystal_pos:
            print(f"Targeting crystal at {crystal_pos}")
            # Climb if high up
            if crystal_pos[1] > 70:
                print("Climbing pillar...")
                # Pillar up or path to top
                goto(client, crystal_pos[0], crystal_pos[1], crystal_pos[2], tolerance=10)
            
            # Use bow or snowballs if possible
            if select_item(client, "minecraft:bow") or select_item(client, "minecraft:snowball"):
                client.transport.dispatch("look_at", {"x": crystal_pos[0], "y": crystal_pos[1], "z": crystal_pos[2]})
                client.transport.dispatch("use_item", {})
            else:
                # Get close and hit (dangerous)
                goto(client, *crystal_pos, tolerance=3)
                client.transport.dispatch("attack_entity", {"id": "minecraft:end_crystal"})
            continue

        # 2. Attack Dragon
        # Look for the dragon entity
        entities = client.transport.dispatch("get_entities", {"radius": 128})
        dragon = next((e for e in entities.get("entities", []) if e.get("id") == "minecraft:ender_dragon"), None)
        
        if dragon:
            dx, dy, dz = dragon["position"]["x"], dragon["position"]["y"], dragon["position"]["z"]
            print(f"Dragon spotted at ({dx}, {dy}, {dz})")
            
            # If perched (at center Y ~64)
            if abs(dx) < 10 and abs(dz) < 10 and dy < 80:
                print("Dragon is perched! Melee attack!")
                goto(client, 0, 64, 0, tolerance=2)
                client.transport.dispatch("attack_entity", {"id": "minecraft:ender_dragon"})
            else:
                # Snipe with bow
                if select_item(client, "minecraft:bow"):
                    client.transport.dispatch("look_at", {"x": dx, "y": dy, "z": dz})
                    client.transport.dispatch("use_item", {})
        
        time.sleep(1)
        
    return False
