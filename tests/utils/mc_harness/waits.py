"""Polling/wait helpers for Minecraft integration tests."""

import time
from typing import Optional, Tuple

from .common import safe_dispatch


def cancel_pathing(ctx) -> None:
    try:
        safe_dispatch(ctx, "cancel", {})
    except Exception:
        return


def close_screen(ctx, timeout: float = 2.0) -> bool:
    try:
        safe_dispatch(ctx, "close_screen", {})
    except Exception:
        return False
    start = time.time()
    while time.time() - start < timeout:
        state = ctx.get_state()
        if not state.get("has_gui") or state.get("screen") == "none":
            return True
        time.sleep(0.1)
    return False


def get_block_id(ctx, x: int, y: int, z: int) -> str:
    block = safe_dispatch(ctx, "get_block", {"x": int(x), "y": int(y), "z": int(z)})
    data = block.get("data", block)
    return data.get("id", "")


def _normalize_block_id(block_id: str) -> str:
    if not block_id:
        return ""
    return block_id.split("[", 1)[0]


def wait_for_block(ctx, x: int, y: int, z: int, expected_id: str,
                   timeout: float = 2.0) -> Tuple[bool, str]:
    """Wait for block at position to match expected ID.
    
    Args:
        expected_id: Full ID like "minecraft:chest" or substring like "chest".
                     Matches if expected_id is contained in the block ID.
    """
    # Normalize: strip "minecraft:" prefix if present for flexible matching
    expected = expected_id.replace("minecraft:", "")
    start = time.time()
    last_id = ""
    while time.time() - start < timeout:
        raw_id = get_block_id(ctx, x, y, z)
        last_id = _normalize_block_id(raw_id)
        # Substring match: "chest" matches "minecraft:chest"
        if expected in last_id or last_id.endswith(expected):
            return True, last_id
        time.sleep(0.2)
    return False, last_id


def assert_block(ctx, x: int, y: int, z: int, expected_id: str) -> bool:
    ok, actual = wait_for_block(ctx, x, y, z, expected_id)
    if not ok and hasattr(ctx, "log_event"):
        ctx.log_event(f"Block mismatch at ({x},{y},{z}): {actual} != {expected_id}")
    return ok



def _dist_xz(p1: Tuple[float, float, float], p2: Tuple[float, float, float]) -> float:
    return ((p1[0] - p2[0])**2 + (p1[2] - p2[2])**2)**0.5


def _dist_xyz(p1: Tuple[float, float, float], p2: Tuple[float, float, float]) -> float:
    return ((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2 + (p1[2] - p2[2])**2)**0.5


def wait_for_pathing_stop(ctx, timeout: float = 8.0) -> bool:
    """
    Wait for pathing to stop AND position to stabilize.
    Returns True only if both conditions are met.
    """
    start = time.time()
    # First wait for is_pathing to become False
    pathing_stopped = False
    while time.time() - start < timeout:
        state = ctx.get_state()
        if not state.get("is_pathing", False):
            pathing_stopped = True
            break
        time.sleep(0.1)
    
    if not pathing_stopped:
        return False
        
    # verify we have actually stopped moving
    # Use a shorter timeout for stability check, but ensure we are stable
    return wait_for_position_stable(ctx, timeout=1.8, stable_window=0.5)


def wait_for_position_stable(ctx, timeout: float = 2.0, stable_window: float = 0.6, 
                             max_drift: float = 0.10, poll: float = 0.1) -> bool:
    """
    Wait until position remains within max_drift for stable_window seconds.
    """
    start = time.time()
    stable_start = time.time()
    last_pos = ctx.get_position()
    
    while time.time() - start < timeout:
        time.sleep(poll)
        curr_pos = ctx.get_position()
        dist = _dist_xyz(last_pos, curr_pos)
        
        if dist > max_drift:
            # Movement detected, reset stability timer
            stable_start = time.time()
            last_pos = curr_pos
        elif time.time() - stable_start >= stable_window:
            return True
            
    return False


def wait_for_arrival(ctx, target: Tuple[float, float, float], radius: float = 1.8, 
                     timeout: float = 10.0, use_xyz: bool = True, poll: float = 0.2) -> bool:
    """Wait until player is within radius of target."""
    start = time.time()
    while time.time() - start < timeout:
        pos = ctx.get_position()
        dist = _dist_xyz(pos, target) if use_xyz else _dist_xz(pos, target)
        if dist <= radius:
            return True
        time.sleep(poll)
    return False


def wait_for_position_change(ctx, start_pos: Tuple[float, float, float], min_dist: float,
                             timeout: float, mode: str = "xyz") -> bool:
    start = time.time()
    while time.time() - start < timeout:
        pos = ctx.get_position()
        if mode == "xz":
            dist = _dist_xz(pos, start_pos)
        elif mode == "y":
            dist = abs(pos[1] - start_pos[1])
        else:
            dist = _dist_xyz(pos, start_pos)
            
        if dist >= min_dist:
            return True
        time.sleep(0.2)
    return False


def wait_for_gui_open(ctx, timeout: float = 2.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        state = ctx.get_state()
        if state.get("has_gui") and state.get("screen") != "none":
            return True
        time.sleep(0.1)
    return False


def wait_for_item_count(ctx, item_id: str, min_count: int, timeout: float = 3.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        if ctx.count_item(item_id) >= min_count:
            return True
        time.sleep(0.2)
    return False


def wait_for_item_decrease(ctx, item_id: str, start_count: int, timeout: float = 3.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        if ctx.count_item(item_id) <= start_count - 1:
            return True
        time.sleep(0.2)
    return False


def wait_for_dimension(ctx, expected_dimension: str, timeout: float = 5.0) -> bool:
    """Wait for the player to be in the expected dimension."""
    start = time.time()
    expected = expected_dimension.lower()
    while time.time() - start < timeout:
        state = ctx.get_state()
        current = state.get("dimension", "").lower()
        if expected in current:
            return True
        time.sleep(0.5)
    return False


def wait_for_entity_gone(ctx, entity_id: int, timeout: float = 5.0) -> bool:
    """Wait for a specific entity ID to disappear from the entities list."""
    start = time.time()
    while time.time() - start < timeout:
        res = safe_dispatch(ctx, "get_entities", {"id": entity_id})
        ent_list = res.get("entities", res.get("data", {}).get("entities", []))
        
        if not ent_list:
            return True
        if not isinstance(ent_list, list):
            time.sleep(0.2)
            continue
        # If list not empty, ensure our specific ID is not in it
        if not any(e.get("id") == entity_id for e in ent_list):
            return True
        time.sleep(0.2)
    return False


def wait_for_entities_count(ctx, entity_type: str, min_count: int, timeout: float = 5.0) -> bool:
    """Wait until at least min_count entities of entity_type are present."""
    start = time.time()
    while time.time() - start < timeout:
        res = safe_dispatch(ctx, "get_entities", {"type": entity_type})
        ent_list = res.get("entities", res.get("data", {}).get("entities", []))
        if isinstance(ent_list, list) and len(ent_list) >= min_count:
            return True
        time.sleep(0.2)
    return False
