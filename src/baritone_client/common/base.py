"""
Base building utilities - Shelter, storage, and infrastructure.
"""

import time
from typing import Optional, Tuple
from .navigation import goto, find_nearby_block
from .inventory import count_item, select_item, craft


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
        
        data = state.get("data", {})
        px = int(data.get("x", 0))
        py = int(data.get("y", 64))
        pz = int(data.get("z", 0))
        
        # Start from current position - simple approach
        # Could be enhanced to scan for flatness
        return (px, py, pz)
        
    except Exception:
        return None


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
            client.transport.dispatch("place_block", {"x": x + dx, "y": y + wall_y, "z": z})
            time.sleep(0.2)
        # South wall
        for dx in range(size + 2):
            client.transport.dispatch("place_block", {"x": x + dx, "y": y + wall_y, "z": z + size + 1})
            time.sleep(0.2)
        # West wall
        for dz in range(1, size + 1):
            client.transport.dispatch("place_block", {"x": x, "y": y + wall_y, "z": z + dz})
            time.sleep(0.2)
        # East wall
        for dz in range(1, size + 1):
            client.transport.dispatch("place_block", {"x": x + size + 1, "y": y + wall_y, "z": z + dz})
            time.sleep(0.2)
    
    # Build roof
    for dx in range(size + 2):
        for dz in range(size + 2):
            client.transport.dispatch("place_block", {"x": x + dx, "y": y + 3, "z": z + dz})
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
    
    if not select_item(client, "minecraft:crafting_table"):
        return False
    
    client.transport.dispatch("place_block", {"x": x, "y": y, "z": z})
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


def open_crafting_table(client) -> bool:
    """
    Find and open a nearby crafting table.
    
    Returns:
        True if crafting table opened
    """
    # Find nearby crafting table
    table_pos = find_nearby_block(client, ["crafting_table"], radius=10)
    
    if table_pos is None:
        print("  No crafting table nearby")
        return False
    
    x, y, z = table_pos
    
    # Walk to it
    goto(client, x, y, z, timeout=30, tolerance=2)
    
    # Interact
    client.transport.dispatch("interact_block", {"x": x, "y": y, "z": z})
    time.sleep(0.5)
    
    return True


def open_furnace(client) -> bool:
    """
    Find and open a nearby furnace.
    
    Returns:
        True if furnace opened
    """
    furnace_pos = find_nearby_block(client, ["furnace", "blast_furnace"], radius=10)
    
    if furnace_pos is None:
        print("  No furnace nearby")
        return False
    
    x, y, z = furnace_pos
    
    goto(client, x, y, z, timeout=30, tolerance=2)
    
    client.transport.dispatch("interact_block", {"x": x, "y": y, "z": z})
    time.sleep(0.5)
    
    return True
