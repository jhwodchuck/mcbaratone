"""
Unified Block Operations Module

Consolidated block operations for functional tests.
Provides functions for block placement, detection, movement, and fill operations.

This module was refactored to unify operations from block_ops.py and block_ops_shared.py
into a single shared module. It provides survival-safe placement, chunked fills for large areas,
and movement utilities used across all extended test suites.

Key functions:
- Block detection: block_id_at, is_liquid, in_range
- Movement: move_near, find_stand_pos, find_place_pos_near
- Placement: place_block_at, bot_place_block
- Fill operations: fill_plane_chunked, fill_volume_chunked, bot_box_fill
- Structure building: bot_build_hollow_box, build_simple_structure
"""

import time
from typing import Dict, List, Optional, Set, Tuple, Union

from baritone_client.common.inventory import select_item
from tests.utils.mc_harness.waits import cancel_pathing, wait_for_pathing_stop, wait_for_position_stable

# Constants
BASE_Y = 80



def get_inv_slots(data: Dict) -> List:
    """Normalize access to inventory slots from various event formats.

    Args:
        data: Inventory data dictionary from events.

    Returns:
        List of inventory slots.
    """
    return data.get("slots") or data.get("inventory") or []


def block_id_at(ctx, x: Union[int, float], y: Union[int, float], z: Union[int, float]) -> str:
    """Get block ID at coordinates.

    Args:
        ctx: Test context.
        x: X coordinate.
        y: Y coordinate.
        z: Z coordinate.

    Returns:
        Block ID string.
    """
    block = ctx.get_block(int(x), int(y), int(z))
    data = block.get("data", block)
    return data.get("id", "")


def is_liquid(block_id: str) -> bool:
    """Check if block is a liquid.

    Args:
        block_id: Block ID string.

    Returns:
        True if the block is a liquid.
    """
    return "water" in block_id or "lava" in block_id or "bubble_column" in block_id


def in_range(ctx, x: Union[int, float], y: Union[int, float], z: Union[int, float], max_dist: float = 4.5) -> bool:
    """Check if position is within interaction range.

    Args:
        ctx: Test context.
        x: X coordinate.
        y: Y coordinate.
        z: Z coordinate.
        max_dist: Maximum distance.

    Returns:
        True if within range.
    """
    px, py, pz = ctx.get_position()
    dx = px - x
    dy = py - y
    dz = pz - z
    return (dx * dx + dy * dy + dz * dz) ** 0.5 <= max_dist


def find_stand_pos(ctx, x: Union[int, float], y: Union[int, float], z: Union[int, float], radius: int = 3) -> Tuple[int, int, int]:
    """Find a suitable standing position near coordinates.

    Args:
        ctx: Test context.
        x: X coordinate.
        y: Y coordinate.
        z: Z coordinate.
        radius: Search radius.

    Returns:
        Tuple of (x, y, z) coordinates for standing.
    """
    offsets = []
    for dy in (0, 1, -1, 2): # Try standing a bit higher too
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                # Avoid standing exactly in the target block OR directly above/below it
                if abs(dx) < 1 and abs(dz) < 1:
                    continue
                offsets.append((dx, dy, dz))
    
    # Sort by horizontal distance, aiming for ~2 blocks away
    offsets.sort(key=lambda o: (abs(2.2 - (o[0]*o[0] + o[2]*o[2])**0.5), abs(o[1])))
    
    for dx, dy, dz in offsets:
        sx, sy, sz = int(x + dx), int(y + dy), int(z + dz)
        block_at = block_id_at(ctx, sx, sy, sz)
        block_above = block_id_at(ctx, sx, sy + 1, sz)
        block_below = block_id_at(ctx, sx, sy - 1, sz)
        
        if is_liquid(block_at) or is_liquid(block_below):
            continue
        if "air" not in block_at or "air" not in block_above:
            continue
        if "air" in block_below:
            continue
        return (sx, sy, sz)
    return (int(x) + 2, int(y), int(z)) # Fallback


def find_place_pos_near(ctx, x: Union[int, float], y: Union[int, float], z: Union[int, float],
                        radius: int = 4, avoid: Optional[Set[Tuple[int, int, int]]] = None) -> Tuple[int, int, int]:
    """Find a suitable block placement position near coordinates.

    Args:
        ctx: Test context.
        x: X coordinate.
        y: Y coordinate.
        z: Z coordinate.
        radius: Search radius.
        avoid: Set of positions to avoid.

    Returns:
        Tuple of (x, y, z) coordinates for placement.
    """
    if avoid is None:
        px, py, pz = ctx.get_position()
        avoid = {(int(px), int(py), int(pz))}
    else:
        avoid = set(avoid)
    offsets = []
    for dy in (0, 1, -1):
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                offsets.append((dx, dy, dz))
    offsets.sort(key=lambda o: (o[0] * o[0] + o[1] * o[1] + o[2] * o[2], abs(o[1])))
    curr_px, curr_py, curr_pz = ctx.get_position()
    for dx, dy, dz in offsets:
        px, py, pz = int(x + dx), int(y + dy), int(z + dz)
        if (px, py, pz) in avoid:
            continue
        
        # Avoid placing at the exact same block the player is standing in (hitbox roughly 0.6x1.8x0.6)
        if abs(px - curr_px) < 0.8 and abs(pz - curr_pz) < 0.8 and abs(py - int(curr_py)) < 2:
             continue
        block_at = block_id_at(ctx, px, py, pz)
        block_below = block_id_at(ctx, px, py - 1, pz)
        
        # Must be placing in air/replaceable
        if "air" not in block_at and "water" not in block_at and "lava" not in block_at:
            continue
            
        # Support block must be solid
        if not block_below or "air" in block_below or "water" in block_below or "lava" in block_below:
            continue
            
        # Exclude common non-solid ground covers
        non_solid = ["grass", "flower", "fern", "sapling", "dead_bush", "torch", "fire", "leaf_litter", "snow"]
        if any(ns in block_below for ns in non_solid):
            # grass_block is an exception, it is solid
            if "grass_block" not in block_below:
                continue
                
        return (px, py, pz)
    return (int(x), int(y), int(z))


def find_ground_place_pos(ctx, radius: int = 5) -> Optional[Tuple[int, int, int]]:
    """Find a placement position on solid ground.
    
    Scans DOWN from player position to find actual ground level first,
    then searches for a placement spot at that level.
    Only places on grass_block, dirt, stone, cobblestone, etc.
    
    Args:
        ctx: Test context.
        radius: Search radius.
        
    Returns:
        Tuple of (x, y, z) coordinates for placement, or None if not found.
    """
    px, py, pz = ctx.get_position()
    player_x, player_z = int(px), int(pz)
    
    # Ground block types we want to place ON TOP OF
    ground_blocks = ["grass_block", "dirt", "stone", "cobblestone", 
                     "deepslate", "diorite", "granite", "andesite", "terracotta"]
    
    # First, find the ACTUAL ground level by scanning down from player
    # This handles cases where player is standing on a tree/log
    actual_ground_y = None
    for scan_y in range(int(py), int(py) - 20, -1):
        block_at = block_id_at(ctx, player_x, scan_y, player_z)
        if block_at and any(gb in block_at for gb in ground_blocks):
            actual_ground_y = scan_y + 1  # Place position is ON TOP of ground
            break
    
    if actual_ground_y is None:
        actual_ground_y = int(py)  # Fallback to player Y
    
    # Search in spiral pattern from player position at ground level
    offsets = []
    for dx in range(-radius, radius + 1):
        for dz in range(-radius, radius + 1):
            offsets.append((dx, dz))
    offsets.sort(key=lambda o: o[0] * o[0] + o[1] * o[1])
    
    for dx, dz in offsets:
        x, z = player_x + dx, player_z + dz
        
        # Skip if too close to player
        if abs(dx) < 1 and abs(dz) < 1:
            continue
        
        # For each XZ position, scan down to find ground at that location
        for scan_y in range(actual_ground_y + 5, actual_ground_y - 10, -1):
            block_at = block_id_at(ctx, x, scan_y, z)
            block_below = block_id_at(ctx, x, scan_y - 1, z)
            
            # Must be air at placement spot
            if not block_at or "air" not in block_at:
                continue
            
            # Must have solid ground below
            if not block_below:
                continue
            
            # Check if block below is actual ground (not leaves, logs, etc.)
            is_ground = any(gb in block_below for gb in ground_blocks)
            if not is_ground:
                continue
            
            return (x, scan_y, z)
    
    return None



def move_near(ctx, x: Union[int, float], y: Union[int, float], z: Union[int, float], timeout: float = 20.0) -> bool:
    """Move to a suitable position near coordinates.

    Args:
        ctx: Test context.
        x: X coordinate.
        y: Y coordinate.
        z: Z coordinate.
        timeout: Movement timeout.

    Returns:
        True if movement successful.
    """
    from tests.utils.mc_harness.actions import do_goto
    stand_x, stand_y, stand_z = find_stand_pos(ctx, x, y, z, radius=3)
    target = {"x": stand_x, "y": stand_y, "z": stand_z}
    # Use smaller arrival radius for interaction
    ok = do_goto(ctx, target, timeout=timeout, arrival_radius=1.2, require_arrival=True)
    if not ok and not in_range(ctx, stand_x, stand_y, stand_z):
        ctx.log_event(f"Move failed near {x},{y},{z}")
        return False
    return True


def fill_plane_chunked(ctx, min_x: int, min_y: int, min_z: int, max_x: int, max_y: int, max_z: int, block: str, xz_step: int = 64):
    """Chunked fill for planes or thin walls using large XZ steps.

    This function breaks large fill operations into smaller chunks to avoid Minecraft's
    32k block limit per command. Ideal for creating floors, walls, or ceilings.

    Args:
        ctx: Test context.
        min_x: Minimum X coordinate.
        min_y: Minimum Y coordinate.
        min_z: Minimum Z coordinate.
        max_x: Maximum X coordinate.
        max_y: Maximum Y coordinate.
        max_z: Maximum Z coordinate.
        block: Block type to fill (e.g., "minecraft:stone").
        xz_step: XZ chunk size (default 64 for optimal performance).

    Example:
        >>> # Create a 128x128 stone floor at y=80
        >>> fill_plane_chunked(ctx, 0, 80, 0, 127, 80, 127, "minecraft:stone")
    """
    # For a single layer 64x64 = 4096 blocks, well under 32k limit.
    min_x, max_x = sorted((min_x, max_x))
    min_y, max_y = sorted((min_y, max_y))
    min_z, max_z = sorted((min_z, max_z))
    for x0 in range(min_x, max_x + 1, xz_step):
        x1 = min(x0 + xz_step - 1, max_x)
        for z0 in range(min_z, max_z + 1, xz_step):
            z1 = min(z0 + xz_step - 1, max_z)
            ctx.run_command(f"fill {x0} {min_y} {z0} {x1} {max_y} {z1} {block}")


def fill_volume_chunked(ctx, min_x: int, min_y: int, min_z: int, max_x: int, max_y: int, max_z: int, block: str, xz_step: int = 24, y_step: int = 64):
    """Chunked fill for volumes with safer XZ step.

    Args:
        ctx: Test context.
        min_x: Minimum X.
        min_y: Minimum Y.
        min_z: Minimum Z.
        max_x: Maximum X.
        max_y: Maximum Y.
        max_z: Maximum Z.
        block: Block type.
        xz_step: XZ chunk size.
        y_step: Y chunk size.
    """
    # 16x16x64 = 16384. 24x24x64 = 36864 (might be too big for 32k limit).
    # Safe limit is ~32768. 24x24x56 is safe.
    # Let's stick to xz_step=16 for volume to be safe as requested, or slightly larger if Y is small.
    # But for full height clear (64), we should stick to 16 or 20. 20x20x64 = 25600.
    real_xz_step = 20
    min_x, max_x = sorted((min_x, max_x))
    min_y, max_y = sorted((min_y, max_y))
    min_z, max_z = sorted((min_z, max_z))
    for x0 in range(min_x, max_x + 1, real_xz_step):
        x1 = min(x0 + real_xz_step - 1, max_x)
        for z0 in range(min_z, max_z + 1, real_xz_step):
            z1 = min(z0 + real_xz_step - 1, max_z)
            for y0 in range(min_y, max_y + 1, y_step):
                y1 = min(y0 + y_step - 1, max_y)
                ctx.run_command(f"fill {x0} {y0} {z0} {x1} {y1} {z1} {block}")


def fill_hollow_shell(ctx, min_x: int, min_y: int, min_z: int, max_x: int, max_y: int, max_z: int, block: str):
    """Build a hollow 6-face shell instead of solid fill.

    Args:
        ctx: Test context.
        min_x: Minimum X.
        min_y: Minimum Y.
        min_z: Minimum Z.
        max_x: Maximum X.
        max_y: Maximum Y.
        max_z: Maximum Z.
        block: Block type.
    """
    # Floor (y=min_y) - Plane
    fill_plane_chunked(ctx, min_x, min_y, min_z, max_x, min_y, max_z, block)
    # Roof (y=max_y) - Plane
    fill_plane_chunked(ctx, min_x, max_y, min_z, max_x, max_y, max_z, block)
    # West wall (x=min_x) - Plane (width 1 in X)
    fill_plane_chunked(ctx, min_x, min_y + 1, min_z, min_x, max_y - 1, max_z, block)
    # East wall (x=max_x) - Plane
    fill_plane_chunked(ctx, max_x, min_y + 1, min_z, max_x, max_y - 1, max_z, block)
    # North wall (z=min_z) - Plane
    fill_plane_chunked(ctx, min_x + 1, min_y + 1, min_z, max_x - 1, max_y - 1, min_z, block)
    # South wall (z=max_z) - Plane
    fill_plane_chunked(ctx, min_x + 1, min_y + 1, max_z, max_x - 1, max_y - 1, max_z, block)


def place_block_at(ctx, x: Union[int, float], y: Union[int, float], z: Union[int, float],
                    block_type: str, allow_break: bool = True, allow_move: bool = True) -> bool:
    """Place a block at coordinates with survival-safe placement.

    Args:
        ctx: Test context.
        x: X coordinate.
        y: Y coordinate.
        z: Z coordinate.
        block_type: Block type to place.
        allow_break: Allow breaking existing blocks.
        allow_move: Allow moving to position.

    Returns:
        True if placement successful.
    """
    from tests.utils.mc_harness import wait_for_block

    if not select_item(ctx.client, block_type, allow_swap=True):
        ctx.log_event(f"Missing item for placement: {block_type}")
        inv = ctx.get_inventory()
        slots = get_inv_slots(inv)
        matching_slots = [s for s in slots if s.get("id") == block_type]
        ctx.log_event(f"Placement inventory snapshot for {block_type}: {matching_slots}")
        target_block = block_id_at(ctx, x, y, z)
        ctx.log_event(f"Placement target block at {x},{y},{z}: {target_block}")
        return False

    # DEBUG: verify we really have it (with retries for sync)
    matching_slots = []
    for _ in range(3):
        inv = ctx.get_inventory()
        slots = get_inv_slots(inv)
        matching_slots = [s for s in slots if s.get("id") == block_type]
        if matching_slots:
            break
        time.sleep(0.5)

    ctx.log_event(f"DEBUG: select_item finished. Inventory slots for {block_type}: {matching_slots}")

    # Ensure we are looking at the target and not pathing
    cancel_pathing(ctx)
    ctx.client.transport.dispatch("look_at", {"x": x + 0.5, "y": y + 0.5, "z": z + 0.5})
    time.sleep(0.5)

    px, py, pz = ctx.get_position()
    if int(px) == int(x) and int(py) == int(y) and int(pz) == int(z):
        if not move_near(ctx, x + 1, y, z, timeout=5.0):
            ctx.log_event(f"Cannot move off target block at {x},{y},{z}")
            return False
    if not in_range(ctx, x, y, z):
        if allow_move:
            move_near(ctx, x, y, z, timeout=10.0)
        else:
            ctx.log_event(f"Out of range for placement at {x},{y},{z}")
            return False
    block = block_id_at(ctx, x, y, z)
    if block_type in block:
        return True
    if block and "air" not in block and allow_break and not is_liquid(block):
        try:
            ctx.client.transport.dispatch("break_block", {"x": int(x), "y": int(y), "z": int(z)})
        except Exception as exc:
            ctx.log_event(f"Break command failed at {x},{y},{z}: {exc}")
            return False
        wait_for_block(ctx, x, y, z, "air", timeout=6.0)
    try:
        block_below = block_id_at(ctx, x, y - 1, z)
        if block_below and "air" not in block_below:
            ctx.client.transport.dispatch("look_at", {"x": x + 0.5, "y": y - 0.5, "z": z + 0.5})
        else:
            ctx.client.transport.dispatch("look_at", {"x": x + 0.5, "y": y + 0.5, "z": z + 0.5})
        payload = {
            "x": int(x),
            "y": int(y),
            "z": int(z),
            "block": block_type,
        }
        ctx.client.transport.dispatch("place_block", payload)
    except Exception as exc:
        if "Target position is already occupied" in str(exc) and allow_break:
            try:
                ctx.client.transport.dispatch("break_block", {"x": int(x), "y": int(y), "z": int(z)})
                wait_for_block(ctx, x, y, z, "air", timeout=6.0)
                payload = {
                    "x": int(x),
                    "y": int(y),
                    "z": int(z),
                    "block": block_type,
                }
                ctx.client.transport.dispatch("place_block", payload)
            except Exception as retry_exc:
                ctx.log_event(f"Place retry failed at {x},{y},{z}: {retry_exc}")
                return False
        elif "Placement failed" in str(exc):
            try:
                time.sleep(0.2)
                payload = {
                    "x": int(x),
                    "y": int(y),
                    "z": int(z),
                    "block": block_type,
                }
                ctx.client.transport.dispatch("place_block", payload)
            except Exception as retry_exc:
                ctx.log_event(f"Place retry failed at {x},{y},{z}: {retry_exc}")
                return False
        else:
            ctx.log_event(f"Place command failed at {x},{y},{z}: {exc}")
            return False
    placed, last_id = wait_for_block(ctx, x, y, z, block_type, timeout=2.0)
    if not placed:
        ctx.log_event(f"Place failed at {x},{y},{z}: {last_id}")
        target_block = block_id_at(ctx, x, y, z)
        ctx.log_event(f"Placement target block after failure at {x},{y},{z}: {target_block}")
    return placed


def bot_place_block(ctx, x: Union[int, float], y: Union[int, float], z: Union[int, float], block_type: str, allow_move: bool = True):
    """Place a single block using survival-safe placement with retries.

    Args:
        ctx: Test context.
        x: X coordinate.
        y: Y coordinate.
        z: Z coordinate.
        block_type: Block type to place.
        allow_move: Allow moving to position.

    Returns:
        True if placement successful.
    """
    failed_spots = set()
    for attempt in range(4):
        # Find a suitable position (increase search radius on retry)
        target = find_place_pos_near(ctx, int(x), int(y), int(z), radius=attempt + 2, avoid=failed_spots)
        if not target:
            continue
            
        tx, ty, tz = target
        if allow_move:
            # Move to a stand position relative to THAT specific target
            move_near(ctx, tx, ty, tz, timeout=12.0)
            
        if place_block_at(ctx, tx, ty, tz, block_type, allow_move=False): # allow_move=False because we just moved
            return True
            
        # If failed, record this spot and try another
        failed_spots.add(target)
        
        # Move slightly to clear potentially conflicting state
        px, py, pz = ctx.get_position()
        move_near(ctx, px + 0.5, py, pz + 0.5, timeout=5.0)
        time.sleep(0.5)
        
    return False


def bot_build_hollow_box(ctx, min_x: int, min_y: int, min_z: int, max_x: int, max_y: int, max_z: int, wall_block: str):
    """Build 4 walls of a box (hollow interior).

    Creates a hollow rectangular structure by placing blocks only on the outer walls.
    The bot will move around to place blocks safely, avoiding liquids and ensuring
    proper support blocks.

    Args:
        ctx: Test context.
        min_x: Minimum X coordinate of the box.
        min_y: Minimum Y coordinate of the box.
        min_z: Minimum Z coordinate of the box.
        max_x: Maximum X coordinate of the box.
        max_y: Maximum Y coordinate of the box.
        max_z: Maximum Z coordinate of the box.
        wall_block: Block type for walls (e.g., "minecraft:oak_planks").

    Returns:
        True if build successful.

    Example:
        >>> # Build a 5x3x5 hollow box from (10,80,10) to (14,82,14)
        >>> success = bot_build_hollow_box(ctx, 10, 80, 10, 14, 82, 14, "minecraft:oak_planks")
        >>> assert success
    """
    ok = True
    # North/South
    ok = ok and bot_box_fill(ctx, min_x, min_y, min_z, max_x, max_y, min_z, wall_block)
    ok = ok and bot_box_fill(ctx, min_x, min_y, max_z, max_x, max_y, max_z, wall_block)
    # East/West (exclude corners to avoid overdraw/fighting)
    ok = ok and bot_box_fill(ctx, min_x, min_y, min_z+1, min_x, max_y, max_z-1, wall_block)
    ok = ok and bot_box_fill(ctx, max_x, min_y, min_z+1, max_x, max_y, max_z-1, wall_block)
    return ok


def bot_box_fill(ctx, min_x: int, min_y: int, min_z: int, max_x: int, max_y: int, max_z: int, block_type: str):
    """Place blocks in a region using survival-safe placement.

    Args:
        ctx: Test context.
        min_x: Minimum X.
        min_y: Minimum Y.
        min_z: Minimum Z.
        max_x: Maximum X.
        max_y: Maximum Y.
        max_z: Maximum Z.
        block_type: Block type to place.

    Returns:
        True if fill successful.
    """
    from tests.utils.mc_harness import wait_for_block

    min_x, max_x = sorted((min_x, max_x))
    min_y, max_y = sorted((min_y, max_y))
    min_z, max_z = sorted((min_z, max_z))

    patch = 3
    ok = True

    for x0 in range(min_x, max_x + 1, patch):
        for z0 in range(min_z, max_z + 1, patch):
            x1 = min(x0 + patch - 1, max_x)
            z1 = min(z0 + patch - 1, max_z)
            center_x = (x0 + x1) // 2
            center_z = (z0 + z1) // 2
            move_y = BASE_Y
            ok = move_near(ctx, center_x, move_y, center_z, timeout=25.0) and ok

            for y in range(min_y, max_y + 1):
                for x in range(x0, x1 + 1):
                    for z in range(z0, z1 + 1):
                        ok = place_block_at(ctx, x, y, z, block_type, allow_break=True) and ok
                        time.sleep(0.02)

    placed, last_id = wait_for_block(ctx, max_x, max_y, max_z, block_type, timeout=2.0)
    if not placed:
        ctx.log_event(f"Build verify failed at {max_x},{max_y},{max_z}: {last_id}")
    return ok and placed


def build_simple_structure(ctx, x: int, y: int, z: int, width: int, depth: int, height: int,
                           wall_block: str = "minecraft:oak_planks", floor_block: str = "minecraft:cobblestone"):
    """Legacy helper for simulated structures (admin).

    Args:
        ctx: Test context.
        x: Base X.
        y: Base Y.
        z: Base Z.
        width: Structure width.
        depth: Structure depth.
        height: Structure height.
        wall_block: Wall block type.
        floor_block: Floor block type.
    """
    from tests.utils.mc_harness import wait_for_block

    # Floor
    ctx.run_command(f"fill {x} {y} {z} {x+width-1} {y} {z+depth-1} {floor_block}")
    # Walls
    ctx.run_command(f"fill {x} {y+1} {z} {x+width-1} {y+height} {z} {wall_block}")  # North
    ctx.run_command(f"fill {x} {y+1} {z+depth-1} {x+width-1} {y+height} {z+depth-1} {wall_block}")  # South
    ctx.run_command(f"fill {x} {y+1} {z} {x} {y+height} {z+depth-1} {wall_block}")  # West
    ctx.run_command(f"fill {x+width-1} {y+1} {z} {x+width-1} {y+height} {z+depth-1} {wall_block}")  # East
    # Clear interior
    if width > 2 and depth > 2:
        ctx.run_command(f"fill {x+1} {y+1} {z+1} {x+width-2} {y+height-1} {z+depth-2} minecraft:air")
    # Roof (simple flat)
    ctx.run_command(f"fill {x} {y+height+1} {z} {x+width-1} {y+height+1} {z+depth-1} {wall_block}")

    # Deterministic wait: verify a corner block exists (pass full ID)
    wait_for_block(ctx, x, y, z, floor_block, timeout=2.0)



def wait_for_baritone_idle(ctx, timeout: float = 10.0):
    """
    Wait for Baritone to stop pathing and position to stabilize.
    
    Args:
        ctx: Test context
        timeout: Max wait time
    """
    wait_for_pathing_stop(ctx, timeout=timeout)
    wait_for_position_stable(ctx, timeout=2.0, stable_window=0.5)
