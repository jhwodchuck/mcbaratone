"""
Inventory operations and utilities for Minecraft automation tests.

This module contains utility functions for inventory management, chest operations,
and crafting operations used in test suites.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
import time

if TYPE_CHECKING:
    from tests.utils.mc_harness.context import TestContext

def _open_supply_chest(ctx: TestContext) -> bool:
    """Open the supply chest at the configured position."""
    pos = _state("T1000").get("chest_pos")
    if not pos:
        pos = anchor("supply")
        _state("T1000")["chest_pos"] = pos
    _move_near(ctx, pos[0], BASE_Y, pos[2], timeout=15.0)
    return bool(do_open_container(ctx, pos, timeout=3.0))

def _build_slot_map_from_screen(slots: list, chest_key: str, max_slots: int) -> dict:
    """Build slot map from screen slots data."""
    slot_map = {}
    for slot in slots:
        slot_idx = slot.get("slot")
        item_id = slot.get("id")
        count = slot.get("count", 0)
        if slot_idx is None or slot_idx >= max_slots:
            continue
        if not item_id or item_id == "minecraft:air" or count <= 0:
            continue
        slot_map.setdefault(item_id, []).append(
            {"chest": chest_key, "slot": slot_idx, "count": count}
        )
    return slot_map

def _refresh_supply_slot_map(ctx) -> dict:
    """Refresh the supply chest slot map by checking all chests."""
    slot_map = {}

    def _record(item_id: str, chest_key: str, slot_idx: int, count: int) -> None:
        slot_map.setdefault(item_id, []).append(
            {"chest": chest_key, "slot": slot_idx, "count": count}
        )

    # Use updated chest positions from fill if available, else T1000 state
    chest_positions = suite_state.get("supply_chest_positions")
    if not chest_positions:
         chest_positions = _state("T1000").get("chest_positions")

    if not chest_positions:
        # Fallback
        chest_positions = [_state("T1000").get("chest_pos") or anchor("supply")]

    overflow_pos = suite_state.get("supply_extra_pos")
    if overflow_pos:
         chest_positions.append(overflow_pos)

    for i, pos in enumerate(chest_positions):
        label = "main" if i == 0 else f"overflow_{i}"

        # Move near before opening (critical for interaction)
        _move_near(ctx, pos[0], BASE_Y, pos[2], timeout=10.0)

        if do_open_container(ctx, pos, timeout=3.0):
            # Use metadata if available
            meta = suite_state.get("chest_meta", {}).get(pos)
            num_container_slots = 27
            if meta:
                num_container_slots = meta.get("slots", 27)

            screen = ctx.client.transport.dispatch("get_screen", {})
            data = screen.get("data", screen)
            slots = _get_inv_slots(data)

            # Player inventory always last 36 slots
            if not slots:
                ctx.log_event(f"Refresh failed: no slots in screen for {label}")
                do_close_container(ctx)
                continue

            actual_container_slots = len(slots) - 36
            if actual_container_slots != num_container_slots and meta:
                 ctx.log_event(f"Chest at {pos} has {actual_container_slots} slots, expected {num_container_slots} (metadata)")

            # Map slots
            for slot_idx in range(actual_container_slots):
                item = slots[slot_idx]
                if item:
                     _record(item["id"], label, slot_idx, item["count"])

            do_close_container(ctx)
        else:
            ctx.log_event(f"Failed to open supply chest {i} at {pos}")

    suite_state["supply_slot_map"] = slot_map
    return slot_map

def _place_double_chest(ctx, x: int, y: int, z: int) -> bool:
    """Place a double chest at the given position."""
    # Face south so both chests get the same facing and merge.
    _move_near(ctx, x, BASE_Y, z - 2, timeout=10.0)
    ctx.client.transport.dispatch("look_at", {"x": x + 0.5, "y": y + 0.5, "z": z + 5.5})
    ok = _place_block_at(ctx, x, y, z, "minecraft:chest", allow_move=False)
    ctx.client.transport.dispatch("look_at", {"x": x + 1.5, "y": y + 0.5, "z": z + 5.5})
    ok = _place_block_at(ctx, x + 1, y, z, "minecraft:chest", allow_move=False) and ok
    return ok

def _deposit_inventory_to_supply_chest(ctx) -> bool:
    """Deposit inventory items into supply chests."""
    def _quick_move_range(start_slot: int, end_slot: int) -> None:
        for slot in range(start_slot, end_slot + 1):
            safe_inventory_click(ctx, slot, "QUICK_MOVE")
            time.sleep(0.05)

    # Use all known chests
    chest_positions = _state("T1000").get("chest_positions")
    if not chest_positions:
        # Fallback
        pos = _state("T1000").get("chest_pos") or anchor("supply")
        chest_positions = [pos]

    # Try depositing into each chest until inventory is clear
    for i, pos in enumerate(chest_positions):
        if not do_open_container(ctx, pos, timeout=3.0):
             ctx.log_event(f"Failed to open supply chest {i} for deposit")
             continue

        # Use metadata if available
        meta = suite_state.get("chest_meta", {}).get(pos)

        screen = ctx.client.transport.dispatch("get_screen", {})
        data = screen.get("data", screen)
        slots = _get_inv_slots(data)

        if not slots:
            do_close_container(ctx)
            continue

        num_container_slots = len(slots) - 36
        if meta:
             num_container_slots = meta.get("slots", num_container_slots)

        player_start = num_container_slots
        player_end = len(slots) - 1

        _quick_move_range(player_start, player_end)
        do_close_container(ctx)

        if ctx.wait_for_inventory_clear(timeout=1.0):
             _refresh_supply_slot_map(ctx)
             return True

    # Fallback: place an extra chest if still full?
    # ... (omitted for simplicity, we have 4 double chests, sufficient space)

    cleared = ctx.wait_for_inventory_clear(timeout=2.0)
    _refresh_supply_slot_map(ctx)
    return cleared

def _withdraw_from_supply_chest(ctx, item_counts: dict) -> bool:
    """Withdraw specified items from supply chests."""
    slot_map = _refresh_supply_slot_map(ctx)
    if not slot_map:
        slot_map = _refresh_supply_slot_map(ctx)
    if not slot_map:
        ctx.log_event("No supply slot map available")
        return False

    chest_positions = suite_state.get("supply_chest_positions")
    if not chest_positions:
         # Fallback to T1000 state
         chest_positions = _state("T1000").get("chest_positions")

    if not chest_positions:
         # Fallback to anchor
         pos = _state("T1000").get("chest_pos") or anchor("supply")
         chest_positions = [pos]

    # Verify sufficiency
    missing_any = False
    for item_id, needed in item_counts.items():
        entries = slot_map.get(item_id, [])
        calc_count = sum(e["count"] for e in entries)
        if calc_count < needed:
             ctx.log_event(f"Not enough {item_id}: have {calc_count}, need {needed}")
             missing_any = True

    if missing_any:
         return False

    # Group needed slots by CHEST KEY (main, overflow_1, etc.)
    slots_to_move = {}

    for item_id, needed in item_counts.items():
        entries = slot_map.get(item_id, [])
        if not entries:
            ctx.log_event(f"Missing {item_id} in supply chest")
            continue
        remaining = needed
        for entry in entries:
            key = entry["chest"]
            slots_to_move.setdefault(key, []).append(entry["slot"])
            remaining -= entry["count"]
            if remaining <= 0:
                break

    # Iterate known chest positions and process if we have tasks for them
    for i, pos in enumerate(chest_positions):
        key = "main" if i == 0 else f"overflow_{i}"

        target_slots = slots_to_move.get(key)
        if not target_slots:
            continue

        if not do_open_container(ctx, pos, timeout=3.0):
            ctx.log_event(f"Failed to open {key} chest at {pos}")
            continue

        # Perform withdrawals
        # Dedupe slots just in case
        processed_slots = set()
        for slot in target_slots:
             if slot in processed_slots: continue
             safe_inventory_click(ctx, slot, "QUICK_MOVE")
             time.sleep(0.05)
             processed_slots.add(slot)

        do_close_container(ctx)

    # Verify success by checking inventory? Caller usually does require()
    return True

def _ensure_item_from_supply(ctx, item_id: str, count: int) -> bool:
    """Ensure specific item count is available from supply."""
    if ctx.has_item(item_id, count):
        return True
    if not _open_supply_chest(ctx):
        ctx.log_event(f"Failed to open supply chest to fetch {item_id}")
        return False
    screen = ctx.client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    slots = data.get("slots", [])
    for slot in slots:
        if slot.get("id") == item_id and slot.get("slot") is not None and slot.get("slot") < 54:
            safe_inventory_click(ctx, slot["slot"], "QUICK_MOVE")
            time.sleep(0.05)
            if ctx.has_item(item_id, count):
                break
    do_close_container(ctx)
    if ctx.has_item(item_id, count):
        return True
    overflow_pos = suite_state.get("supply_overflow_pos")
    if not overflow_pos:
        return False
    if not do_open_container(ctx, overflow_pos, timeout=3.0):
        return False
    screen = ctx.client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    slots = data.get("slots", [])
    for slot in slots:
        if slot.get("id") == item_id and slot.get("slot") is not None and slot.get("slot") < 27:
            safe_inventory_click(ctx, slot["slot"], "QUICK_MOVE")
            time.sleep(0.05)
            if ctx.has_item(item_id, count):
                break
    do_close_container(ctx)
    return ctx.has_item(item_id, count)

def _smelt_in_furnace(ctx, furnace_pos: tuple, input_id: str, fuel_id: str, output_id: str, output_count: int, wait_per_item: float = 10.5) -> bool:
    """Smelt items in a furnace."""
    if "furnace" not in _block_id_at(ctx, furnace_pos[0], furnace_pos[1], furnace_pos[2]):
        if not bot_place_block(ctx, furnace_pos[0], furnace_pos[1], furnace_pos[2], "minecraft:furnace"):
            ctx.log_event("Failed to place furnace for smelting")
            return False

    if not do_open_container(ctx, furnace_pos, timeout=3.0):
        ctx.log_event("Failed to open furnace for smelting")
        return False

    # Dynamic slot finding in the OPEN container screen
    # Furnace Screen: 0=Input, 1=Fuel, 2=Output. 3+ = Player Inventory.
    screen = ctx.client.transport.dispatch("get_screen", {})
    slots = _get_inv_slots(screen.get("data", screen))

    input_slot_idx = None
    fuel_slot_idx = None

    # Search player slots (start at 3)
    for i, item in enumerate(slots):
        if i < 3: continue # Skip furnace slots
        if not item: continue

        if item.get("id") == input_id and input_slot_idx is None:
            input_slot_idx = i
        elif item.get("id") == fuel_id and fuel_slot_idx is None:
            fuel_slot_idx = i

    if input_slot_idx is None or fuel_slot_idx is None:
        ctx.log_event(f"Missing smelt items in inventory (found input={input_slot_idx}, fuel={fuel_slot_idx})")
        do_close_container(ctx)
        return False

    # Move items to furnace
    # Order matters? Fuel first?
    safe_inventory_click(ctx, fuel_slot_idx, "QUICK_MOVE")
    time.sleep(0.3)
    safe_inventory_click(ctx, input_slot_idx, "QUICK_MOVE")
    time.sleep(0.3)

    # Wait for smelting
    time.sleep(max(12.0, output_count * wait_per_item))

    # Take output (Slot 2)
    safe_inventory_click(ctx, 2, "QUICK_MOVE")
    time.sleep(0.3)

    do_close_container(ctx)
    return ctx.has_item(output_id, output_count)

def _craft_bed_manual(ctx, bed_id: str = "minecraft:white_bed") -> bool:
    """Manually craft a bed through the crafting interface."""
    screen = ctx.client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    screen_type = data.get("type", "")
    if (
        "Crafting" not in screen_type
        and screen_type not in {"class_1714", "class_1723", "CraftingScreenHandler"}
    ):
        state = ctx.get_state()
        state_screen = state.get("screen", "")
        if "craft" not in state_screen.lower():
            ctx.log_event(f"Bed craft failed: wrong screen {screen_type}")
            return False
    slots = data.get("slots", [])
    if not slots:
        ctx.log_event("Bed craft failed: no slots in crafting screen")
        return False

    def _find_slot(predicate, min_count: int = 1):
        for slot in slots:
            if predicate(slot) and slot.get("count", 0) >= min_count:
                return slot.get("slot")
        return None

    wool_slot = _find_slot(lambda s: s.get("id", "").endswith("_wool"), min_count=3)
    plank_slot = _find_slot(lambda s: s.get("id", "").endswith("_planks"), min_count=3)
    if wool_slot is None or plank_slot is None:
        ctx.log_event(f"Bed craft missing ingredients (wool_slot={wool_slot}, plank_slot={plank_slot})")
        return False

    # Clear any leftovers in the crafting grid.
    for grid_slot in range(1, 10):
        slot_info = next((s for s in slots if s.get("slot") == grid_slot), None)
        if slot_info and slot_info.get("id") not in ("minecraft:air", None) and slot_info.get("count", 0) > 0:
            safe_inventory_click(ctx, grid_slot, "QUICK_MOVE")
            time.sleep(0.05)

    def _place_one_each(source_slot: int, target_slots: list) -> None:
        safe_inventory_click(ctx, source_slot, "PICKUP", button=0)
        time.sleep(0.05)
        for target in target_slots:
            safe_inventory_click(ctx, target, "PICKUP", button=1)
            time.sleep(0.05)
        safe_inventory_click(ctx, source_slot, "PICKUP", button=0)
        time.sleep(0.05)

    # Wool row then planks row in 3x3 grid.
    _place_one_each(wool_slot, [1, 2, 3])
    _place_one_each(plank_slot, [4, 5, 6])

    safe_inventory_click(ctx, 0, "QUICK_MOVE")
    time.sleep(0.1)
    return ctx.has_item(bed_id)

def _ensure_crafting_table_open(ctx, table_pos=None) -> bool:
    """Ensure a crafting table is available and open."""
    px, py, pz = ctx.get_position()
    if table_pos is None:
        table_pos = _find_place_pos_near(ctx, int(px) + 1, int(py), int(pz))

    # Candidates: Target + Neighbors
    candidates = [table_pos]
    candidates.append((table_pos[0] + 1, table_pos[1], table_pos[2]))
    candidates.append((table_pos[0] - 1, table_pos[1], table_pos[2]))
    candidates.append((table_pos[0], table_pos[1], table_pos[2] + 1))
    candidates.append((table_pos[0], table_pos[1], table_pos[2] - 1))

    for i, (tx, ty, tz) in enumerate(candidates):
        tx, ty, tz = int(tx), int(ty), int(tz)

        # Check if occupied by something else (skip if not air and not crafting table)
        current_block = _block_id_at(ctx, tx, ty, tz)
        if current_block and "air" not in current_block and "crafting_table" not in current_block:
            continue

        # Ensure we are not standing on it
        px, py, pz = ctx.get_position()
        if int(px) == tx and int(pz) == tz:
            _move_near(ctx, tx + 2, ty, tz, timeout=5.0)

        # Move near
        _move_near(ctx, tx, BASE_Y, tz, timeout=8.0)

        # Check existing
        if "crafting_table" in _block_id_at(ctx, tx, ty, tz):
             suite_state["crafting_table_pos"] = (tx, ty, tz)
             return bool(do_open_container(ctx, (tx, ty, tz), timeout=3.0))

        # Try place
        if bot_place_block(ctx, tx, ty, tz, "minecraft:crafting_table", allow_move=True):
             suite_state["crafting_table_pos"] = (tx, ty, tz)
             if do_open_container(ctx, (tx, ty, tz), timeout=3.0):
                 return True

        ctx.log_event(f"Candidate {i} failed at {tx},{ty},{tz}")

    ctx.log_event("All crafting table placement candidates failed.")
    return False



# These functions need to be imported from the test infrastructure
def _get_inv_slots(data):
    """Get inventory slots from screen data."""
    return data.get("slots", [])

def safe_inventory_click(ctx, slot, action, button=0):
    """Click on inventory slot."""
    # This is a placeholder - need to implement based on actual inventory system
    pass

def do_open_container(ctx, pos, timeout=3.0):
    """Open container at position."""
    # This is a placeholder - need to implement based on actual container system
    return True

def do_close_container(ctx):
    """Close current container."""
    # This is a placeholder - need to implement based on actual container system
    pass

# Global variable that needs to be defined
BASE_Y = 80

# suite_state needs to be available
suite_state = {}

# These need to be imported from block_ops or defined
def _block_id_at(ctx, x, y, z):
    """Get block ID at coordinates."""
    block = ctx.get_block(x, y, z)
    data = block.get("data", block)
    return data.get("id", "")

def _move_near(ctx, x, y, z, timeout=20.0):
    """Move to a suitable position near coordinates."""
    stand_x, stand_y, stand_z = _find_stand_pos(ctx, x, y, z, radius=3)
    target = {"x": stand_x, "y": stand_y, "z": stand_z}
    ok = do_goto(ctx, target, timeout=timeout, arrival_radius=2.5, require_arrival=False)
    if not ok and not _in_range(ctx, stand_x, stand_y, stand_z):
        ctx.log_event(f"Move failed near {x},{y},{z}")
        return False
    return True

def _find_stand_pos(ctx, x, y, z, radius: int = 3):
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

def _in_range(ctx, x, y, z, max_dist: float = 4.5):
    """Check if position is within interaction range."""
    px, py, pz = ctx.get_position()
    dx = px - x
    dy = py - y
    dz = pz - z
    return (dx * dx + dy * dy + dz * dz) ** 0.5 <= max_dist

def _is_liquid(block_id: str):
    """Check if block is a liquid."""
    return "water" in block_id or "lava" in block_id or "bubble_column" in block_id

def do_goto(ctx, target, timeout=20.0, arrival_radius=2.5, require_arrival=True):
    """Move to target position."""
    # This is a placeholder - need to implement based on actual movement system
    return True

def bot_place_block(ctx, x, y, z, block_type, allow_move=True):
    """Place a single block using survival-safe placement."""
    move_y = BASE_Y
    if allow_move:
        _move_near(ctx, x, move_y, z, timeout=15.0)
    return _place_block_at(ctx, x, y, z, block_type, allow_move=allow_move)

def _place_block_at(ctx, x, y, z, block_type, allow_break=True, allow_move=True):
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

def select_item(client, item_type, allow_swap=True):
    """Select item in inventory."""
    # This is a placeholder - need to implement based on actual inventory system
    return True

def wait_for_block(ctx, x, y, z, block_type, timeout=2.0):
    """Wait for block to appear at position."""
    # This is a placeholder - need to implement based on actual block checking
    return True, block_type

# These need to be available from the suite
def anchor(key: str):
    """Get world coordinates for a structure."""
    # This needs to be defined based on the layout
    return (0, BASE_Y, 0)

def _state(test_id: str):
    """Get state for a test."""
    return suite_state.setdefault(test_id, {})

# Import time for sleep
import time