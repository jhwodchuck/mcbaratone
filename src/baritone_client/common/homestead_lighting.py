"""
Helpers for deterministic BOOT perimeter lighting.

The first lighting ring is intentionally small and stable so BOOT can persist a
bounded coordinate set and repair missing torches one-by-one across retries.
"""

from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple


Coordinate = Tuple[int, int, int]


def perimeter_ring(
    anchor: Sequence[int],
    radius: int = 4,
    spacing: int = 4,
) -> List[Coordinate]:
    """Return a deterministic, *spaced* square ring around ``anchor``.

    ``spacing`` is the gap in blocks between adjacent torches along the ring.

    This used to emit every block of the perimeter -- 32 contiguous positions
    for the default radius, all mutually adjacent. That is a solid wall of
    torches, and it is pure waste: a torch emits light level 14 falling by 1
    per block, and hostile mobs need light level 0 to spawn, so a single torch
    comfortably covers a 9x9 footprint. The bot was being asked to craft and
    place ~24 torches where a handful suffices.

    It was not merely cosmetic. Each torch costs a stick plus a coal/charcoal,
    and on 2026-07-31 the fleet's furthest-along bot (Bot16, 8/9 homestead
    steps) stalled at 16/24 placed with 0 torches, 0 sticks and 2 charcoal
    left, unable to finish the ring it had been told to build. Spacing the
    ring cuts the requirement by roughly two thirds while still lighting the
    footprint, and always keeps the four corners so coverage stays even.
    """
    if len(anchor) != 3:
        return []
    ax, ay, az = (int(value) for value in anchor)
    max_radius = max(1, int(radius))
    step = max(1, int(spacing))
    y = int(ay) + 1

    corners = {
        (ax - max_radius, y, az - max_radius),
        (ax + max_radius, y, az - max_radius),
        (ax - max_radius, y, az + max_radius),
        (ax + max_radius, y, az + max_radius),
    }

    # Walk the ring in a stable order so the sampled subset is deterministic.
    walk: List[Coordinate] = []
    for x in range(ax - max_radius, ax + max_radius + 1):
        walk.append((x, y, az - max_radius))
    for z in range(az - max_radius + 1, az + max_radius + 1):
        walk.append((ax + max_radius, y, z))
    for x in range(ax + max_radius - 1, ax - max_radius - 1, -1):
        walk.append((x, y, az + max_radius))
    for z in range(az + max_radius - 1, az - max_radius, -1):
        walk.append((ax - max_radius, y, z))

    unique: List[Coordinate] = []
    seen = set()
    for index, pos in enumerate(walk):
        if pos in seen:
            continue
        if index % step and pos not in corners:
            continue
        seen.add(pos)
        unique.append(pos)
    return unique


def normalize_coordinates(positions: Iterable[Sequence[int]]) -> List[Coordinate]:
    """Normalize a sequence of coords to int tuples and preserve stable order."""
    normalized: List[Coordinate] = []
    seen = set()
    for pos in positions:
        if len(pos) != 3:
            continue
        candidate = (int(pos[0]), int(pos[1]), int(pos[2]))
        if candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


def partition_light_coordinates(
    intended: Iterable[Sequence[int]],
    observed_torches: Iterable[Sequence[int]],
) -> Tuple[List[Coordinate], List[Coordinate]]:
    """Split intended coordinates into missing and verified lists."""
    intended_positions = normalize_coordinates(intended)
    observed = {
        tuple(int(part) for part in position)
        for position in observed_torches
        if len(position) == 3
    }
    missing = []
    verified_positions = []
    for pos in intended_positions:
        if tuple(pos) in observed:
            verified_positions.append(pos)
        else:
            missing.append(pos)
    return missing, verified_positions
