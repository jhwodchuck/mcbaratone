"""Bounded recovery policies for verified underground descent stalls."""

from __future__ import annotations

from typing import Any, Callable, Collection

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
    maximum_altitude: int = 8,
    maximum_steps: int = 12,
) -> bool:
    """Use the guarded column descent only for a low-altitude deadlock."""
    if target_y >= current_y or current_y > maximum_altitude:
        return False
    fallback_target = max(target_y, current_y - maximum_steps)
    if fallback_target >= current_y:
        return False
    print(
        f"Y navigation: low-altitude stall at Y={current_y}, "
        f"trying guarded column recovery to Y={fallback_target}"
    )
    from .stone_descent import manual_column_descend

    return manual_column_descend(
        client,
        target_y=fallback_target,
        max_steps=current_y - fallback_target,
    )
