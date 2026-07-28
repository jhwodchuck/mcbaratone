"""Bounded staging recovery for starter-house door placement."""

from __future__ import annotations

from typing import Any


def move_to_door_staging(
    client: Any,
    door_x: int,
    door_y: int,
    door_z: int,
    *,
    timeout: float = 20.0,
) -> bool:
    """Reach either side of a north-wall doorway without replaying one goal."""
    from . import harness_ops

    candidates = (
        (door_x, door_y, door_z - 2),
        (door_x - 2, door_y, door_z - 2),
        (door_x + 2, door_y, door_z - 2),
        (door_x, door_y, door_z + 2),
    )
    for staging in candidates:
        if harness_ops.move_near(client, *staging, timeout=timeout):
            return True
        client.transport.dispatch("cancel", {})
    return False
