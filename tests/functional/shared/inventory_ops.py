"""
Inventory Operations Module

Consolidated inventory management operations for functional tests.
Provides functions for inventory manipulation, chest operations, crafting, and smelting.

This module was refactored to unify shared functionality from actions/inventory_ops.py
and other disparate sources. The get_workshop_furnace function was moved here to provide
consistent access to furnace positions across all test suites.

Key functions:
- Inventory management: get_inventory_payload, select_item, safe_inventory_click
- Chest operations: open_supply_chest, withdraw_from_supply_chest, refresh_supply_slot_map
- Crafting: craft_bed_manual, ensure_crafting_table_open, craft_and_wait
- Smelting: smelt_in_furnace, get_workshop_furnace
"""

import time
from typing import Dict, List, Optional, Tuple, Union

from tests.functional.shared.block_ops import (
    block_id_at,
    move_near,
    place_block_at,
    bot_place_block,
    get_inv_slots,
    BASE_Y,
)
from tests.utils.mc_harness.interaction import robust_interact_block
from tests.utils.mc_harness.waits import close_screen
from tests.functional.shared.constants import (
    PLANK_ITEM_IDS,
    LOG_BLOCK_IDS,
    STRIPPED_LOG_BLOCK_IDS,
    WOOD_BLOCK_IDS,
    STRIPPED_WOOD_BLOCK_IDS,
)


def get_inventory_payload(ctx) -> List[Dict]:
    """
    Retrieve inventory list in a robust manner.

    Args:
        ctx: Test context

    Returns:
        List of inventory items
    """
    inv = ctx.get_inventory()
    if not inv:
        return []
    if isinstance(inv, dict):
        return inv.get("inventory", [])
    return inv


def get_slot_entry(inv_list: List[Dict], slot_idx: int) -> Optional[Dict]:
    """
    Find the inventory entry for a specific slot index.

    Args:
        inv_list: List of inventory items
        slot_idx: Slot index to find

    Returns:
        Inventory entry dict or None
    """
    for item in inv_list:
        if item.get("slot") == slot_idx:
            return item
    return None


def item_in_slot(inv_list: List[Dict], slot_idx: int) -> Tuple[Optional[str], int]:
    """
    Return (id, count) for a slot. Returns (None, 0) if empty.

    Args:
        inv_list: List of inventory items
        slot_idx: Slot index

    Returns:
        Tuple of (item_id, count)
    """
    item = get_slot_entry(inv_list, slot_idx)
    if not item:
        return None, 0
    return item.get("id"), item.get("count", 0)


def find_slot_with_item(inv_list: List[Dict], item_id: str) -> Optional[int]:
    """
    Find first slot containing specified item_id.

    Args:
        inv_list: List of inventory items
        item_id: Item ID to find

    Returns:
        Slot index or None
    """
    for item in inv_list:
        if item.get("id") == item_id and item.get("count", 0) > 0:
            return item.get("slot")
    return None


def find_empty_slot(inv_list: List[Dict], prefer_main: bool = True) -> Optional[int]:
    """
    Find an empty slot index.

    Args:
        inv_list: List of inventory items
        prefer_main: Prefer main inventory over hotbar

    Returns:
        Slot index or None
    """
    slots_to_check = list(range(9, 36)) + list(range(0, 9)) if prefer_main else list(range(0, 9)) + list(range(9, 36))
    occupied_slots = {item.get("slot") for item in inv_list if item.get("id") != "minecraft:air" and item.get("count", 0) > 0}

    for slot in slots_to_check:
        if slot not in occupied_slots:
            return slot
    return None




def select_item(ctx, item_id: str, allow_swap: bool = True) -> bool:
    """
    Select item in inventory (hotbar or swap to hotbar).

    Args:
        ctx: Test context
        item_id: Item ID to select
        allow_swap: Allow swapping from main inventory to hotbar

    Returns:
        True if item was selected
    """
    from baritone_client.common.inventory import find_item_slot
    from baritone_client.common.inventory import select_item as common_select

    # Use the common implementation
    return common_select(ctx.client, item_id, allow_swap)


def safe_inventory_click(ctx, slot: int, action: str, button: int = 0) -> None:
    """
    Perform a safe inventory click operation.

    Args:
        ctx: Test context
        slot: Slot index
        action: Action type (e.g., "PICKUP", "QUICK_MOVE")
        button: Button (0=left, 1=right)
    """
    ctx.client.transport.dispatch("inventory_click", {
        "slot": slot,
        "type": action,
        "button": button
    })
    time.sleep(0.1)  # Small delay for UI synchronization


def do_open_container(ctx, pos: Tuple[int, int, int], timeout: float = 3.0) -> bool:
    """
    Open container at specified position.

    Args:
        ctx: Test context
        pos: (x, y, z) coordinates
        timeout: Timeout for operation

    Returns:
        True if container was opened
    """
    # Check what's at the target position
    block_at = block_id_at(ctx, pos[0], pos[1], pos[2])
    px, py, pz = ctx.get_position()
    dist = ((px - pos[0]) ** 2 + (py - pos[1]) ** 2 + (pz - pos[2]) ** 2) ** 0.5
    
    ctx.log_event(f"Opening container at {pos}: block={block_at}, player=({px:.1f},{py:.1f},{pz:.1f}), dist={dist:.1f}")
    
    if "chest" not in block_at and "container" not in block_at and "crafting_table" not in block_at and "furnace" not in block_at and "shulker" not in block_at and "barrel" not in block_at:
        ctx.log_event(f"No interactable block at {pos}, found: {block_at}")
        return False
    
    if dist > 5.0:
        ctx.log_event(f"Too far from container at {pos}: {dist:.1f} blocks")
        return False

    block_lower = (block_at or "").lower()
    expected_total_slots = None
    if "crafting_table" in block_lower:
        expected_total_slots = {46}
    elif any(token in block_lower for token in ("furnace", "smoker", "blast_furnace")):
        expected_total_slots = {39}
    elif any(token in block_lower for token in ("chest", "barrel", "shulker")):
        expected_total_slots = {63, 90}

    # Ensure no stale GUI is open (crafting/inventory screens can otherwise "mask" chest opens).
    close_screen(ctx, timeout=min(1.0, timeout))
    ctx.client.transport.dispatch("close_screen", {})
    time.sleep(0.05)

    def _screen_matches_expected() -> Tuple[bool, Dict]:
        screen = ctx.client.transport.dispatch("get_screen", {})
        data = screen.get("data", screen)
        total_slots = int(data.get("total_slots") or 0)
        if total_slots <= 0:
            total_slots = len(get_inv_slots(data))
        if expected_total_slots is None:
            if total_slots > 0 and int(data.get("sync_id", -1)) != -1:
                return True, data
            return False, data
        return total_slots in expected_total_slots, data

    # Retry opening; we frequently see a stale 46-slot screen returned.
    attempts = 4
    per_attempt_timeout = max(0.3, timeout / attempts)
    last_data: Dict = {}
    for attempt in range(1, attempts + 1):
        close_screen(ctx, timeout=min(0.8, per_attempt_timeout))
        ctx.client.transport.dispatch("close_screen", {})
        time.sleep(0.05)

        ok_interact = robust_interact_block(
            ctx, pos[0], pos[1], pos[2], retries=6, interval=0.25
        )
        if not ok_interact:
            ctx.log_event(f"Container interact failed (attempt {attempt}/{attempts}) at {pos}")

        deadline = time.time() + per_attempt_timeout
        while time.time() < deadline:
            ok_screen, data = _screen_matches_expected()
            last_data = data
            if ok_screen:
                return True
            time.sleep(0.05)

        ctx.log_event(
            "Container UI mismatch after interact. "
            f"attempt={attempt}/{attempts} "
            f"expected_total_slots={sorted(expected_total_slots) if expected_total_slots else None} "
            f"screen_type={last_data.get('type', '')} sync_id={last_data.get('sync_id', -1)} "
            f"total_slots={last_data.get('total_slots', 0)}"
        )

    ctx.log_event(
        "Container UI did not open. "
        f"expected_total_slots={sorted(expected_total_slots) if expected_total_slots else None} "
        f"screen_type={last_data.get('type', '')} sync_id={last_data.get('sync_id', -1)} total_slots={last_data.get('total_slots', 0)}"
    )
    return False


def do_close_container(ctx) -> None:
    """
    Close the current container screen.

    Args:
        ctx: Test context
    """
    close_screen(ctx, timeout=1.0)
    ctx.client.transport.dispatch("close_screen", {})
    time.sleep(0.05)


def withdraw_from_supply_chest(ctx, suite_state: Dict, item_counts: Dict[str, int]) -> bool:
    """
    Withdraw specified items from supply chests.

    Args:
        ctx: Test context
        suite_state: Suite state dict
        item_counts: Dict of item_id -> count needed

    Returns:
        True if withdrawal successful
    """
    slot_map = refresh_supply_slot_map(ctx, suite_state)
    if not slot_map:
        ctx.log_event("No supply slot map available")
        return False

    chest_positions = suite_state.get("supply_chest_positions")
    if not chest_positions:
        chest_positions = suite_state.get("T1000", {}).get("chest_positions")

    if not chest_positions:
        pos = suite_state.get("T1000", {}).get("chest_pos") or (0, BASE_Y, 0)
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

    slots_to_move = {}
    for item_id, needed in item_counts.items():
        entries = slot_map.get(item_id, [])
        if not entries:
            ctx.log_event(f"No entries in slot_map for {item_id}")
            continue
        remaining = needed
        for entry in entries:
            key = entry["chest"]
            slots_to_move.setdefault(key, []).append(entry["slot"])
            remaining -= entry["count"]
            if remaining <= 0:
                break
    
    ctx.log_event(f"Slots to move by chest: {slots_to_move}")

    overall_ok = True
    for i, pos in enumerate(chest_positions):
        key = "main" if i == 0 else f"overflow_{i}"
        target_slots = slots_to_move.get(key)
        if not target_slots:
            ctx.log_event(f"No slots to move for {key} at {pos}")
            continue

        ctx.log_event(f"Moving items from {key} chest at {pos}: slots {target_slots}")
        
        # Move to chest before opening
        move_near(ctx, pos[0], pos[1], pos[2], timeout=10.0)
        
        if not do_open_container(ctx, pos, timeout=3.0):
            ctx.log_event(f"Failed to open {key} chest at {pos}")
            overall_ok = False
            continue

        processed_slots = set()
        for slot in target_slots:
            if slot in processed_slots:
                continue
            ctx.log_event(f"Quick-moving slot {slot} from {key}")
            safe_inventory_click(ctx, slot, "QUICK_MOVE")
            time.sleep(0.05)
            processed_slots.add(slot)

        do_close_container(ctx)

    return overall_ok


def ensure_item_from_supply(ctx, item_id: str, count: int, suite_state: Dict) -> bool:
    """
    Ensure specific item count is available from supply.

    Args:
        ctx: Test context
        item_id: Item ID
        count: Required count
        suite_state: Suite state dict

    Returns:
        True if item is available
    """
    if ctx.has_item(item_id, count):
        return True
    if not open_supply_chest(ctx, suite_state):
        ctx.log_event(f"Failed to open supply chest to fetch {item_id}")
        return False
    screen = ctx.client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    slots = get_inv_slots(data)
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
    slots = get_inv_slots(data)
    for slot in slots:
        if slot.get("id") == item_id and slot.get("slot") is not None and slot.get("slot") < 27:
            safe_inventory_click(ctx, slot["slot"], "QUICK_MOVE")
            time.sleep(0.05)
            if ctx.has_item(item_id, count):
                break
    do_close_container(ctx)
    return ctx.has_item(item_id, count)


def smelt_in_furnace(ctx, furnace_pos: Tuple[int, int, int], input_id: str, fuel_id: str,
                      output_id: str, output_count: int, wait_per_item: float = 10.5) -> bool:
    """
    Smelt items in a furnace.

    Places a furnace if none exists, loads it with input and fuel items,
    waits for smelting to complete, then retrieves the output.

    Args:
        ctx: Test context
        furnace_pos: (x, y, z) coordinates of the furnace
        input_id: Item ID to smelt (e.g., "minecraft:iron_ore")
        fuel_id: Fuel item ID (e.g., "minecraft:coal")
        output_id: Expected output item ID (e.g., "minecraft:iron_ingot")
        output_count: Minimum number of output items required
        wait_per_item: Seconds to wait per item being smelted

    Returns:
        True if smelting successful and output items obtained.

    Example:
        >>> # Smelt 8 iron ore into iron ingots
        >>> success = smelt_in_furnace(ctx, (10, 80, 10), "minecraft:iron_ore",
        ...                            "minecraft:coal", "minecraft:iron_ingot", 8)
        >>> assert success
    """
    if "furnace" not in block_id_at(ctx, furnace_pos[0], furnace_pos[1], furnace_pos[2]):
        if not bot_place_block(ctx, furnace_pos[0], furnace_pos[1], furnace_pos[2], "minecraft:furnace"):
            ctx.log_event("Failed to place furnace for smelting")
            return False

    if not do_open_container(ctx, furnace_pos, timeout=3.0):
        ctx.log_event("Failed to open furnace for smelting")
        return False

    screen = ctx.client.transport.dispatch("get_screen", {})
    slots = get_inv_slots(screen.get("data", screen))

    input_slot_idx = None
    fuel_slot_idx = None

    for i, item in enumerate(slots):
        if i < 3:
            continue
        if not item:
            continue
        if item.get("id") == input_id and input_slot_idx is None:
            input_slot_idx = i
        elif item.get("id") == fuel_id and fuel_slot_idx is None:
            fuel_slot_idx = i

    if input_slot_idx is None or fuel_slot_idx is None:
        ctx.log_event(f"Missing smelt items in inventory")
        do_close_container(ctx)
        return False

    safe_inventory_click(ctx, fuel_slot_idx, "QUICK_MOVE")
    time.sleep(0.3)
    safe_inventory_click(ctx, input_slot_idx, "QUICK_MOVE")
    time.sleep(0.3)

    time.sleep(max(12.0, output_count * wait_per_item))

    safe_inventory_click(ctx, 2, "QUICK_MOVE")
    time.sleep(0.3)

    do_close_container(ctx)
    return ctx.has_item(output_id, output_count)


def craft_bed_manual(ctx, bed_id: str = "minecraft:white_bed") -> bool:
    """
    Manually craft a bed through crafting interface.

    Requires wool and planks in inventory. Uses the 3x3 crafting grid with
    wool in the top row and planks in the middle row.

    Args:
        ctx: Test context
        bed_id: Bed item ID (e.g., "minecraft:white_bed")

    Returns:
        True if crafting successful

    Example:
        >>> # Craft a white bed (requires 3 wool and 3 planks in inventory)
        >>> success = craft_bed_manual(ctx, "minecraft:white_bed")
        >>> assert success
    """
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

    def find_slot(predicate, min_count: int = 1):
        for slot in slots:
            if predicate(slot) and slot.get("count", 0) >= min_count:
                return slot.get("slot")
        return None

    wool_slot = find_slot(lambda s: s.get("id", "").endswith("_wool"), min_count=3)
    plank_slot = find_slot(lambda s: s.get("id", "").endswith("_planks"), min_count=3)
    if wool_slot is None or plank_slot is None:
        ctx.log_event(f"Bed craft missing ingredients")
        return False

    for grid_slot in range(1, 10):
        slot_info = next((s for s in slots if s.get("slot") == grid_slot), None)
        if slot_info and slot_info.get("id") not in ("minecraft:air", None) and slot_info.get("count", 0) > 0:
            safe_inventory_click(ctx, grid_slot, "QUICK_MOVE")
            time.sleep(0.05)

    def place_one_each(source_slot: int, target_slots: List[int]) -> None:
        safe_inventory_click(ctx, source_slot, "PICKUP", button=0)
        time.sleep(0.05)
        for target in target_slots:
            safe_inventory_click(ctx, target, "PICKUP", button=1)
            time.sleep(0.05)
        safe_inventory_click(ctx, source_slot, "PICKUP", button=0)
        time.sleep(0.05)

    place_one_each(wool_slot, [1, 2, 3])
    place_one_each(plank_slot, [4, 5, 6])

    safe_inventory_click(ctx, 0, "QUICK_MOVE")
    time.sleep(0.1)
    return ctx.has_item(bed_id)


def craft_door_manual(ctx, door_id: str = "minecraft:oak_door") -> bool:
    """
    Manually craft a door (2 columns of 3 planks).
    """
    ctx.log_event("Door craft: Starting manual door crafting")
    
    # Wait a moment for screen to fully open
    time.sleep(0.5)
    
    # Try getting screen with retry
    slots = []
    for attempt in range(5):
        screen = ctx.client.transport.dispatch("get_screen", {})
        data = screen.get("data", screen)
        slots = data.get("slots", [])
        if slots and len(slots) > 10:
            break
        ctx.log_event(f"Door craft: Wait for screen (attempt {attempt+1}, got {len(slots)} slots)")
        time.sleep(0.5)
    
    ctx.log_event(f"Door craft: Got screen with {len(slots)} slots")
    
    if not slots:
        ctx.log_event("Door craft failed: no slots in screen")
        return False
    
    # Debug: log all plank slots found
    plank_slots_found = []
    for slot in slots:
        slot_idx = slot.get("slot", -1)
        item_id = slot.get("id", "")
        count = slot.get("count", 0)
        if "_planks" in item_id and count > 0:
            plank_slots_found.append(f"slot{slot_idx}:{item_id}x{count}")
    ctx.log_event(f"Door craft: Plank slots found: {plank_slots_found}")
        
    def find_plank_slot(min_count: int = 6):
        """Find a plank slot in player inventory (slots 10+ for crafting table screen)."""
        for slot in slots:
            slot_idx = slot.get("slot", -1)
            # Crafting table screen: slots 1-9 are grid, 0 is output, 10+ is player inventory
            if slot_idx < 10:
                continue
            if slot.get("id", "").endswith("_planks") and slot.get("count", 0) >= min_count:
                return slot_idx
        return None
        
    plank_slot = find_plank_slot(min_count=6)
    if plank_slot is None:
        ctx.log_event("Door craft failed: missing 6 planks in inventory (checked slots 10+)")
        return False
        
    ctx.log_event(f"Door craft: found planks at slot {plank_slot}")
    
    # Recipe: Left and Middle columns (1,4,7 and 2,5,8)
    target_slots = [1, 4, 7, 2, 5, 8]
    
    def place_one_each(source_slot: int, target_slots: List[int]) -> None:
        safe_inventory_click(ctx, source_slot, "PICKUP", button=0)
        time.sleep(0.05)
        for target in target_slots:
            safe_inventory_click(ctx, target, "PICKUP", button=1)
            time.sleep(0.05)
        safe_inventory_click(ctx, source_slot, "PICKUP", button=0)
        time.sleep(0.05)
        
    place_one_each(plank_slot, target_slots)
    
    # Check result slot before taking
    screen = ctx.client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    result_slot = None
    for s in data.get("slots", []):
        if s.get("slot") == 0:
            result_slot = s
            break
    ctx.log_event(f"Door craft: Result slot before taking: {result_slot}")
    
    safe_inventory_click(ctx, 0, "QUICK_MOVE")
    time.sleep(0.3)
    
    # Check if door is in inventory using screen slots (not base inventory)
    screen = ctx.client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    for s in data.get("slots", []):
        if s.get("slot", -1) >= 10 and "_door" in s.get("id", ""):
            ctx.log_event(f"Door craft: Found door in slot {s.get('slot')}")
            return True
    
    ctx.log_event("Door craft: Door not found in inventory after crafting")
    return False


def ensure_crafting_table_open(ctx, table_pos: Optional[Tuple[int, int, int]] = None, suite_state: Optional[Dict] = None) -> bool:
    """
    Ensure crafting table is available and open.

    Args:
        ctx: Test context
        table_pos: Optional table position
        suite_state: Optional suite state

    Returns:
        True if table is open
    """
    px, py, pz = ctx.get_position()
    if table_pos is None:
        from tests.functional.shared.block_ops import find_place_pos_near
        table_pos = find_place_pos_near(ctx, int(px) + 1, int(py), int(pz))
    elif isinstance(table_pos, dict):
        table_pos = (table_pos.get("x", int(px)), table_pos.get("y", int(py)), table_pos.get("z", int(pz)))

    ctx.log_event(f"Crafting table target position: {table_pos}")
    candidates = [table_pos]
    candidates.append((table_pos[0] + 1, table_pos[1], table_pos[2]))
    candidates.append((table_pos[0] - 1, table_pos[1], table_pos[2]))
    candidates.append((table_pos[0], table_pos[1], table_pos[2] + 1))
    candidates.append((table_pos[0], table_pos[1], table_pos[2] - 1))

    for i, (tx, ty, tz) in enumerate(candidates):
        tx, ty, tz = int(tx), int(ty), int(tz)

        current_block = block_id_at(ctx, tx, ty, tz)
        if current_block and "air" not in current_block and "crafting_table" not in current_block:
            continue

        px, py, pz = ctx.get_position()
        if int(px) == tx and int(pz) == tz:
            move_near(ctx, tx + 2, ty, tz, timeout=5.0)

        move_near(ctx, tx, ty, tz, timeout=8.0)

        # Retry block detection to handle timing issues (block may be placed but state not updated yet)
        for check_attempt in range(5):
            detected_block = block_id_at(ctx, tx, ty, tz)
            if "crafting_table" in detected_block:
                if suite_state is not None:
                    suite_state["crafting_table_pos"] = (tx, ty, tz)
                return bool(do_open_container(ctx, (tx, ty, tz), timeout=3.0))
            if check_attempt < 4:
                time.sleep(0.5)

        if bot_place_block(ctx, tx, ty, tz, "minecraft:crafting_table", allow_move=True):
            if suite_state is not None:
                suite_state["crafting_table_pos"] = (tx, ty, tz)
            if do_open_container(ctx, (tx, ty, tz), timeout=3.0):
                return True

        ctx.log_event(f"Candidate {i} failed at {tx},{ty},{tz}")

    ctx.log_event("All crafting table placement candidates failed.")
    return False


def open_supply_chest(ctx, suite_state: Dict) -> bool:
    """
    Open the supply chest at configured position.

    Args:
        ctx: Test context
        suite_state: Suite state dict

    Returns:
        True if chest opened
    """
    pos = suite_state.get("T1000", {}).get("chest_pos")
    if not pos:
        pos = (0, BASE_Y, 0)
    move_near(ctx, pos[0], pos[1], pos[2], timeout=15.0)
    return bool(do_open_container(ctx, pos, timeout=3.0))


_MISSING_ITEM_FALLBACKS = {
    "#logs": "minecraft:oak_log",
    "#planks": "minecraft:oak_planks",
    "#coals": "minecraft:coal",
    "#wool": "minecraft:white_wool",
    "#stone_tool_material": "minecraft:cobblestone",
    "any_logs": "minecraft:oak_log",
    "any_planks": "minecraft:oak_planks",
    "any_coal": "minecraft:coal",
    "any_wool": "minecraft:white_wool",
}


def _normalize_missing_requirements(missing: List[Dict]) -> Dict[str, int]:
    normalized: Dict[str, int] = {}
    for entry in missing:
        item_id = entry.get("item") or entry.get("id")
        count = entry.get("count", 0)
        if not item_id or count <= 0:
            continue
        if item_id in {"unknown", "minecraft:air"}:
            continue
        item_id = _MISSING_ITEM_FALLBACKS.get(item_id, item_id)
        normalized[item_id] = normalized.get(item_id, 0) + int(count)
    return normalized


def _attempt_withdraw_for_craft(ctx, item_id: str, count: int, suite_state: Dict) -> None:
    from baritone_client.common.inventory import check_craft

    try:
        craft_info = check_craft(ctx.client, item_id, count=count)
    except Exception as exc:
        ctx.log_event(f"Craft precheck failed for {item_id}: {exc}")
        return

    missing = craft_info.get("missing", [])
    needed = _normalize_missing_requirements(missing)
    if not needed:
        return

    if not withdraw_from_supply_chest(ctx, suite_state, needed):
        ctx.log_event(f"Supply withdrawal failed for {item_id}: {needed}")


def craft_and_wait(
    ctx,
    item_id: str,
    count: int = 1,
    timeout: float = 5.0,  # Increased default from 2.0
    batch: int = 16,
    suite_state: Optional[Dict] = None,
) -> bool:
    """
    Craft an item and wait for it to appear.

    Args:
        ctx: Test context
        item_id: Item to craft
        count: Number to craft
        timeout: Wait timeout
        batch: Batch size for large craft counts
        suite_state: Optional suite state for supply chest withdrawals

    Returns:
        True if crafting successful
    """
    if count <= 0:
        return True

    if suite_state is not None:
        _attempt_withdraw_for_craft(ctx, item_id, count, suite_state)

    batch = max(1, batch)
    use_batches = count > batch
    remaining = count
    crafted = 0
    start_count = ctx.count_item(item_id)
    
    ctx.log_event(f"DEBUG: Crafting {item_id} x{count}. Start count: {start_count}")

    while remaining > 0:
        craft_count = remaining if not use_batches else min(batch, remaining)
        ctx.client.transport.dispatch("craft", {"item": item_id, "count": craft_count})
        target_total = start_count + crafted + craft_count
        
        # Manual wait with debug logging
        wait_start = time.time()
        curr_count = 0
        success = False
        while time.time() - wait_start < timeout:
             curr_count = ctx.count_item(item_id)
             if curr_count >= target_total:
                 success = True
                 break
             time.sleep(0.5)
             
        if not success:
            ctx.log_event(f"Craft failed for {item_id} batch {craft_count}. Wanted {target_total}, got {curr_count}")
            try:
                debug_inv = get_inv_slots(ctx.get_inventory())
                ctx.log_event(f"DEBUG: Inv dump: {debug_inv}")
            except Exception as e:
                ctx.log_event(f"DEBUG: Failed to dump inv: {e}")
            return False
            
        remaining -= craft_count
        crafted += craft_count

    return True


def refresh_supply_slot_map(ctx, suite_state: Dict) -> Dict[str, List[Dict]]:
    """
    Refresh supply chest slot map.

    Args:
        ctx: Test context
        suite_state: Suite state dict

    Returns:
        Slot map dict
    """
    slot_map = {}

    def record(item_id: str, chest_key: str, slot_idx: int, count: int) -> None:
        slot_map.setdefault(item_id, []).append(
            {"chest": chest_key, "slot": slot_idx, "count": count}
        )

    chest_positions = suite_state.get("supply_chest_positions")
    if not chest_positions:
        chest_positions = suite_state.get("T1000", {}).get("chest_positions")

    if not chest_positions:
        fallback_pos = suite_state.get("T1000", {}).get("chest_pos") or (0, BASE_Y, 0)
        ctx.log_event(
            "Supply chest positions missing; using fallback. "
            f"supply_chest_positions={suite_state.get('supply_chest_positions')} "
            f"t1000_chest_positions={suite_state.get('T1000', {}).get('chest_positions')} "
            f"t1000_chest_pos={suite_state.get('T1000', {}).get('chest_pos')} "
            f"fallback={fallback_pos}"
        )
        chest_positions = [fallback_pos]

    chest_positions = list(chest_positions)

    overflow_pos = suite_state.get("supply_extra_pos")
    if overflow_pos:
        chest_positions.append(overflow_pos)

    for i, pos in enumerate(chest_positions):
        label = "main" if i == 0 else f"overflow_{i}"

        do_close_container(ctx)
        move_near(ctx, pos[0], pos[1], pos[2], timeout=10.0)

        meta = suite_state.get("chest_meta", {}).get(pos)
        expected_container_slots = 27
        if meta:
            expected_container_slots = int(meta.get("slots", 27))
        expected_total_slots = expected_container_slots + 36

        opened = do_open_container(ctx, pos, timeout=3.0)
        if not opened:
            ctx.log_event(f"Failed to open supply chest {i} at {pos}")
            continue

        screen = ctx.client.transport.dispatch("get_screen", {})
        data = screen.get("data", screen)
        slots = get_inv_slots(data)
        total_slots = int(data.get("total_slots") or len(slots))

        if not slots:
            ctx.log_event(f"Refresh failed: no slots in screen for {label}")
            do_close_container(ctx)
            continue

        if total_slots != expected_total_slots:
            ctx.log_event(
                f"Chest at {pos} opened with unexpected slot count: total_slots={total_slots} expected_total_slots={expected_total_slots}"
            )
            do_close_container(ctx)
            # Retry once: stale GUIs can cause a crafting screen (46 slots) to be returned.
            if not do_open_container(ctx, pos, timeout=3.0):
                ctx.log_event(f"Refresh failed: could not reopen chest {label} at {pos}")
                continue
            screen = ctx.client.transport.dispatch("get_screen", {})
            data = screen.get("data", screen)
            slots = get_inv_slots(data)
            total_slots = int(data.get("total_slots") or len(slots))
            if total_slots != expected_total_slots or len(slots) < expected_total_slots:
                ctx.log_event(
                    f"Refresh failed: chest {label} at {pos} still has wrong slot count: total_slots={total_slots} expected_total_slots={expected_total_slots}"
                )
                do_close_container(ctx)
                continue

        for slot_idx in range(expected_container_slots):
            item = slots[slot_idx]
            if item:
                record(item["id"], label, slot_idx, item["count"])

        do_close_container(ctx)

    suite_state["supply_slot_map"] = slot_map
    return slot_map


# Additional functions from actions/inventory_ops.py

def fill_supply_chests(ctx, x: int, y: int, z: int, items: Dict[str, int], chest_positions: List[Tuple[int, int, int]], chest_meta: Dict) -> Dict[str, List[Dict]]:
    """
    Fill supply chests with items.

    Args:
        ctx: Test context
        x, y, z: Base position
        items: Item dict
        chest_positions: List of chest positions
        chest_meta: Chest metadata

    Returns:
        Slot map
    """
    total_slots = 0
    for item_id, count in items.items():
        total_slots += (count + 63) // 64

    ctx.log_event(f"Supply needs {total_slots} slots, using up to 4 double chests (216 slots)")

    for i, (cx, cy, cz) in enumerate(chest_positions):
        block_id = block_id_at(ctx, cx, cy, cz)
        if "chest" not in block_id:
            ctx.require(False, f"Supply chest {i+1} missing at {cx},{cy},{cz} (found {block_id})")

    slot_assignments = []
    for item_id, count in items.items():
        remaining = count
        while remaining > 0:
            stack_size = min(remaining, 64)
            slot_assignments.append((item_id, stack_size))
            remaining -= stack_size

    slot_map = {}
    def record(item_id: str, chest_key: str, slot_idx: int, count: int) -> None:
        slot_map.setdefault(item_id, []).append(
            {"chest": chest_key, "slot": slot_idx, "count": count}
        )

    slot_idx = 0
    for chest_num, (cx, cy, cz) in enumerate(chest_positions):
        chest_key = "main" if chest_num == 0 else f"overflow_{chest_num}"
        meta = chest_meta.get((cx, cy, cz), {"slots": 54})
        max_slots = meta.get("slots", 54)

        for local_slot in range(max_slots):
            if slot_idx >= len(slot_assignments):
                break
            item_id, stack_size = slot_assignments[slot_idx]
            ctx.run_command(f"item replace block {cx} {cy} {cz} container.{local_slot} with {item_id} {stack_size}")
            record(item_id, chest_key, local_slot, stack_size)
            slot_idx += 1

    if len(slot_assignments) > 216:
        ctx.log_event(f"WARNING: {len(slot_assignments) - 216} stacks don't fit in 4 double chests!")

    return slot_map


def deposit_inventory_to_supply_chest(ctx, chest_positions: List[Tuple[int, int, int]], chest_meta: Dict) -> bool:
    """
    Deposit inventory to supply chests.

    Args:
        ctx: Test context
        chest_positions: Chest positions
        chest_meta: Chest metadata

    Returns:
        True if deposited
    """
    def quick_move_range(start_slot: int, end_slot: int) -> None:
        for slot in range(start_slot, end_slot + 1):
            safe_inventory_click(ctx, slot, "QUICK_MOVE")
            time.sleep(0.05)

    for i, pos in enumerate(chest_positions):
        # Move to chest before opening
        move_near(ctx, pos[0], pos[1], pos[2], timeout=10.0)

        if not do_open_container(ctx, pos, timeout=3.0):
            ctx.log_event(f"Failed to open supply chest {i} for deposit")
            continue

        meta = chest_meta.get(pos)
        screen = ctx.client.transport.dispatch("get_screen", {})
        data = screen.get("data", screen)
        slots = get_inv_slots(data)

        if not slots:
            do_close_container(ctx)
            continue

        num_container_slots = len(slots) - 36
        if meta:
            num_container_slots = meta.get("slots", num_container_slots)

        player_start = num_container_slots
        player_end = len(slots) - 1

        quick_move_range(player_start, player_end)
        do_close_container(ctx)

        if ctx.wait_for_inventory_clear(timeout=1.0):
            return True

    return ctx.wait_for_inventory_clear(timeout=2.0)


def place_double_chest(ctx, x: int, y: int, z: int, base_y: int) -> bool:
    """
    Place double chest.

    Args:
        ctx: Test context
        x, y, z: Position
        base_y: Base Y

    Returns:
        True if placed
    """
    move_near(ctx, x, base_y, z - 2, timeout=10.0)
    ctx.client.transport.dispatch("look_at", {"x": x + 0.5, "y": y + 0.5, "z": z + 5.5})
    ok = place_block_at(ctx, x, y, z, "minecraft:chest", allow_move=False)
    ctx.client.transport.dispatch("look_at", {"x": x + 1.5, "y": y + 0.5, "z": z + 5.5})
    ok = place_block_at(ctx, x + 1, y, z, "minecraft:chest", allow_move=False) and ok
    return ok


def build_slot_map_from_screen(slots: list, chest_key: str, max_slots: int) -> dict:
    """
    Build slot map from screen slots data.

    Args:
        slots: List of slot dicts
        chest_key: Key for the chest
        max_slots: Maximum number of slots

    Returns:
        Slot map dict
    """
    slot_map = {}

    def record(item_id: str, slot_idx: int, count: int) -> None:
        slot_map.setdefault(item_id, []).append(
            {"chest": chest_key, "slot": slot_idx, "count": count}
        )

    for slot_idx in range(max_slots):
        item = slots[slot_idx]
        if item and item.get("id") and item.get("count", 0) > 0:
            record(item["id"], slot_idx, item["count"])

    return slot_map


def get_workshop_furnace(suite_state: Dict) -> Tuple[int, int, int]:
    """
    Get the workshop furnace position, with fallback to default beyond supply chests.

    Args:
        suite_state: Suite state dict

    Returns:
        (x, y, z) coordinates
    """
    if suite_state.get("furnace_pos"):
        return suite_state["furnace_pos"]
    # Fallback: beyond the 4 double chests (x to x+10)
    pos = suite_state.get("T1000", {}).get("chest_pos") or (0, BASE_Y, 0)
    return (pos[0] + 13, pos[1], pos[2])


def craft_wooden_axe_manual(ctx) -> bool:
    """
    Manually craft wooden axe in 3x3 grid.
    
    Args:
        ctx: Test context
    
    Returns:
        True if crafting successful
    """
    return _craft_tool_manual_generic(ctx, "_planks", "axe", "minecraft:wooden_axe")


def craft_chest_manual(ctx) -> bool:
    """
    Manual chest crafting sequence.
    
    Args:
        ctx: Test context
        
    Returns:
        True if crafting successful
    """
    ctx.log_event("Starting manual chest click sequence...")
    # 1. Take result slot if any (clear it)
    safe_inventory_click(ctx, 0, "QUICK_MOVE", 0)
    time.sleep(0.2)
    
    # 2. Find planks from screen slots (NOT base inventory - slot numbers are different!)
    # When crafting table is open: 0=output, 1-9=grid, 10-36=main inventory, 37-45=hotbar
    screen = ctx.client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    screen_slots = data.get("slots", [])
    plank_slots = []
    total_planks = 0
    for s in screen_slots:
        slot_id = s.get("slot", -1)
        item_id = s.get("id", "")
        count = s.get("count", 0)
        # Player inventory starts at slot 10 in crafting table screen
        if slot_id >= 10 and item_id.endswith("_planks") and count > 0:
            plank_slots.append((slot_id, count))
            total_planks += count
            if total_planks >= 8:
                break

    if total_planks < 8:
        ctx.log_event(f"ERROR: Not enough planks (have {total_planks}, need 8)")
        return False
    
    ctx.log_event(f"Found plank slots for chest: {plank_slots}, total: {total_planks}")

    # 3. Place in 'O' shape: 1,2,3, 4,6, 7,8,9 (middle 5 empty)
    chest_pattern = [1, 2, 3, 4, 6, 7, 8, 9]
    slot_idx = 0
    remaining = 0
    current_slot = None
    for grid_slot in chest_pattern:
        if remaining == 0:
            if slot_idx >= len(plank_slots):
                ctx.log_event("ERROR: Ran out of planks during chest craft")
                return False
            current_slot, remaining = plank_slots[slot_idx]
            slot_idx += 1
            safe_inventory_click(ctx, current_slot, "PICKUP", 0)
            time.sleep(0.1)

        safe_inventory_click(ctx, grid_slot, "PICKUP", 1) # Right click to place 1
        time.sleep(0.1)
        remaining -= 1

        if remaining == 0 and current_slot is not None:
            safe_inventory_click(ctx, current_slot, "PICKUP", 0)
            time.sleep(0.1)
            current_slot = None

    if remaining > 0 and current_slot is not None:
        safe_inventory_click(ctx, current_slot, "PICKUP", 0)
        time.sleep(0.1)
    
    # Debug: Check screen state before taking result
    screen = ctx.client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    result_slot = None
    for s in data.get("slots", []):
        if s.get("slot") == 0:
            result_slot = s
            break
    ctx.log_event(f"Result slot (0) before taking: {result_slot}")
    
    # 6. Take result
    safe_inventory_click(ctx, 0, "QUICK_MOVE", 0)
    time.sleep(0.5)
    
    if ctx.has_item("minecraft:chest"):
        ctx.log_event("Manual chest crafting successful!")
        return True
    
    ctx.log_event("ERROR: Chest not in inventory after crafting sequence")
    return False

# --- Generic Manual Crafting Helpers ---

def _craft_tool_manual_generic(ctx, material_id_or_tag: str, tool_type: str, result_id: str) -> bool:
    """
    Generic manual tool crafting helper.

    Args:
        ctx: Test context
        material_id_or_tag: Item ID substring (e.g. "_planks", "minecraft:cobblestone")
        tool_type: "axe", "pickaxe", "shovel", "sword", "hoe"
        result_id: Expected result item ID (e.g. "minecraft:stone_axe")

    Returns:
        True if successful.
    """
    TOOL_Recipes = {
        "axe": {"material": [1, 2, 4], "stick": [5, 8], "mat_count": 3},
        "pickaxe": {"material": [1, 2, 3], "stick": [5, 8], "mat_count": 3},
        "shovel": {"material": [2], "stick": [5, 8], "mat_count": 1},
        "sword": {"material": [2, 5], "stick": [8], "mat_count": 2},
        "hoe": {"material": [1, 2], "stick": [5, 8], "mat_count": 2},
    }

    recipe = TOOL_Recipes.get(tool_type)
    if not recipe:
        ctx.log_event(f"Unknown tool type: {tool_type}")
        return False

    screen = ctx.client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    slots = data.get("slots", [])
    
    mat_slot = -1
    stick_slot = -1
    
    # 1. Check ingredients
    for s in slots:
        sid = s.get("id", "")
        count = s.get("count", 0)
        
        # Check material
        if material_id_or_tag in sid and count >= recipe["mat_count"]:
            # Special case for planks vs logs vs sticks
            if "stick" not in sid: 
                mat_slot = s.get("slot")
        
        # Check sticks
        if "stick" in sid and count >= len(recipe["stick"]):
            stick_slot = s.get("slot")

    if mat_slot == -1 or stick_slot == -1:
        ctx.log_event(f"Manual {tool_type}: Missing ingredients (mat_slot={mat_slot}, stick_slot={stick_slot})")
        return False

    ctx.log_event(f"Placing {tool_type} recipe: material from {mat_slot}, sticks from {stick_slot}")

    # 2. Clear output slot
    safe_inventory_click(ctx, 0, "QUICK_MOVE", 0)
    time.sleep(0.1)

    # 3. Place Materials
    safe_inventory_click(ctx, mat_slot, "PICKUP", 0)
    time.sleep(0.1)
    for grid_idx in recipe["material"]:
        safe_inventory_click(ctx, grid_idx, "PICKUP", 1) # Right click place 1
        time.sleep(0.1)
    safe_inventory_click(ctx, mat_slot, "PICKUP", 0) # Return rest
    time.sleep(0.1)

    # 4. Place Sticks
    safe_inventory_click(ctx, stick_slot, "PICKUP", 0)
    time.sleep(0.1)
    for grid_idx in recipe["stick"]:
        safe_inventory_click(ctx, grid_idx, "PICKUP", 1)
        time.sleep(0.1)
    safe_inventory_click(ctx, stick_slot, "PICKUP", 0) # Return rest
    time.sleep(0.1)

    # 5. Take Result
    safe_inventory_click(ctx, 0, "QUICK_MOVE", 0)
    time.sleep(0.3)
    
    do_close_container(ctx)
    time.sleep(0.2)

    return ctx.has_item(result_id)

def craft_stone_axe_manual(ctx) -> bool:
    return _craft_tool_manual_generic(ctx, "minecraft:cobblestone", "axe", "minecraft:stone_axe")

def craft_iron_axe_manual(ctx) -> bool:
    return _craft_tool_manual_generic(ctx, "minecraft:iron_ingot", "axe", "minecraft:iron_axe")

def craft_golden_axe_manual(ctx) -> bool:
    return _craft_tool_manual_generic(ctx, "minecraft:gold_ingot", "axe", "minecraft:golden_axe")

def craft_diamond_axe_manual(ctx) -> bool:
    return _craft_tool_manual_generic(ctx, "minecraft:diamond", "axe", "minecraft:diamond_axe")

def craft_stone_pickaxe_manual(ctx) -> bool:
    return _craft_tool_manual_generic(ctx, "minecraft:cobblestone", "pickaxe", "minecraft:stone_pickaxe")

def craft_iron_pickaxe_manual(ctx) -> bool:
    return _craft_tool_manual_generic(ctx, "minecraft:iron_ingot", "pickaxe", "minecraft:iron_pickaxe")

def craft_diamond_pickaxe_manual(ctx) -> bool:
    return _craft_tool_manual_generic(ctx, "minecraft:diamond", "pickaxe", "minecraft:diamond_pickaxe")

def craft_stone_shovel_manual(ctx) -> bool:
    return _craft_tool_manual_generic(ctx, "minecraft:cobblestone", "shovel", "minecraft:stone_shovel")

def craft_iron_shovel_manual(ctx) -> bool:
    return _craft_tool_manual_generic(ctx, "minecraft:iron_ingot", "shovel", "minecraft:iron_shovel")

def craft_stone_sword_manual(ctx) -> bool:
    return _craft_tool_manual_generic(ctx, "minecraft:cobblestone", "sword", "minecraft:stone_sword")

def craft_iron_sword_manual(ctx) -> bool:
    return _craft_tool_manual_generic(ctx, "minecraft:iron_ingot", "sword", "minecraft:iron_sword")

def craft_stone_hoe_manual(ctx) -> bool:
    return _craft_tool_manual_generic(ctx, "minecraft:cobblestone", "hoe", "minecraft:stone_hoe")

def craft_iron_hoe_manual(ctx) -> bool:
    return _craft_tool_manual_generic(ctx, "minecraft:iron_ingot", "hoe", "minecraft:iron_hoe")

# --- Advanced Helpers (Refactored) ---

def equip_item_to_hotbar(ctx, slot: int, hotbar_idx: int = 0):
    """
    Move an item from inventory slot to hotbar slot (default 0).
    """
    try:
        # Swap with hotbar slot (slots 36-44 are hotbar in player inventory screen)
        # Note: This assumes standard inventory screen numbering where hotbar is 36-44 or similar.
        # But safe_inventory_click usually handles the raw slot ID. 
        # For 'swap' to hotbar via number key, we can use 'SWAP' mode if needed, 
        # but here we follow the click-click pattern from the original suite.
        
        target_hotbar_slot = 36 + hotbar_idx # 36 is usually hotbar slot 0 in main inventory container
        
        safe_inventory_click(ctx, slot, "PICKUP", 0)
        time.sleep(0.1)
        safe_inventory_click(ctx, target_hotbar_slot, "PICKUP", 0)
        time.sleep(0.1)
        # If there was something in hotbar, put it back in the original slot
        safe_inventory_click(ctx, slot, "PICKUP", 0)
        time.sleep(0.1)
    except Exception as e:
        ctx.log_event(f"Failed to equip item: {e}")

def get_best_tool_durability(ctx, tool_type_id: str = "minecraft:wooden_axe") -> Optional[int]:
    """
    Check tool durability. Returns remaining uses of BEST tool of that type (None if no tool).
    """
    inv = ctx.get_inventory().get("inventory", [])
    best_durability = None
    best_slot = None
    
    # Generic max damage map? For now hardcoded for wooden axe as per original, 
    # but we can make it smarter later.
    max_damage = 59 if "wooden" in tool_type_id else 131 if "stone" in tool_type_id else 250
    if "iron" in tool_type_id: max_damage = 250
    if "diamond" in tool_type_id: max_damage = 1561
    
    for item in inv:
        if item.get("id") == tool_type_id:
            damage = item.get("damage", 0)
            remaining = max_damage - damage
            slot = item.get("slot", -1)
            
            if best_durability is None or remaining > best_durability:
                best_durability = remaining
                best_slot = slot
    
    # If we found a good tool not in hotbar, equip it
    if best_slot is not None and best_slot >= 9 and best_durability is not None and best_durability > 10:
        ctx.log_event(f"Best {tool_type_id} (durability {best_durability}) at slot {best_slot}, moving to hotbar...")
        equip_item_to_hotbar(ctx, best_slot)
    
    return best_durability

def robust_craft(ctx, item_id: str, count: int = 1, is_tool: bool = False, timeout: float = 30.0) -> bool:
    """
    Craft an item with retries and multiple methods (auto_craft, generic craft, manual fallback).
    """
    
    def _check_count():
        return ctx.count_item(item_id)
        
    start_time = time.time()
    
    # Attempt loop
    for attempt in range(6):
        if time.time() - start_time > timeout:
            break
            
        current_count = _check_count()
        if not is_tool and current_count >= count:
            return True
        if is_tool and ctx.has_item(item_id):
             # For tools, we might want to check durability, but basic existence is often enough for 'crafted' check
             return True

        # Method 1: auto_craft (baritone)
        try:
            ctx.log_event(f"Crafting {count}x {item_id} (Attempt {attempt+1}, using auto_craft)...")
            ctx.client.transport.dispatch("auto_craft", {"item": item_id, "quantity": count})
            time.sleep(3.0)
        except Exception as e:
            ctx.log_event(f"auto_craft attempt failed: {e}")

        if not is_tool and _check_count() >= count: return True
        if is_tool and ctx.has_item(item_id): return True

        # Method 2: craft (vanilla/mod packet)
        try:
            ctx.log_event(f"Crafting {count}x {item_id} (Attempt {attempt+1}, using craft)...")
            ctx.client.transport.dispatch("craft", {"item": item_id, "count": count})
            time.sleep(2.0)
        except Exception as e:
            ctx.log_event(f"craft attempt failed: {e}")
        
        if not is_tool and _check_count() >= count: return True
        if is_tool and ctx.has_item(item_id): return True

        # Method 3: Manual Fallbacks
        fallback_success = False
        if is_tool and item_id == "minecraft:wooden_axe":
            fallback_success = craft_wooden_axe_manual(ctx)
        elif item_id == "minecraft:chest":
             # Try generic manual craft for chest if specific one fails? 
             # We have craft_chest_manual
             fallback_success = craft_chest_manual(ctx)
        elif item_id == "minecraft:stick":
             # We can add a simple manual stick craft if needed, or rely on auto
             pass 
        elif item_id == "minecraft:stick":
             # We can add a simple manual stick craft if needed, or rely on auto
             pass 
        elif item_id == "minecraft:crafting_table":
             pass
        elif "door" in item_id:
             fallback_success = craft_door_manual(ctx, item_id)

        if fallback_success:
            return True
        
        time.sleep(1.0)
        
    return False

def craft_sticks_manual(ctx) -> bool:
    """Craft sticks using robust crafting wrapper."""
    return robust_craft(ctx, "minecraft:stick", 1)

def craft_crafting_table_manual(ctx) -> bool:
    """Craft crafting table using robust crafting wrapper."""
    return robust_craft(ctx, "minecraft:crafting_table", 1)


# --- Status and Counting Helpers ---

def safe_count_item(ctx, item_id: str) -> int:
    """Safe wrapper for counting items."""
    try:
        return ctx.count_item(item_id)
    except Exception:
        return 0

def get_inventory_counts(ctx) -> Dict[str, int]:
    """Get dictionary of all items in inventory and their counts."""
    inv = ctx.get_inventory().get("inventory", [])
    items = {}
    for slot in inv:
        if slot and slot.get("id") and slot.get("id") != "minecraft:air":
            item_id = slot.get("id")
            count = slot.get("count", 0)
            if item_id in items:
                items[item_id] += count
            else:
                items[item_id] = count
    return items

def count_all_logs(ctx) -> int:
    """Count all wood-like blocks considered as 'logs'."""
    all_ids = (
        LOG_BLOCK_IDS
        + STRIPPED_LOG_BLOCK_IDS
        + WOOD_BLOCK_IDS
        + STRIPPED_WOOD_BLOCK_IDS
    )
    return sum(max(0, safe_count_item(ctx, item_id)) for item_id in all_ids)

def count_any_planks(ctx) -> int:
    """Count all plank types."""
    return sum(max(0, safe_count_item(ctx, item_id)) for item_id in PLANK_ITEM_IDS)

def log_full_status(ctx, prefix: str = "STATUS:"):
    """Print comprehensive status: health, hunger, position, inventory."""
    try:
        # Get position
        pos = ctx.get_position()
        pos_str = f"Pos({pos[0]:.1f}, {pos[1]:.1f}, {pos[2]:.1f})" if pos else "unknown"
        
        # Get health/hunger from player state
        state = ctx.client.transport.dispatch("get_state", {})
        health = state.get("health", "?")
        hunger = state.get("food", state.get("hunger", "?"))
        world_time = state.get("world_time")
        time_str = ""
        if isinstance(world_time, (int, float)):
            day_time = int(world_time) % 24000
            if day_time < 12000:
                eta_ticks = 12000 - day_time
                eta_label = "etaDark"
            else:
                eta_ticks = 24000 - day_time
                eta_label = "etaDay"
            eta_seconds = eta_ticks / 20.0
            time_str = f" | Time={day_time} {eta_label}={eta_seconds:.0f}s"
        
        # Get full inventory
        inv_contents = get_inventory_counts(ctx)
        inv_str = ", ".join([f"{k.replace('minecraft:', '')}: {v}" for k, v in inv_contents.items()]) if inv_contents else "empty"
        
        status_line = f"{prefix} HP={health} Hunger={hunger} | {pos_str}{time_str} | Inv: {inv_str}"
        ctx.log_event(status_line)
        print(status_line)
    except Exception as e:
        print(f"{prefix} Error getting status: {e}")


