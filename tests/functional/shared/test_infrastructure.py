"""
Shared test infrastructure functions for extended suite tests.

This module contains common test setup and utility functions used across
multiple extended_suite_*.py files to reduce duplication.
"""

import time
from typing import Dict, List, Optional, Tuple, Union

from tests.utils.mc_harness.actions import do_goto
from utils.mc_harness import (
    prepare_test_world, teardown_test_world, clear_box, build_floor, tp,
    assert_block, wait_for_pathing_stop, wait_for_position_change,
    get_block_id, wait_for_tick_stabilization, robust_place_block,
    robust_interact, wait_for_block
)


def wait_for_y_change(ctx, start_y: float, min_delta: float, timeout: float = 5.0) -> bool:
    """Wait for Y to change by at least min_delta."""
    start_t = time.time()
    while time.time() - start_t < timeout:
        pos = ctx.get_position()
        current_y = pos[1]
        if min_delta > 0:
            if current_y >= start_y + min_delta: return True
        else:
            if current_y <= start_y + min_delta: return True
        time.sleep(0.1)
    return False


def safe_look(ctx, yaw: Optional[float] = None, pitch: Optional[float] = None,
               target: Optional[Tuple[float, float, float]] = None) -> bool:
    """Try yaw/pitch look, fallback to look_at target."""
    if yaw is not None or pitch is not None:
        try:
            ctx.client.transport.dispatch("look", {"yaw": yaw or 0, "pitch": pitch or 0})
            return True
        except Exception:
            pass
    if target is None:
        x, y, z = ctx.get_position()
        target = (x + 1, y, z)
    try:
        ctx.client.transport.dispatch("look_at", {"x": target[0], "y": target[1], "z": target[2]})
        return True
    except Exception as exc:
        ctx.log_event(f"Look failed: {exc}")
        return False


def goto_vertical(ctx, target: Dict[str, Union[int, float]], min_delta: float, timeout: float = 8.0) -> bool:
    """Move vertically; cancel pathing after y-change or timeout."""
    start_pos = ctx.get_position()
    ctx.client.transport.dispatch("goto", target)
    moved = wait_for_position_change(ctx, start_pos, min_dist=min_delta, timeout=timeout, mode="y")
    ctx.client.transport.dispatch("cancel", {})
    return moved


def prepare_standard_move_test(ctx, test_id: str, anchor: Tuple[int, int, int],
                              size: int = 30, height: int = 20, floor: bool = True,
                              floor_mat: str = "minecraft:stone") -> Dict[str, int]:
    """
    Standard fixture for movement tests.
    Clears area, prepares world, tps player, and optionally builds floor.
    Returns the bounds dict for teardown.
    """
    ax, ay, az = anchor
    bounds = {
        "min_x": ax - size, "min_y": ay - 10, "min_z": az - size,
        "max_x": ax + size, "max_y": ay + height, "max_z": az + size,
    }

    # Store bounds in test state if needed
    if hasattr(ctx, '_test_state'):
        ctx._test_state[test_id] = ctx._test_state.get(test_id, {})
        ctx._test_state[test_id]["bounds"] = bounds

    # 1. Clean slate
    clear_box(ctx, bounds)
    prepare_test_world(ctx)

    # 2. Position
    tp(ctx, ax, ay, az)

    # Ensure chunk is loaded at anchor before building
    for _ in range(20):
        block = get_block_id(ctx, ax, ay - 1, az)
        if block and "void_air" not in block:
            break
        time.sleep(0.5)

    if floor:
        # Build a larger floor for reliable pathing
        floor_half = max(20, size)
        build_floor(ctx, ax - floor_half, ay - 1, az - floor_half, ax + floor_half, az + floor_half, mat=floor_mat)

    wait_for_tick_stabilization(ctx, 40)

    if floor:
        # Verify terrain (fail fast)
        check_points = [(ax, ay - 1, az), (ax + 5, ay - 1, az + 5)]
        for cx, cy, cz in check_points:
            ok, actual = wait_for_block(ctx, cx, cy, cz, floor_mat, timeout=3.0)
            if not ok:
                ctx.log_event(f"terrain mismatch at {cx},{cy},{cz}: expected {floor_mat}, got {actual}")
                raise RuntimeError(f"Terrain verification failed: {actual} != {floor_mat}")

    # Re-teleport after build to ensure we start on the prepared floor
    tp(ctx, ax, ay, az)
    wait_for_tick_stabilization(ctx, 10)
    return bounds


def verify_pad_or_structure(ctx, checks: List[Tuple[int, int, int, str]]) -> bool:
    """Verify block placement."""
    return all([assert_block(ctx, *c) for c in checks])


def prepare_standard_block_test(ctx, test_id: str, anchor: Tuple[int, int, int],
                              size: int = 12, height: int = 10, gamemode: str = "survival", state_dict: Optional[Dict] = None) -> Dict[str, int]:
    """
    Standard fixture for block tests.
    Clears area, prepares world, tps player, and waits for chunk loading.
    Returns the bounds dict for teardown.
    """
    ax, ay, az = anchor
    bounds = {
        "min_x": ax - size, "min_y": ay - 5, "min_z": az - size,
        "max_x": ax + size, "max_y": ay + height, "max_z": az + size,
    }

    # Store bounds in provided state dict or ctx._test_state if available
    if state_dict is not None:
        state_dict[test_id] = state_dict.get(test_id, {})
        state_dict[test_id]["bounds"] = bounds
    elif hasattr(ctx, '_test_state'):
        ctx._test_state[test_id] = ctx._test_state.get(test_id, {})
        ctx._test_state[test_id]["bounds"] = bounds

    # 1. Clean slate
    clear_box(ctx, bounds)
    prepare_test_world(ctx, gamemode=gamemode)

    # 2. Position
    tp(ctx, ax, ay, az)

    # Ensure chunk is loaded at anchor before building
    for _ in range(20):
        block = get_block_id(ctx, ax, ay - 1, az)
        if block and "void_air" not in block:
            break
        time.sleep(0.5)

    wait_for_tick_stabilization(ctx, 40)
    return bounds


def prepare_standard_inventory_test(ctx, test_id: str, anchor: Tuple[int, int, int],
                              size: int = 15, height: int = 10, gamemode: str = "survival", floor: bool = True, state_dict: Optional[Dict] = None) -> Dict[str, int]:
    """
    Standard fixture for inventory tests.
    Clears area, prepares world, tps player, optionally builds floor, clears inventory.
    Returns the bounds dict for teardown.
    """
    ax, ay, az = anchor
    bounds = {
        "min_x": ax - size, "min_y": ay - 5, "min_z": az - size,
        "max_x": ax + size, "max_y": ay + height, "max_z": az + size,
    }

    # Store bounds in provided state dict or ctx._test_state if available
    if state_dict is not None:
        state_dict[test_id] = state_dict.get(test_id, {})
        state_dict[test_id]["bounds"] = bounds
    elif hasattr(ctx, '_test_state'):
        ctx._test_state[test_id] = ctx._test_state.get(test_id, {})
        ctx._test_state[test_id]["bounds"] = bounds

    # 1. Clean slate
    clear_box(ctx, bounds)
    prepare_test_world(ctx, gamemode=gamemode)

    # 2. Position
    tp(ctx, ax, ay, az)

    # 3. Optional floor
    if floor:
        build_floor(ctx, ax - 6, ay - 1, az - 6, ax + 6, az + 6)

    # 4. Clear inventory and snapshot
    ctx.clear_inventory()
    ctx.snapshot("start")

    # Ensure chunk is loaded
    for _ in range(20):
        block = get_block_id(ctx, ax, ay - 1, az)
        if block and "void_air" not in block:
            break
        time.sleep(0.5)

    wait_for_tick_stabilization(ctx, 40)
    return bounds


def run_and_wait_goto(ctx, target: Dict[str, Union[int, float]],
                      start_pos: Optional[Tuple[float, float, float]] = None,
                      min_dist: float = 1.0, goto_timeout: float = 15) -> bool:
    """Run goto with proper timeout control."""
    # Calculate arrival_radius from min_dist (allow some tolerance)
    arrival_radius = min_dist * 0.8 if min_dist > 1.5 else 1.5
    return do_goto(
        ctx,
        target,
        timeout=goto_timeout,
        arrival_radius=arrival_radius,
        start_move_timeout=3.0,
        start_move_min_dist=0.5,
        require_arrival=True
    )