"""Shared progress detection and bounded recovery waypoints.

Baritone can report ``is_pathing=True`` even when a route never moves the
player.  Recovery callers must therefore judge commands by observed position
changes, not by the process flag alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import hypot
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple
import time


BlockPosition = Tuple[int, int, int]


def block_position(state: Dict[str, Any]) -> BlockPosition:
    """Return an integer player position from either supported state shape."""
    position = state.get("block_position", state.get("position", {}))
    return (
        int(position.get("x", state.get("x", 0))),
        int(position.get("y", state.get("y", 64))),
        int(position.get("z", state.get("z", 0))),
    )


@dataclass
class MovementWatchdog:
    """Track real displacement across successive bridge state samples."""

    minimum_horizontal_change: float = 0.75
    minimum_vertical_change: int = 1
    last_position: Optional[BlockPosition] = None
    stationary_checks: int = 0

    def observe(self, state: Dict[str, Any]) -> bool:
        """Record a sample and return True only when the player really moved."""
        current = block_position(state)
        previous = self.last_position
        self.last_position = current
        if previous is None:
            self.stationary_checks = 0
            return False
        horizontal = hypot(current[0] - previous[0], current[2] - previous[2])
        vertical = abs(current[1] - previous[1])
        moved = (
            horizontal >= self.minimum_horizontal_change
            or vertical >= self.minimum_vertical_change
        )
        self.stationary_checks = 0 if moved else self.stationary_checks + 1
        return moved

    def stalled(self, checks: int) -> bool:
        """Return whether the configured number of stationary samples elapsed."""
        return self.stationary_checks >= max(1, int(checks))

    def reset(self, state: Optional[Dict[str, Any]] = None) -> None:
        """Start a fresh command-observation window."""
        self.last_position = block_position(state) if state else None
        self.stationary_checks = 0


@dataclass
class ExplorationWaypoints:
    """Rotate deterministic non-zero exploration targets inside a safety radius."""

    origin_x: float
    origin_z: float
    maximum_radius: float
    step: float = 24.0
    _index: int = field(default=0, init=False)

    _DIRECTIONS: Sequence[Tuple[int, int]] = (
        (1, 0),
        (0, 1),
        (-1, 0),
        (0, -1),
        (1, 1),
        (-1, 1),
        (-1, -1),
        (1, -1),
    )

    def next(self) -> Tuple[int, int]:
        """Return the next target, never the origin itself."""
        radius = min(max(8.0, self.step), max(8.0, self.maximum_radius))
        direction = self._DIRECTIONS[self._index % len(self._DIRECTIONS)]
        ring = 1 + self._index // len(self._DIRECTIONS)
        distance = min(radius * ring, max(8.0, self.maximum_radius))
        self._index += 1
        return (
            round(self.origin_x + direction[0] * distance),
            round(self.origin_z + direction[1] * distance),
        )


def nearest_lower_surface_candidates(
    response: Dict[str, Any],
    origin: BlockPosition,
    *,
    minimum_drop: int = 2,
    maximum_drop: int = 24,
    maximum_distance: float = 40.0,
    maximum_extra_drop: float = 6.0,
    limit: int = 12,
) -> Iterable[BlockPosition]:
    """Select nearby lower surface blocks for high-altitude egress attempts."""
    ox, oy, oz = origin
    candidates = []
    seen = set()
    for item in response.get("found", []):
        x, y, z = int(item["x"]), int(item["y"]), int(item["z"])
        drop = oy - (y + 1)
        distance = hypot(x - ox, z - oz)
        position = (x, y + 1, z)
        if (
            minimum_drop <= drop <= maximum_drop
            and 2.0 <= distance <= maximum_distance
            and drop <= distance + maximum_extra_drop
            and position not in seen
        ):
            seen.add(position)
            # Prefer a natural staircase profile: roughly one block down per
            # horizontal step, with at most a bounded final fall.
            score = distance + abs(drop - distance) * 0.5
            candidates.append((score, position))
    candidates.sort(key=lambda item: item[0])
    return [position for _, position in candidates[: max(1, int(limit))]]


def recover_stalled_gathering(
    client: Any,
    waypoints: ExplorationWaypoints,
    state: Dict[str, Any],
    *,
    blocks: Sequence[str],
    quantity: int,
    major_stalls: int,
    exploring: bool,
) -> tuple[int, bool]:
    """Retry once, then rotate exploration after false-pathing gathering."""
    major_stalls += 1
    client.transport.dispatch("cancel", {})
    time.sleep(0.5)
    if major_stalls == 1 and not exploring:
        print(
            "DEBUG: Gathering has false pathing with no movement; "
            "retrying the local mine command once"
        )
        client.transport.dispatch(
            "mine", {"blocks": list(blocks), "quantity": quantity}
        )
        return major_stalls, False
    target_x, target_z = waypoints.next()
    print(
        "DEBUG: Gathering remains stationary; rotating exploration "
        f"toward ({target_x}, {target_z})"
    )
    client.transport.dispatch(
        "explore", {"x": target_x, "z": target_z}
    )
    return major_stalls, True
