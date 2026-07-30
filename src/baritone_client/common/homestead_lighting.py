"""
Helpers for deterministic BOOT perimeter lighting.

The first lighting ring is intentionally small and stable so BOOT can persist a
bounded coordinate set and repair missing torches one-by-one across retries.
"""

from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple


Coordinate = Tuple[int, int, int]


def perimeter_ring(anchor: Sequence[int], radius: int = 4) -> List[Coordinate]:
    """Return a deterministic square ring around ``anchor`` excluding duplicates."""
    if len(anchor) != 3:
        return []
    ax, ay, az = (int(value) for value in anchor)
    max_radius = max(1, int(radius))
    positions: List[Coordinate] = []
    y = int(ay) + 1

    for x in range(ax - max_radius, ax + max_radius + 1):
        positions.append((x, y, az - max_radius))
        positions.append((x, y, az + max_radius))
    for z in range(az - max_radius + 1, az + max_radius):
        positions.append((ax - max_radius, y, z))
        positions.append((ax + max_radius, y, z))

    unique = []
    seen = set()
    for pos in positions:
        if pos not in seen:
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
