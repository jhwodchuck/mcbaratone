"""Bounded recovery policies for verified underground descent stalls."""

from __future__ import annotations

import time
from typing import Any, Callable, Collection, Optional

from .movement_recovery import block_position


def cleared_step_reached(
    position: dict[str, Any],
    *,
    target_x: int,
    target_y: int,
    target_z: int,
    require_horizontal: bool = False,
) -> bool:
    """Verify the position postcondition for a cleared staircase step."""
    moved_x = int(position.get("x", target_x))
    moved_y = int(position.get("y", target_y + 1))
    moved_z = int(position.get("z", target_z))
    if moved_y > target_y:
        return False
    if require_horizontal:
        if not all(axis in position for axis in ("x", "z")):
            return False
        if (moved_x, moved_z) != (target_x, target_z):
            return False
    return True


def walk_to_cleared_step(
    client: Any,
    target_x: int,
    target_y: int,
    target_z: int,
    current_y: int,
    state: dict[str, Any],
    *,
    label: str,
    require_horizontal: bool,
    dispatch: Callable[..., dict[str, Any]],
    read_state: Callable[..., Any],
    read_flags: dict[str, bool],
    mine_setup_seconds: float,
    cancel_grace_seconds: float,
) -> str:
    """Enter one verified step and classify movement, stall, or danger."""
    response = dispatch(
        client,
        "goto",
        {"x": target_x, "y": target_y, "z": target_z},
        post_delay_seconds=mine_setup_seconds,
    )
    if response.get("error"):
        print(f"Y navigation: {label} goto rejected: {response['error']}")
        return "stalled"

    move_deadline = time.monotonic() + 30.0
    starting_health = float(state.get("health", 20) or 0)
    last_position = (target_x, current_y, target_z)
    while time.monotonic() < move_deadline:
        moved, _ = read_state(client, retries=2, label="Y navigation cleared-step wait")
        if moved is None:
            read_flags["unreadable"] = True
            time.sleep(0.25)
            continue
        moved_pos = moved.get("block_position", moved.get("position", {}))
        moved_x = int(moved_pos.get("x", target_x))
        moved_y = int(moved_pos.get("y", current_y))
        moved_z = int(moved_pos.get("z", target_z))
        last_position = (moved_x, moved_y, moved_z)
        moved_health = float(moved.get("health", starting_health) or 0)
        if (
            moved.get("is_dead", False)
            or moved_health <= 0
            or moved_health < starting_health - 4
            or moved_y < target_y - 2
        ):
            dispatch(client, "cancel", {}, post_delay_seconds=cancel_grace_seconds)
            print(f"Y navigation safety abort during {label}")
            return "unsafe"
        if cleared_step_reached(
            moved_pos,
            target_x=target_x,
            target_y=target_y,
            target_z=target_z,
            require_horizontal=require_horizontal,
        ):
            dispatch(client, "cancel", {}, post_delay_seconds=cancel_grace_seconds)
            return "moved"
        time.sleep(0.25)

    dispatch(client, "cancel", {}, post_delay_seconds=cancel_grace_seconds)
    print(
        f"Y navigation: {label} goto made no downward progress; "
        f"target=({target_x}, {target_y}, {target_z}) last_position={last_position}"
    )
    return "stalled"


def attempt_water_pocket_sidestep(
    *,
    state: dict[str, Any],
    directions: Collection[tuple[int, int]],
    air_blocks: Collection[str],
    unsafe_blocks: Collection[str],
    read_block: Callable[[int, int, int], str],
    break_for_step: Callable[[int, int, int], bool],
    walk_to_step: Callable[..., str],
) -> str:
    """Carve and verify one safe same-level step out of trapped water."""
    px, py, pz = block_position(state)
    for dx, dz in directions:
        target_x, target_z = px + dx, pz + dz
        floor_id = read_block(target_x, py - 1, target_z)
        foot_id = read_block(target_x, py, target_z)
        head_id = read_block(target_x, py + 1, target_z)
        if (
            floor_id in air_blocks
            or floor_id in unsafe_blocks
            or foot_id in unsafe_blocks
            or head_id in unsafe_blocks
        ):
            continue

        # Match the ordinary staircase safety order: clear head space before
        # feet so falling terrain cannot force an early movement.
        if head_id not in air_blocks and not break_for_step(target_x, py + 1, target_z):
            continue
        if foot_id not in air_blocks and not break_for_step(target_x, py, target_z):
            continue

        floor_after = read_block(target_x, py - 1, target_z)
        foot_after = read_block(target_x, py, target_z)
        head_after = read_block(target_x, py + 1, target_z)
        if (
            floor_after in air_blocks
            or floor_after in unsafe_blocks
            or foot_after in unsafe_blocks
            or head_after in unsafe_blocks
        ):
            continue

        move_result = walk_to_step(
            target_x,
            py,
            target_z,
            py,
            state,
            label="water pocket sidestep",
            require_horizontal=True,
        )
        if move_result in {"moved", "unsafe"}:
            return move_result

    print("Y navigation: no verified safe sidestep out of source-water pocket")
    return "blocked"


def recover_low_altitude_column(
    client: Any,
    *,
    current_y: int,
    target_y: int,
    maximum_altitude: Optional[int] = 8,
    maximum_steps: int = 12,
    reason: str = "low-altitude stall",
) -> bool:
    """Guarded column descent for a detected navigation stall.

    ``maximum_altitude=None`` lifts the low-altitude-only gate for callers
    recovering a stall detected at any elevation (e.g. a general repeated-
    position deadlock, not specifically a low-altitude one) -- same
    fallback-target math and same recovery call either way, just without
    the deadlock being altitude-qualified first.
    """
    if target_y >= current_y:
        return False
    if maximum_altitude is not None and current_y > maximum_altitude:
        return False
    fallback_target = max(target_y, current_y - maximum_steps)
    if fallback_target >= current_y:
        return False
    print(
        f"Y navigation: {reason} at Y={current_y}, "
        f"trying guarded column recovery to Y={fallback_target}"
    )
    from .stone_descent import manual_column_descend

    return manual_column_descend(
        client,
        target_y=fallback_target,
        max_steps=current_y - fallback_target,
    )
