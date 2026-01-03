"""
Combat utilities - Mob engagement, retreat logic, and healing.
"""

import time
from typing import Dict, List, Optional

from .inventory import count_item, equip_best_weapon
from .tasks import TaskResult


def get_nearby_entities(client, radius: int = 30) -> List[Dict]:
    """
    Get list of nearby entities.
    
    Returns:
        List of entity dictionaries
    """
    try:
        data = client.transport.dispatch("get_entities", {"radius": radius})
        return data.get("entities", [])
    except Exception:
        return []


def find_entity_by_type(client, entity_types: List[str], radius: int = 30) -> Optional[Dict]:
    """
    Find nearest entity of specified types.
    
    Args:
        entity_types: List of entity type substrings (e.g., ["zombie", "skeleton"])
        radius: Search radius
        
    Returns:
        Entity dict or None
    """
    entities = get_nearby_entities(client, radius)
    
    for entity in sorted(entities, key=lambda e: e.get("distance", 999)):
        entity_type = entity.get("type", "").lower()
        if any(t.lower() in entity_type for t in entity_types):
            return entity
    
    return None


def attack_nearest(
    client,
    entity_types: List[str],
    max_range: int = 10,
) -> bool:
    """
    Attack nearest entity of specified type.
    
    Args:
        client: Baritone client
        entity_types: Types to attack (e.g., ["pig", "cow"])
        max_range: Maximum attack range
        
    Returns:
        True if attacked an entity
    """
    entity = find_entity_by_type(client, entity_types, radius=max_range)
    
    if entity is None:
        return False
    
    entity_id = entity.get("id")
    if entity_id is None:
        return False
        
    # Equip weapon
    equip_best_weapon(client)
    
    try:
        # Look at entity
        client.transport.dispatch("look_at", {"entity_id": entity_id})
        time.sleep(0.2)
        
        # Attack
        client.transport.dispatch("attack_entity", {"entity_id": entity_id})
        return True
        
    except Exception as e:
        print(f"Attack error: {e}")
        return False


def safe_combat(
    client,
    target_id: int,
    retreat_health: float = 6.0,
    max_duration: int = 30,
) -> bool:
    """
    Fight target with retreat logic.
    
    Args:
        client: Baritone client
        target_id: Entity ID to attack
        retreat_health: Retreat if health drops below this
        max_duration: Maximum combat duration
        
    Returns:
        True if target killed, False if retreated or failed
    """
    # Equip weapon
    equip_best_weapon(client)
    
    start = time.time()
    
    last_goto_time = 0
    
    while time.time() - start < max_duration:
        # Check health
        state = client.transport.dispatch("get_state", {})
        health = state.get("health", 20)
        if health < retreat_health:
            print(f"Retreating! Health: {health}")
            client.transport.dispatch("cancel", {})
            return False
        
        # Check if target still exists
        entities = get_nearby_entities(client, radius=30)
        target = next((e for e in entities if e.get("id") == target_id), None)
        
        if target is None:
            # Target dead or escaped
            return True
        
        dist = target.get("distance", 999)
        
        # Attack if in range
        if dist < 4.5:
            client.transport.dispatch("look_at", {"entity_id": target_id})
            client.transport.dispatch("attack_entity", {"entity_id": target_id})
            # Reset pathing if we are close enough to just whack it
            if state.get("is_pathing", False):
                 client.transport.dispatch("chat", {"message": "#stop"})
        else:
            # Move closer
            # Only sending goto if we aren't pathing OR if it's been a while (update target pos)
            is_pathing = state.get("is_pathing", False)
            
            if not is_pathing or (time.time() - last_goto_time > 1.0):
                tx, ty, tz = int(target.get("x", 0)), int(target.get("y", 0)), int(target.get("z", 0))
                client.transport.dispatch("goto", {"x": tx, "y": ty, "z": tz})
                last_goto_time = time.time()
                
            # Fail fast if stuck not pathing for too long
            if not is_pathing and time.time() - last_goto_time > 3.0:
                 # We tried to go to it 3 seconds ago and still aren't pathing?
                 print("DEBUG: Baritone failed to path to target (Stuck?)")
                 client.transport.dispatch("cancel", {})
                 return False

        time.sleep(0.2)
    
    return False


def heal_if_needed(client, threshold: float = 10.0) -> bool:
    """
    Eat food if health below threshold.
    
    Args:
        client: Baritone client
        threshold: Health threshold to trigger healing
        
    Returns:
        True if healing was attempted
    """
    # Check health
    state = client.transport.dispatch("get_state", {})
    health = state.get("health", 20)
    if health >= threshold:
        return False
    
    # Find food in inventory
    food_items = [
        "minecraft:cooked_beef",
        "minecraft:cooked_porkchop", 
        "minecraft:cooked_chicken",
        "minecraft:cooked_mutton",
        "minecraft:bread",
        "minecraft:apple",
        "minecraft:cooked_salmon",
        "minecraft:cooked_cod",
        "minecraft:baked_potato",
        "minecraft:golden_apple",
    ]
    
    inventory = client.transport.dispatch("get_inventory", {})
    
    for item in inventory.get("inventory", []):
        item_id = item.get("id", "")
        if item_id in food_items:
            slot = item.get("slot")
            if slot is not None and 0 <= slot <= 8:
                # Select and eat
                client.transport.dispatch("select_slot", {"slot": slot})
                time.sleep(0.1)
                client.transport.dispatch("use_item", {"duration_ms": 2000})
                return True
    
    return False


def hunt_passive_mobs(
    client,
    target_count: int = 10,
    timeout: int = 300,
) -> int:
    """
    Hunt passive mobs for food.
    
    Args:
        client: Baritone client
        target_count: Number of mobs to kill
        timeout: Maximum time
        
    Returns:
        Number of mobs killed
    """
    result = hunt_mobs(
        client,
        mob_types=["pig", "cow", "sheep", "chicken"],
        required_loot={"minecraft:cooked_beef": 0},
        search_radius=50,
        timeout=timeout,
        heal_threshold=5.0,
        target_kills=target_count,
    )
    return int(result.data.get("kills", 0))


def hunt_mobs(
    client,
    mob_types: List[str],
    required_loot: Dict[str, int],
    search_radius: int = 48,
    timeout: int = 600,
    heal_threshold: float = 8.0,
    target_kills: Optional[int] = None,
) -> TaskResult:
    """
    Hunt a set of mobs until loot requirements are satisfied.

    Args:
        mob_types: Substrings of entity types to target
        required_loot: Dict of item_id -> count
        search_radius: Radius to scan for mobs
        timeout: Maximum duration
        heal_threshold: Auto-heal when health drops below this value
        target_kills: Optional kill target to stop early even if loot collected
    """
    start = time.time()
    baseline = {item: count_item(client, item) for item in required_loot}
    kills = 0

    def _missing() -> Dict[str, int]:
        missing: Dict[str, int] = {}
        for item_id, count in required_loot.items():
            current = count_item(client, item_id) - baseline.get(item_id, 0)
            if current < count:
                missing[item_id] = count - max(current, 0)
        return missing

    missing = _missing()
    if not missing and not target_kills:
        return TaskResult.ok("Already satisfied", kills=kills, missing={})

    exploring = False
    last_time_check = 0
    
    while (missing or (target_kills and kills < target_kills)) and time.time() - start < timeout:
        # Check for night every 10 seconds
        if time.time() - last_time_check > 10:
            last_time_check = time.time()
            state = client.transport.dispatch("get_state", {})
            if state.get("world_time", 0) % 24000 >= 13000:
                 print("  Night detected! Aborting hunt.")
                 if exploring:
                      client.transport.dispatch("chat", {"message": "#stop"})
                 return TaskResult.fail("Night detected")

        heal_if_needed(client, threshold=heal_threshold)
        entity = find_entity_by_type(client, mob_types, radius=search_radius)
        
        if entity is None:
            if not exploring:
                print("  No targets found, starting exploration...")
                # Dispatch explore command (using chat command via API or strict explore)
                # Since handleExplore expects X/Z, providing current or origin is common.
                # But #explore works best.
                client.transport.dispatch("chat", {"message": "#explore"})
                exploring = True
            time.sleep(3)
            continue

        if exploring:
             print("  Target found! Stopping exploration.")
             client.transport.dispatch("chat", {"message": "#stop"})
             exploring = False
             time.sleep(0.5)

        target_id = entity.get("id")
        if target_id is None:
            time.sleep(1)
            continue
            
        if safe_combat(client, target_id, retreat_health=heal_threshold - 2, max_duration=40):
            kills += 1
            missing = _missing()
        else:
            heal_if_needed(client, threshold=heal_threshold)
        time.sleep(1)

    gained = {
        item: max(0, count_item(client, item) - baseline.get(item, 0))
        for item in required_loot
    }

    if missing:
        return TaskResult.fail(
            "Failed to acquire required loot",
            missing=missing,
            kills=kills,
            gained=gained,
            duration=time.time() - start,
        )

    return TaskResult.ok(
        "Loot collection complete",
        missing={},
        kills=kills,
        gained=gained,
        duration=time.time() - start,
    )


def scan_for_threats(client, radius: int = 16) -> List[Dict]:
    """Return list of nearby hostile mobs sorted by distance."""
    hostiles = ["zombie", "skeleton", "creeper", "spider", "witch", "pillager", "enderman", "slime"]
    
    nearby = get_nearby_entities(client, radius)
    threats = []
    
    for entity in nearby:
        etype = entity.get("type", "").lower()
        if any(h in etype for h in hostiles):
            threats.append(entity)
            
    return sorted(threats, key=lambda e: e.get("distance", 999))


def run_away(client, threat: Dict):
    """Run away from a specific threat."""
    try:
        t_pos = (threat.get("x", 0), threat.get("z", 0))
        
        # Get our pos
        state = client.transport.dispatch("get_state", {})
        pos = state.get("block_position", {})
        o_x, o_z = int(pos.get("x", 0)), int(pos.get("z", 0))
        
        # Vector away
        dx = o_x - t_pos[0]
        dz = o_z - t_pos[1]
        
        # Normalize roughly
        dist = (dx*dx + dz*dz)**0.5
        if dist < 0.1: dist = 1 # Avoid div by zero
        
        # Target 25 blocks away
        target_dist = 25
        tx = int(o_x + (dx/dist) * target_dist)
        tz = int(o_z + (dz/dist) * target_dist)
        
        print(f"FLEE: Running to {tx}, {o_z} (Away from {t_pos})")
        client.transport.dispatch("goal", {"x": tx, "y": int(pos.get("y", 64)), "z": tz})
        client.transport.dispatch("chat", {"message": "#path"})
        time.sleep(1) # Let it start
        
    except Exception as e:
        print(f"Run away failed: {e}")


def defend_or_flee(client) -> bool:
    """
    Check surroundings. If threat:
    - If weapon: Attack
    - If no weapon: Flee
    
    Returns: True if action taken (was interrupted)
    """
    threats = scan_for_threats(client)
    if not threats:
        return False
        
    closest = threats[0]
    dist = closest.get("distance", 999)
    if dist > 10:
        return False # Too far to worry yet
        
    print(f"DEFENSE: Threat detected! {closest.get('type')} at {dist:.1f}m")
    
    # Are we equipped?
    # Simple check: do we have a sword or axe in hotbar/inventory?
    # equip_best_weapon does the checking and equipping.
    has_weapon = equip_best_weapon(client)
    
    if has_weapon:
        print("DEFENSE: Engels mode engaged. Attacking.")
        client.transport.dispatch("chat", {"message": "#stop"}) # Stop mining
        safe_combat(client, closest.get("id"), retreat_health=6.0)
        return True
    else:
        print("DEFENSE: No weapon! FLEE!")
        client.transport.dispatch("chat", {"message": "#stop"})
        run_away(client, closest)
        time.sleep(5) # Run for a bit
        return True
