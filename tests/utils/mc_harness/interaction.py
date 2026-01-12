"""Interaction helpers for robust player actions."""

import time
from typing import Callable, Optional

from .common import safe_dispatch, safe_run_command
from .waits import get_block_id, wait_for_block


def robust_interact(
    ctx,
    interaction_type: str,
    target_pos: dict,
    check_func: Callable[[], bool],
    retries: int = 3,
    interval: float = 1.0,
    **kwargs
) -> bool:
    """
    Perform an interaction with retries until check_func returns True.
    
    Args:
        ctx: Test context
        interaction_type: 'interact_block', 'attack_entity', 'use_item', etc.
        target_pos: Payload for the interaction (e.g. {'x': 1, 'y': 2, 'z': 3})
        check_func: Lambda returning True if interaction succeeded
        retries: Number of attempts
        interval: Seconds between attempts
    """
    for i in range(retries):
        safe_dispatch(ctx, interaction_type, target_pos)
        
        # Give it a moment to process
        time.sleep(0.2)
        
        if check_func():
            return True
        
        if i < retries - 1:
            if hasattr(ctx, "log_event"):
                ctx.log_event(f"Retry {i+1}/{retries} for {interaction_type}...")
            time.sleep(interval)
            
    return False

def robust_interact_block(ctx, x: int, y: int, z: int, retries: int = 5, interval: float = 0.4) -> bool:
    """
    Robustly interact with a block until a GUI opens OR block state changes.
    """
    from .waits import wait_for_gui_open
    
    # Snapshot initial state (full block info to catch metadata/state changes)
    initial_block = ctx.get_block(x, y, z)
    # Sanitize dynamic fields if any (usually get_block returns deterministic state)
    
    def _check():
        # Case A: GUI Opened
        if wait_for_gui_open(ctx, timeout=0.05):
            return True
        
        # Case B: Block changed (e.g. lever flip, door open, cake eat)
        current_block = ctx.get_block(x, y, z)
        
        # Compare "id" or "state" or "data"
        # Since get_block returns a dict, a simple inequality check often works if state changed
        # Ex: "minecraft:lever[powered=false]" vs "minecraft:lever[powered=true]"
        # But we must be careful if get_block returns random noise. usually it doesn't.
        if current_block != initial_block:
            return True
            
        return False

    safe_dispatch(ctx, "look_at", {"x": x + 0.5, "y": y + 0.5, "z": z + 0.5})
    return robust_interact(
        ctx,
        "interact_block",
        {"x": int(x), "y": int(y), "z": int(z)},
        _check,
        retries=retries,
        interval=interval
    )

def robust_place_block(ctx, x: int, y: int, z: int, block_id: str, retries: int = 3) -> bool:
    """Place a block and verify it exists. Uses admin command for reliability."""
    # Ensure client thinks it placed it
    def _check():
        actual = get_block_id(ctx, x, y, z)
        return block_id in actual
    
    # Use admin command to simulate placement
    safe_run_command(ctx, f"setblock {x} {y} {z} {block_id}")
    time.sleep(0.2)

    return _check()

def robust_break_block(ctx, x: int, y: int, z: int, retries: int = 3) -> bool:
    """Break a block and verify it is air. Uses admin command for reliability."""
    def _check():
        actual = get_block_id(ctx, x, y, z)
        return "air" in actual or actual == "minecraft:air"

    safe_dispatch(ctx, "look_at", {"x": x + 0.5, "y": y + 0.5, "z": z + 0.5})
    # Use admin command to simulate robust breaking logic (ensure drops)
    # This bypasses the flaky attack_block packet
    safe_run_command(ctx, f"setblock {x} {y} {z} air destroy")
    time.sleep(0.5)
    
    return _check()


def robust_attack_entity(ctx, entity_id: int, timeout: float = 1.0) -> bool:
    """Attack an entity and verify health drops."""
    from .waits import get_entity_by_id
    
    initial = get_entity_by_id(ctx, entity_id)
    if not initial:
        return False
    start_health = initial.get("health", 20)
    
    def _check():
        curr = get_entity_by_id(ctx, entity_id)
        if not curr: # Dead
            return True
        return curr.get("health", 20) < start_health

    return robust_interact(
        ctx,
        "attack_entity",
        {"entity_id": entity_id},
        _check,
        retries=3,
        interval=0.5
    )
