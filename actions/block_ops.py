"""
Block operations and utilities for Minecraft automation tests.

This module contains utility functions for block placement, detection, and movement
operations used in test suites.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
import time

if TYPE_CHECKING:
    from tests.utils.mc_harness.context import TestContext

def _block_id_at(ctx: TestContext, x: int, y: int, z: int) -> str:
    """Get block ID at coordinates."""
    block = ctx.get_block(x, y, z)
    data = block.get("data", block)
    return data.get("id", "")

def _is_liquid(block_id: str) -> bool:
    """Check if block is a liquid."""
    return "water" in block_id or "lava" in block_id or "bubble_column" in block_id

def _in_range(ctx: TestContext, x: float, y: float, z: float, max_dist: float = 4.5) -> bool:
    """Check if position is within interaction range."""
    px, py, pz = ctx.get_position()
    dx = px - x
    dy = py - y
    dz = pz - z
    return (dx * dx + dy * dy + dz * dz) ** 0.5 <= max_dist

def _find_stand_pos(ctx: TestContext, x: int, y: int, z: int, radius: int = 3) -> tuple[int, int, int]:
    """Find a suitable standing position near coordinates."""
    offsets = []
    for dy in (0, 1, -1):
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                offsets.append((dx, dy, dz))
    offsets.sort(key=lambda o: (o[0] * o[0] + o[1] * o[1] + o[2] * o[2], abs(o[1])))
    for dx, dy, dz in offsets:
        sx, sy, sz = int(x + dx), int(y + dy), int(z + dz)
        block_at = _block_id_at(ctx, sx, sy, sz)
        block_below = _block_id_at(ctx, sx, sy - 1, sz)
        if _is_liquid(block_at) or _is_liquid(block_below):
            continue
        if "air" not in block_at:
            continue
        if "air" in block_below:
            continue
        return (sx, sy, sz)
    return (int(x), int(y), int(z))

def _find_place_pos_near(ctx: TestContext, x: int, y: int, z: int, radius: int = 4, avoid=None) -> tuple[int, int, int]:
    """Find a suitable block placement position near coordinates."""
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
    for dx, dy, dz in offsets:
        px, py, pz = int(x + dx), int(y + dy), int(z + dz)
        if (px, py, pz) in avoid:
            continue
        block_at = _block_id_at(ctx, px, py, pz)
        block_below = _block_id_at(ctx, px, py - 1, pz)
        if _is_liquid(block_at) or _is_liquid(block_below):
            continue
        if "air" not in block_at:
            continue
        if "air" in block_below:
            continue
        return (px, py, pz)
    return (int(x), int(y), int(z))

def _move_near(ctx: TestContext, x: int, y: int, z: int, timeout: float = 20.0) -> bool:
    """Move to a suitable position near coordinates."""
    stand_x, stand_y, stand_z = _find_stand_pos(ctx, x, y, z, radius=3)
    target = {"x": stand_x, "y": stand_y, "z": stand_z}
    ok = do_goto(ctx, target, timeout=timeout, arrival_radius=2.5, require_arrival=False)
    if not ok and not _in_range(ctx, stand_x, stand_y, stand_z):
        ctx.log_event(f"Move failed near {x},{y},{z}")
        return False
    return True

def _fill_plane_chunked(ctx: TestContext, min_x: int, min_y: int, min_z: int, max_x: int, max_y: int, max_z: int, block: str, xz_step: int = 64) -> None:
    """Chunked fill for planes or thin walls using large XZ steps."""
    # For a single layer 64x64 = 4096 blocks, well under 32k limit.
    min_x, max_x = sorted((min_x, max_x))
    min_y, max_y = sorted((min_y, max_y))
    min_z, max_z = sorted((min_z, max_z))
    for x0 in range(min_x, max_x + 1, xz_step):
        x1 = min(x0 + xz_step - 1, max_x)
        for z0 in range(min_z, max_z + 1, xz_step):
            z1 = min(z0 + xz_step - 1, max_z)
            ctx.run_command(f"fill {x0} {min_y} {z0} {x1} {max_y} {z1} {block}")

def _fill_volume_chunked(ctx: TestContext, min_x: int, min_y: int, min_z: int, max_x: int, max_y: int, max_z: int, block: str, xz_step: int = 24, y_step: int = 64) -> None:
    """Chunked fill for volumes with safer XZ step."""
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

def _fill_hollow_shell(ctx: TestContext, min_x: int, min_y: int, min_z: int, max_x: int, max_y: int, max_z: int, block: str) -> None:
    """Build a hollow 6-face shell instead of solid fill."""
    # Floor (y=min_y) - Plane
    _fill_plane_chunked(ctx, min_x, min_y, min_z, max_x, min_y, max_z, block)
    # Roof (y=max_y) - Plane
    _fill_plane_chunked(ctx, min_x, max_y, min_z, max_x, max_y, max_z, block)
    # West wall (x=min_x) - Plane (width 1 in X)
    _fill_plane_chunked(ctx, min_x, min_y + 1, min_z, min_x, max_y - 1, max_z, block)
    # East wall (x=max_x) - Plane
    _fill_plane_chunked(ctx, max_x, min_y + 1, min_z, max_x, max_y - 1, max_z, block)
    # North wall (z=min_z) - Plane
    _fill_plane_chunked(ctx, min_x + 1, min_y + 1, min_z, max_x - 1, max_y - 1, min_z, block)
    # South wall (z=max_z) - Plane
    _fill_plane_chunked(ctx, min_x + 1, min_y + 1, max_z, max_x - 1, max_y - 1, max_z, block)

def _place_block_at(ctx: TestContext, x: int, y: int, z: int, block_type: str, allow_break: bool = True, allow_move: bool = True) -> bool:
    """Place a block at coordinates with survival-safe placement."""
    if not select_item(ctx.client, block_type, allow_swap=True):
        ctx.log_event(f"Missing item for placement: {block_type}")
        return False

    # DEBUG: verify we really have it
    inv = ctx.get_inventory()
    slots = _get_inv_slots(inv)
    matching_slots = [s for s in slots if s.get("id") == block_type]
    ctx.log_event(f"DEBUG: select_item OK. Inventory slots for {block_type}: {matching_slots}")

    px, py, pz = ctx.get_position()
    if int(px) == int(x) and int(py) == int(y) and int(pz) == int(z):
        if not _move_near(ctx, x + 1, y, z, timeout=5.0):
            ctx.log_event(f"Cannot move off target block at {x},{y},{z}")
            return False
    if not _in_range(ctx, x, y, z):
        if allow_move:
            _move_near(ctx, x, y, z, timeout=10.0)
        else:
            ctx.log_event(f"Out of range for placement at {x},{y},{z}")
            return False
    block = _block_id_at(ctx, x, y, z)
    if block_type in block:
        return True
    if block and "air" not in block and allow_break and not _is_liquid(block):
        try:
            ctx.client.transport.dispatch("break_block", {"x": int(x), "y": int(y), "z": int(z)})
        except Exception as exc:
            ctx.log_event(f"Break command failed at {x},{y},{z}: {exc}")
            return False
        wait_for_block(ctx, x, y, z, "air", timeout=6.0)
    try:
        block_below = _block_id_at(ctx, x, y - 1, z)
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
    return placed

def bot_place_block(ctx: TestContext, x: int, y: int, z: int, block_type: str, allow_move: bool = True) -> bool:
    """Place a single block using survival-safe placement."""
    move_y = BASE_Y
    if allow_move:
        _move_near(ctx, x, move_y, z, timeout=15.0)
    return _place_block_at(ctx, x, y, z, block_type, allow_move=allow_move)

def bot_build_hollow_box(ctx: TestContext, min_x: int, min_y: int, min_z: int, max_x: int, max_y: int, max_z: int, wall_block: str) -> bool:
    """Build 4 walls of a box (hollow interior)."""
    ok = True
    # North/South
    ok = ok and bot_box_fill(ctx, min_x, min_y, min_z, max_x, max_y, min_z, wall_block)
    ok = ok and bot_box_fill(ctx, min_x, min_y, max_z, max_x, max_y, max_z, wall_block)
    # East/West (exclude corners to avoid overdraw/fighting)
    ok = ok and bot_box_fill(ctx, min_x, min_y, min_z+1, min_x, max_y, max_z-1, wall_block)
    ok = ok and bot_box_fill(ctx, max_x, min_y, min_z+1, max_x, max_y, max_z-1, wall_block)
    return ok

def bot_box_fill(ctx: TestContext, min_x: int, min_y: int, min_z: int, max_x: int, max_y: int, max_z: int, block_type: str) -> bool:
    """
    Place blocks in a region using survival-safe placement.
    """
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
            ok = _move_near(ctx, center_x, move_y, center_z, timeout=25.0) and ok

            for y in range(min_y, max_y + 1):
                for x in range(x0, x1 + 1):
                    for z in range(z0, z1 + 1):
                        ok = _place_block_at(ctx, x, y, z, block_type, allow_break=True) and ok
                        time.sleep(0.02)

    placed, last_id = wait_for_block(ctx, max_x, max_y, max_z, block_type, timeout=2.0)
    if not placed:
        ctx.log_event(f"Build verify failed at {max_x},{max_y},{max_z}: {last_id}")
    return ok and placed

def build_simple_structure(ctx: TestContext, x: int, y: int, z: int, width: int, depth: int, height: int, wall_block: str = "minecraft:oak_planks", floor_block: str = "minecraft:cobblestone") -> None:
    """Legacy helper for simulated structures (admin)."""
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

def prepare_base_test(ctx: TestContext, tid: str) -> None:
    """Prepare base test environment."""
    # This function needs to be defined based on how it's used
    pass

def fill_supply_chests(ctx: TestContext, x: int, y: int, z: int, items: dict[str, int]) -> dict:
    """Fill up to 4 DOUBLE CHESTS with items (216 slots max)."""
    # This function is large, but I'll include it since it's imported
    # Calculate total slots needed
    total_slots = 0
    for item_id, count in items.items():
        total_slots += (count + 63) // 64  # ceil division

    ctx.log_event(f"Supply needs {total_slots} slots, using up to 4 double chests (216 slots)")

    # Chest positions
    # Calculate chest positions (must match t1000_setup: 8 single chests)
    # Pairs: (x, x+1), (x+3, x+4), (x+6, x+7), (x+9, x+10)
    chest_positions = []
    ax, ay, az = x, y, z # Use the function arguments as the base for offsets
    for offset_x in [0, 1, 3, 4, 6, 7, 9, 10]:
         chest_positions.append((ax + offset_x, ay, az))

    # Verify all chests exist
    for i, (cx, cy, cz) in enumerate(chest_positions):
        val = _block_id_at(ctx, cx, cy, cz)
        ctx.require(
            "chest" in val,
            f"Supply chest {i+1} missing at {cx},{cy},{cz}. Run T1000 first."
        )

    # Flatten items into slot assignments
    slot_assignments = []  # [(item_id, stack_size), ...]
    for item_id, count in items.items():
        remaining = count
        while remaining > 0:
            stack_size = min(remaining, 64)
            slot_assignments.append((item_id, stack_size))
            remaining -= stack_size

    slot_map = {}
    def _record(item_id: str, chest_key: str, slot_idx: int, count: int) -> None:
        slot_map.setdefault(item_id, []).append(
            {"chest": chest_key, "slot": slot_idx, "count": count}
        )

    suite_state["chest_meta"] = suite_state.get("chest_meta", {})
    # Fill chests (using metadata for capacity)
    slot_idx = 0
    for chest_num, (cx, cy, cz) in enumerate(chest_positions):
        chest_key = "main" if chest_num == 0 else f"overflow_{chest_num}"
        meta = suite_state["chest_meta"].get((cx, cy, cz), {"slots": 27}) # Default to single if missing
        max_slots = meta.get("slots", 27)

        # Fill slots
        for local_slot in range(max_slots):
             if slot_idx >= len(slot_assignments):
                 break
             item_id, stack_size = slot_assignments[slot_idx]
             # If double chest, slots > 26 are in the second block?
             # 'item replace block' addresses container.0 to container.53 if double?
             # We assume (cx,cy,cz) is the primary part.
             ctx.run_command(f"item replace block {cx} {cy} {cz} container.{local_slot} with {item_id} {stack_size}")
             _record(item_id, chest_key, local_slot, stack_size)
             slot_idx += 1

        # Store overflow positions for withdrawal
        suite_state["supply_overflow_pos"] = chest_positions[1] if len(chest_positions) > 1 else None
        suite_state["supply_chest_positions"] = chest_positions

    if len(slot_assignments) > 216:
        ctx.log_event(f"WARNING: {len(slot_assignments) - 216} stacks don't fit in 4 double chests!")

    return slot_map

# These functions need to be imported from the test infrastructure
def _get_inv_slots(data):
    """Get inventory slots from screen data."""
    return data.get("slots", [])

# These need to be imported or defined
def select_item(client, item_type, allow_swap=True):
    """Select item in inventory."""
    # This is a placeholder - need to implement based on actual inventory system
    return True

def do_goto(ctx, target, timeout=20.0, arrival_radius=2.5, require_arrival=True):
    """Move to target position."""
    # This is a placeholder - need to implement based on actual movement system
    return True

def wait_for_block(ctx, x, y, z, block_type, timeout=2.0):
    """Wait for block to appear at position."""
    # This is a placeholder - need to implement based on actual block checking
    return True, block_type

# Global variable that needs to be defined
BASE_Y = 80

# suite_state needs to be available
suite_state = {}