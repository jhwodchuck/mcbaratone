"""
Supply Verification Module

Provides verification utilities for supply chest operations to prevent
race conditions between chest creation and filling operations.
"""

import time
from typing import Dict, List, Tuple, Optional

from tests.functional.shared.block_ops import block_id_at


def verify_supply_chests(
    ctx,
    positions: List[Tuple[int, int, int]],
    timeout: float = 5.0
) -> bool:
    """
    Verify all supply chests exist and are accessible.
    
    Polls until all chests at the given positions are confirmed to exist,
    or until timeout is reached.
    
    Args:
        ctx: Test context
        positions: List of (x, y, z) positions where chests should exist
        timeout: Maximum time to wait for verification
        
    Returns:
        True if all chests verified, False if timeout reached
        
    Example:
        >>> positions = [(100, 64, 100), (102, 64, 100)]
        >>> if not verify_supply_chests(ctx, positions):
        ...     ctx.log_event("Supply chests not ready")
    """
    start = time.time()
    poll_interval = 0.3
    
    while time.time() - start < timeout:
        missing = []
        for pos in positions:
            block = block_id_at(ctx, pos[0], pos[1], pos[2])
            if "chest" not in block:
                missing.append(pos)
        
        if not missing:
            ctx.log_event(f"Verified {len(positions)} supply chests exist")
            return True
        
        time.sleep(poll_interval)
    
    ctx.log_event(f"Supply chest verification timed out. Missing: {missing}")
    return False


def wait_for_chest_contents(
    ctx,
    pos: Tuple[int, int, int],
    expected_items: Dict[str, int],
    timeout: float = 5.0
) -> bool:
    """
    Wait until chest at position contains expected items.
    
    Opens chest and polls contents until items are present or timeout.
    
    Args:
        ctx: Test context
        pos: (x, y, z) position of chest
        expected_items: Dict of item_id -> minimum count expected
        timeout: Maximum time to wait
        
    Returns:
        True if chest contains expected items, False on timeout
        
    Example:
        >>> expected = {"minecraft:oak_log": 64, "minecraft:cobblestone": 128}
        >>> if not wait_for_chest_contents(ctx, (100, 64, 100), expected):
        ...     ctx.log_event("Chest fill incomplete")
    """
    from tests.functional.shared.inventory_ops import (
        do_open_container,
        do_close_container,
        get_inv_slots,
    )
    
    start = time.time()
    poll_interval = 0.5
    
    while time.time() - start < timeout:
        if not do_open_container(ctx, pos, timeout=2.0):
            ctx.log_event(f"Could not open chest at {pos}")
            time.sleep(poll_interval)
            continue
        
        try:
            screen = ctx.client.transport.dispatch("get_screen", {})
            data = screen.get("data", screen)
            slots = get_inv_slots(data)
            
            # Count items in chest slots (exclude player inventory)
            # Container slots come first, player inventory follows
            chest_items: Dict[str, int] = {}
            for slot in slots:
                slot_idx = slot.get("slot", 0)
                # Single chest has 27 slots, double has 54
                # We check the first portion for container items
                if slot_idx >= 54:  # Skip player inventory slots
                    continue
                item_id = slot.get("id")
                if not item_id or item_id == "minecraft:air":
                    continue
                chest_items[item_id] = chest_items.get(item_id, 0) + slot.get("count", 0)
            
            # Check if all expected items are present
            missing = {}
            for item_id, count in expected_items.items():
                have = chest_items.get(item_id, 0)
                if have < count:
                    missing[item_id] = count - have
            
            if not missing:
                do_close_container(ctx)
                ctx.log_event(f"Chest at {pos} verified with expected contents")
                return True
                
        finally:
            do_close_container(ctx)
        
        time.sleep(poll_interval)
    
    ctx.log_event(f"Chest content verification timed out. Missing: {missing}")
    return False


def verify_supply_setup_complete(
    ctx,
    positions: List[Tuple[int, int, int]],
    sample_items: Optional[Dict[str, int]] = None,
    timeout: float = 8.0
) -> bool:
    """
    Verify complete supply chain setup: chests exist and optionally contain items.
    
    Combines chest existence verification with optional content check on first chest.
    
    Args:
        ctx: Test context
        positions: List of supply chest positions
        sample_items: Optional items to verify in first chest
        timeout: Maximum total wait time
        
    Returns:
        True if setup is verified complete
    """
    # First verify all chests exist
    if not verify_supply_chests(ctx, positions, timeout=timeout / 2):
        return False
    
    # Optionally verify contents of first chest
    if sample_items and positions:
        return wait_for_chest_contents(ctx, positions[0], sample_items, timeout=timeout / 2)
    
    return True


__all__ = [
    "verify_supply_chests",
    "wait_for_chest_contents", 
    "verify_supply_setup_complete",
]
