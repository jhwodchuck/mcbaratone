"""
Base building utilities - Shelter, storage, and infrastructure.
"""

import time
from typing import Optional, Tuple
from .navigation import goto, find_nearby_block
from .automation_utils import get_player_pos
from .inventory import count_item, select_item, craft


def is_position_safe(client, x: int, y: int, z: int) -> bool:
    """Check if a position is safe for placement (not liquid)."""
    try:
        # Scan area around player to check for liquids
        # Radius 8 covers typical reach distance
        res = client.transport.dispatch("get_view", {"radius": 8})
        voxels = res.get("voxels", [])
        
        for v in voxels:
            if v["x"] == x and v["y"] == y and v["z"] == z:
                # Found the block at target position
                block_id = v.get("id", "")
                if "water" in block_id or "lava" in block_id:
                    print(f"  Target ({x}, {y}, {z}) is liquid: {block_id}")
                    return False
        return True
    except Exception as e:
        print(f"  Safety check error: {e}")
        return True



def find_flat_ground(client, radius: int = 20) -> Optional[Tuple[int, int, int]]:
    """
    Find a flat area suitable for building a base.
    
    Args:
        client: Baritone client
        radius: Search radius
        
    Returns:
        (x, y, z) of suitable location or None
    """
    try:
        state = client.transport.dispatch("get_state", {})
        if state.get("status") != "ok":
            return None
        
        pos = state.get("position", {})
        px = int(pos.get("x", 0))
        py = int(pos.get("y", 64))
        pz = int(pos.get("z", 0))
        
        # Start from current position - simple approach
        # Could be enhanced to scan for flatness
        return (px, py, pz)
        
    except Exception:
        return None


def safe_place_block(client, x, y, z, max_depth=2) -> bool:
    """
    Attempt to place a block, adding support if needed.
    """
    try:
        client.transport.dispatch("place_block", {"x": x, "y": y, "z": z})
        return True
    except Exception as e:
        msg = str(e)
        if "No solid block found to place against" in msg and max_depth > 0:
            print(f"  Placing support block at ({x}, {y-1}, {z})...")
            # Recursive call with depth limit
            if safe_place_block(client, x, y-1, z, max_depth-1):
                time.sleep(0.2)
                try:
                    client.transport.dispatch("place_block", {"x": x, "y": y, "z": z})
                    return True
                except Exception:
                    pass
        elif "Target position is already occupied" in msg:
             return True
        
        # Only print non-routine errors to avoid spam
        if "No solid block" not in msg:
            print(f"  Place block failed at ({x}, {y}, {z}): {msg}")
        return False


def build_dirt_shelter(
    client,
    x: int,
    y: int,
    z: int,
    size: int = 3,
) -> bool:
    """
    Build a basic dirt shelter (walls and roof).
    
    Args:
        client: Baritone client
        x, y, z: Base corner position
        size: Interior size
        
    Returns:
        True if shelter built
    """
    # Check for dirt/cobblestone blocks
    dirt_count = count_item(client, "minecraft:dirt")
    cobble_count = count_item(client, "minecraft:cobblestone")
    
    if dirt_count + cobble_count < 30:
        print(f"  Need 30+ blocks for shelter, have {dirt_count + cobble_count}")
        return False
    
    # Select building material
    if cobble_count >= 30:
        if not select_item(client, "minecraft:cobblestone"):
            return False
    elif not select_item(client, "minecraft:dirt"):
        return False
    
    # Build walls (simplified - 4 walls, 3 high)
    for wall_y in range(3):
        # North wall
        for dx in range(size + 2):
            safe_place_block(client, x + dx, y + wall_y, z)
            time.sleep(0.2)
        # South wall
        for dx in range(size + 2):
            safe_place_block(client, x + dx, y + wall_y, z + size + 1)
            time.sleep(0.2)
        # West wall
        for dz in range(1, size + 1):
            safe_place_block(client, x, y + wall_y, z + dz)
            time.sleep(0.2)
        # East wall
        for dz in range(1, size + 1):
            safe_place_block(client, x + size + 1, y + wall_y, z + dz)
            time.sleep(0.2)
    
    # Build roof
    for dx in range(size + 2):
        for dz in range(size + 2):
            safe_place_block(client, x + dx, y + 3, z + dz)
            time.sleep(0.2)
    
    return True


def place_crafting_table(client, x: int, y: int, z: int) -> bool:
    """
    Place a crafting table at specified location.
    
    Returns:
        True if placed
    """
    if count_item(client, "minecraft:crafting_table") < 1:
        # Try to craft one
        if count_item(client, "minecraft:oak_planks") >= 4:
            craft(client, "minecraft:crafting_table", 1)
            time.sleep(0.5)
        else:
            print("  Need crafting table or 4 planks")
            return False
    
    if not select_item(client, "minecraft:crafting_table", allow_swap=True):
        return False
    
    if not is_position_safe(client, x, y, z):
        print(f"  Skipping placement at ({x}, {y}, {z}) - Target is liquid")
        return False

    try:
        client.transport.dispatch("place_block", {"x": x, "y": y, "z": z})
    except Exception as e:
        print(f"  Placement failed: {e}")
        return False
        
    time.sleep(0.5)
    return True


def place_furnace(client, x: int, y: int, z: int) -> bool:
    """
    Place a furnace at specified location.
    
    Returns:
        True if placed
    """
    if count_item(client, "minecraft:furnace") < 1:
        # Try to craft one
        if count_item(client, "minecraft:cobblestone") >= 8:
            craft(client, "minecraft:furnace", 1)
            time.sleep(0.5)
        else:
            print("  Need furnace or 8 cobblestone")
            return False
    
    if not select_item(client, "minecraft:furnace"):
        return False
    
    if not is_position_safe(client, x, y, z):
        print(f"  Skipping placement at ({x}, {y}, {z}) - Target is liquid")
        return False

    client.transport.dispatch("place_block", {"x": x, "y": y, "z": z})
    time.sleep(0.5)
    
    return True


def place_chest(client, x: int, y: int, z: int) -> bool:
    """
    Place a chest at specified location.
    
    Returns:
        True if placed
    """
    if count_item(client, "minecraft:chest") < 1:
        # Try to craft one
        if count_item(client, "minecraft:oak_planks") >= 8:
            craft(client, "minecraft:chest", 1)
            time.sleep(0.5)
        else:
            print("  Need chest or 8 planks")
            return False
    
    if not select_item(client, "minecraft:chest"):
        return False
    
    if not is_position_safe(client, x, y, z):
        print(f"  Skipping placement at ({x}, {y}, {z}) - Target is liquid")
        return False

    client.transport.dispatch("place_block", {"x": x, "y": y, "z": z})
    time.sleep(0.5)
    
    return True


def place_bed(client, x: int, y: int, z: int) -> bool:
    """
    Place a bed at specified location.
    
    Returns:
        True if placed
    """
    # Check for any bed color
    bed_types = [
        "minecraft:white_bed", "minecraft:red_bed", "minecraft:blue_bed",
        "minecraft:green_bed", "minecraft:black_bed", "minecraft:yellow_bed",
        "minecraft:orange_bed", "minecraft:pink_bed", "minecraft:purple_bed",
    ]
    
    has_bed = False
    for bed in bed_types:
        if count_item(client, bed) > 0:
            if select_item(client, bed):
                has_bed = True
                break
    
    if not has_bed:
        print("  Need a bed (craft from wool + planks)")
        return False
    
    if not is_position_safe(client, x, y, z):
        print(f"  Skipping placement at ({x}, {y}, {z}) - Target is liquid")
        return False

    client.transport.dispatch("place_block", {"x": x, "y": y, "z": z})
    time.sleep(0.5)
    
    return True


def setup_base(
    client,
    location: Optional[Tuple[int, int, int]] = None,
) -> Tuple[bool, Optional[Tuple[int, int, int]]]:
    """
    Set up a complete base with shelter, storage, and crafting.
    
    Args:
        client: Baritone client
        location: Optional specific location, else auto-find
        
    Returns:
        (success, base_coordinates)
    """
    if location is None:
        location = find_flat_ground(client)
        if location is None:
            return (False, None)
    
    x, y, z = location
    
    print(f"  Setting up base at ({x}, {y}, {z})")
    
    # Place essential blocks inside base area
    placed_crafting = place_crafting_table(client, x + 1, y, z + 1)
    placed_furnace = place_furnace(client, x + 2, y, z + 1)
    placed_chest = place_chest(client, x + 1, y, z + 2)
    
    # Optional: try to place bed
    placed_bed = place_bed(client, x + 2, y, z + 2)
    
    if placed_crafting and placed_furnace:
        print("  Base setup complete!")
        return (True, location)
    else:
        print("  Base setup incomplete - missing crafting table or furnace")
        return (False, location)


def open_crafting_table(client, x: Optional[int] = None, y: Optional[int] = None, z: Optional[int] = None) -> bool:
    """
    Find and open a nearby crafting table.
    If coordinates are provided, it will use those.
    
    Returns:
        True if crafting table opened
    """
    if x is not None and y is not None and z is not None:
        table_pos = (x, y, z)
    else:
        # Find nearby crafting table
        table_pos = find_nearby_block(client, ["minecraft:crafting_table"], radius=20)
    
    if table_pos is None:
        print("  No crafting table nearby")
        return False
    
    tx, ty, tz = table_pos
    
    # Walk to it
    goto(client, tx, ty, tz, timeout=30, tolerance=2)
    time.sleep(0.3)  # Small delay before interaction
    
    # Look at the block first
    try:
        client.transport.dispatch("look_at", {"x": tx, "y": ty, "z": tz})
        time.sleep(0.2)
    except Exception:
        pass  # look_at might not be implemented
    
    # Interact
    client.transport.dispatch("interact_block", {"x": tx, "y": ty, "z": tz})
    
    # Wait for screen - Fabric uses obfuscated class names, so check for any non-'none' screen
    # since we just interacted with a crafting table, any screen opening is likely it
    print(f"  Waiting for crafting table screen to open...")
    for attempt in range(20):  # Increased attempts
        time.sleep(0.2)
        try:
            state = client.transport.dispatch("get_state", {}, timeout=0.5)
            screen = state.get("screen", "none")
            
            # Check for crafting-related screen names (works with both mapped and obfuscated)
            if screen != "none" and screen != "":
                # In Fabric, crafting screen might be CraftingScreen, class_XXX, or similar
                # Any non-empty screen after clicking a crafting table is likely correct
                if "craft" in screen.lower() or screen.startswith("class_"):
                    print(f"  Detected crafting screen: {screen}")
                    time.sleep(0.3)  # Extra buffer for screen to be ready
                    return True
        except Exception as e:
            print(f"  State check error: {e}")
            continue
    
    print(f"  Timed out waiting for crafting table screen")
    return False


def open_furnace(client) -> bool:
    """
    Find and open a nearby furnace.
    
    Returns:
        True if furnace opened
    """
    furnace_pos = find_nearby_block(client, ["minecraft:furnace", "minecraft:blast_furnace"], radius=20)
    
    if furnace_pos is None:
        print("  No furnace nearby")
        return False
    
    x, y, z = furnace_pos
    
    goto(client, x, y, z, timeout=30, tolerance=2)
    
    client.transport.dispatch("interact_block", {"x": x, "y": y, "z": z})
    time.sleep(0.5)
    
    return True


def sleep_through_night(client, timeout: int = 30) -> bool:
    """
    Attempt to place a bed, sleep, and then retrieve the bed.
    Checks time/weather to see if sleep is possible/needed.
    """
    try:
        # Check time/weather
        state = client.transport.dispatch("get_state", {})
        world_time = state.get("world_time", 0)
        is_raining = state.get("is_raining", False)
        
        # Minecraft Night is roughly 13000 to 23000
        is_night = (world_time % 24000) >= 12542
        
        if not (is_night or is_raining):
            return True # Not needed
            
        print(f"Night/Rain detected (time={world_time%24000}). Attempting to sleep...")
        
        # Check for bed
        bed_types = [
            "minecraft:white_bed", "minecraft:red_bed", "minecraft:blue_bed",
            "minecraft:green_bed", "minecraft:black_bed", "minecraft:yellow_bed",
            "minecraft:orange_bed", "minecraft:pink_bed", "minecraft:purple_bed",
            # Add others if needed
        ]
        has_bed = False
        for bed in bed_types:
            if count_item(client, bed) > 0:
                has_bed = True
                break
        
        if not has_bed:
            print("No bed to sleep in!")
            return False
            
        # Find flat spot
        pos = find_flat_ground(client, radius=10)
        if not pos:
            # Try current pos
            pos = get_player_pos(client)
            if not pos:
                return False
            x, y, z = int(pos[0]), int(pos[1]), int(pos[2])
            pos = (x, y, z)
            
        x, y, z = pos
        # Place bed slightly offset to avoid suffocating or overlapping
        # x+1, z+1
        bx, by, bz = x+1, y, z+1 # Simple offset
        
        print(f"Placing bed at {bx}, {by}, {bz}")
        client.transport.dispatch("place_block", {"x": bx, "y": by, "z": bz}) # This might need specific bed item selection handled by place_bed?
        # place_bed handles selection!
        if not place_bed(client, bx, by, bz):
            print("Failed to place bed")
            return False
            
        # Sleep
        print("Sleeping...")
        client.transport.dispatch("interact_block", {"x": bx, "y": by, "z": bz})
        
        # Wait for morning
        # We can wait for time to change or just fixed delay
        time.sleep(10) 
        
        # Verify it's morning? 
        state = client.transport.dispatch("get_state", {})
        new_time = state.get("world_time", 0)
        if (new_time % 24000) < 1000:
            print("Woke up! Morning.")
        
        # Retrieve bed
        print("Mining bed to retrieve it...")
        client.transport.dispatch("mine", {"blocks": bed_types, "quantity": 1})
        time.sleep(3) # Wait for mine
        client.transport.dispatch("cancel", {})
        
        # Pickup item? (Auto-pickup usually works if close)
        return True
        
    except Exception as e:
        print(f"Sleep error: {e}")
        return False


def build_emergency_shelter(client) -> bool:
    """
    Build a quick 1x2x1 hole or dirt hut to survive the night.
    Strategy: Dig 3 blocks down and place a block above head.
    """
    try:
        print("Building EMERGENCY SHELTER!")
        # 1. Dig down 3 blocks
        pos = get_player_pos(client)
        x, y, z = int(pos[0]), int(pos[1]), int(pos[2])
        
        # Dig under feet
        client.transport.dispatch("mine", {"blocks": ["minecraft:dirt", "minecraft:grass_block", "minecraft:stone"], "quantity": 1})
        time.sleep(2)
        # Drop down? Baritone might resist. simpler is to build UP walls.
        # Let's simple "surround" via blocks.
        
        # New Strategy: 3x3x3 dirt box around player.
        # Needs blocks.
        if count_item(client, "minecraft:dirt") < 20 and count_item(client, "minecraft:cobblestone") < 20:
             print("Not enough blocks for shelter. Digging down...")
             # Just dig a hole and stay in it?
             client.transport.dispatch("mine", {"x": x, "y": y-1, "z": z, "quantity": 1})
             time.sleep(1)
             client.transport.dispatch("mine", {"x": x, "y": y-2, "z": z, "quantity": 1})
             time.sleep(1)
             client.transport.dispatch("mine", {"x": x, "y": y-3, "z": z, "quantity": 1})
             time.sleep(1)
             client.transport.dispatch("goto", {"x": x, "y": y-3, "z": z})
             time.sleep(2)
             # Cover top
             client.transport.dispatch("place_block", {"x": x, "y": y, "z": z})
             return True
             
        # Build box
        return build_dirt_shelter(client, x-1, y, z-1, size=1)
        
    except Exception as e:
        print(f"Emergency shelter failed: {e}")
        return False


def build_good_house(client, x: int, y: int, z: int) -> bool:
    """
    Build a nicer house using planks and cobblestone.
    Size: 7x7 outer dimensions, 4 high.
    Materials: Cobble floor, Plank walls, Glass windows if possible.
    """
    try:
        size = 7
        height = 4
        
        # Needs materials
        wood_needed = (size * 4 * height) // 4 # Roughly wall blocks
        cobble_needed = size * size # Floor
        
        current_planks = count_item(client, "minecraft:oak_planks")
        if current_planks < 64:
             print("Gathering wood for good house...")
             # Need roughly 20 logs
             from .resources import gather_wood
             gather_wood(client, count=32)
             craft(client, "minecraft:oak_planks", 32)
             
        current_cobble = count_item(client, "minecraft:cobblestone")
        if current_cobble < 64:
             print("Gathering stone for good house...")
             from .resources import gather_stone
             gather_stone(client, count=64)

        # 1. Floor (Cobble)
        # Dig out floor area?? Or just place on top. Assume on top of flat ground.
        print("Building Good House Floor...")
        for dx in range(size):
            for dz in range(size):
                if not select_item(client, "minecraft:cobblestone"):
                    break # Failed
                client.transport.dispatch("place_block", {"x": x+dx, "y": y, "z": z+dz})
                time.sleep(0.1)
                
        # 2. Walls (Planks)
        # Corners can be logs if we had them? Let's stick to planks.
        print("Building Good House Walls...")
        if not select_item(client, "minecraft:oak_planks"):
             select_item(client, "minecraft:birch_planks") # Try others
             
        for dy in range(1, height):
           # Outer perimeter
           for dx in range(size):
               # North/South
               client.transport.dispatch("place_block", {"x": x+dx, "y": y+dy, "z": z})
               client.transport.dispatch("place_block", {"x": x+dx, "y": y+dy, "z": z+size-1})
               # East/West (exclude corners to avoid double place)
           for dz in range(1, size-1):
               client.transport.dispatch("place_block", {"x": x, "y": y+dy, "z": z+dz})
               client.transport.dispatch("place_block", {"x": x+size-1, "y": y+dy, "z": z+dz})
           time.sleep(0.5)

        # 3. Roof (Cobble or Wood)
        print("Building Roof...")
        select_item(client, "minecraft:oak_planks")
        for dx in range(size):
            for dz in range(size):
                client.transport.dispatch("place_block", {"x": x+dx, "y": y+height, "z": z+dz})
                
        # 4. Door and Torch
        # Leave a hole for door?
        # Place door at x+size//2, y, z
        door_x, door_z = x + size // 2, z
        
        # Clear blocks at door pos (if any)
        client.transport.dispatch("mine", {"x": door_x, "y": y+1, "z": door_z, "quantity": 1})
        client.transport.dispatch("mine", {"x": door_x, "y": y+2, "z": door_z, "quantity": 1})
        time.sleep(1)

        # Place Door
        door_types = [
            "minecraft:oak_door", "minecraft:spruce_door", "minecraft:birch_door", 
            "minecraft:jungle_door", "minecraft:acacia_door", "minecraft:dark_oak_door",
            "minecraft:crimson_door", "minecraft:warped_door"
        ]
        
        has_door = False
        for door in door_types:
            if count_item(client, door) > 0:
                if select_item(client, door):
                    has_door = True
                    break
        
        if not has_door:
             # Try craft door
             if count_item(client, "minecraft:oak_planks") >= 6:
                 craft(client, "minecraft:oak_door", 3)
                 select_item(client, "minecraft:oak_door")
                 has_door = True
        
        if has_door:
            print("Placing Door...")
            # Place bottom half
            client.transport.dispatch("place_block", {"x": door_x, "y": y+1, "z": door_z})
            time.sleep(0.5)
        else:
             print("No door available")

        print("Good House Complete!")
        return True
        
    except Exception as e:
        print(f"Good house build failed: {e}")
        return False


def open_chest(client, x: int, y: int, z: int) -> bool:
    """Open a chest at specific coordinates."""
    # Look at it first
    client.transport.dispatch("look_at", {"x": x, "y": y, "z": z})
    time.sleep(0.3)
    
    client.transport.dispatch("interact_block", {"x": x, "y": y, "z": z})
    
    # Wait for screen to open
    print(f"  Waiting for chest screen to open...")
    for i in range(15):
        state = client.transport.dispatch("get_state", {})
        screen = state.get("screen", "none")
        # Accept any screen that's not 'none' as it likely means a container opened
        # Fabric obfuscates screen names, so we can't rely on specific names
        if screen != "none":
            print(f"  Detected screen: {screen}")
            time.sleep(0.5)  # Wait a bit for screen to be fully ready
            return True
        time.sleep(0.2)
    
    print(f"  Timed out waiting for chest screen")
    return False


def loot_nearby_chests(client, radius: int = 16) -> bool:
    """Search for and loot all nearby chests."""
    print(f"Searching for nearby chests (radius {radius})...")
    res = client.transport.dispatch("find_blocks", {
        "blocks": ["minecraft:chest", "minecraft:trapped_chest", "minecraft:barrel"],
        "radius": radius,
        "limit": 5
    })
    
    found = res.get("found", [])
    if not found:
        print("  No chests found nearby.")
        return False
        
    print(f"  Found {len(found)} chests. Looting...")
    
    for c in found:
        tx, ty, tz = c['x'], c['y'], c['z']
        print(f"  Walking to chest at ({tx}, {ty}, {tz})...")
        goto(client, tx, ty, tz, tolerance=2)
        
        if open_chest(client, tx, ty, tz):
            print(f"  Looting chest...")
            # Chests usually have 27 slots (0-26) or 54 for double (0-53)
            # We'll try to loot up to 54 slots
            for slot in range(54):
                # Shift-click all slots to move items to inventory
                client.transport.dispatch("inventory_click", {
                    "slot": slot,
                    "button": 0,
                    "type": "QUICK_MOVE"
                })
                # Micro-delay to avoid overwhelming server/bridge
                # but fast enough to loot quickly
                if slot % 9 == 0: time.sleep(0.1) 
            
            # Close screen
            client.transport.dispatch("close_screen", {})
            time.sleep(0.3)
            
    return True

