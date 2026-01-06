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
    expected = _normalize_block_id(expected_id)
    start = time.time()
    last_id = ""
    while time.time() - start < timeout:
        last_id = _normalize_block_id(get_block_id(ctx, x, y, z))
        if last_id == expected:
            return True, last_id
        time.sleep(0.2)
    return False, last_id


def assert_block(ctx, x: int, y: int, z: int, expected_id: str) -> bool:
    ok, actual = wait_for_block(ctx, x, y, z, expected_id)
    if not ok and hasattr(ctx, "log_event"):
        ctx.log_event(f"Block mismatch at ({x},{y},{z}): {actual} != {expected_id}")
    return ok


def wait_for_pathing_stop(ctx, timeout: float = 8.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        state = ctx.get_state()
        if not state.get("is_pathing", False):
            return True
        time.sleep(0.2)
    return False


def wait_for_position_change(ctx, start_pos: Tuple[float, float, float], min_dist: float,
                             timeout: float) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        pos = ctx.get_position()
        dx = pos[0] - start_pos[0]
        dz = pos[2] - start_pos[2]
        dist = (dx * dx + dz * dz) ** 0.5
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
