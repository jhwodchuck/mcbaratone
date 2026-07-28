"""Shared water-state checks and bounded drowning prevention."""

from __future__ import annotations

import time
from typing import Any, Callable


_WATER_FEET_BLOCKS = ("water", "seagrass", "kelp")


def head_block_is_water(client: Any, state: dict) -> bool:
    """Return whether the block at player head height is water."""
    position = state.get("block_position", state.get("position", {}))
    if not all(axis in position for axis in ("x", "y", "z")):
        return False
    try:
        head = client.transport.dispatch(
            "get_block",
            {
                "x": int(position["x"]),
                "y": int(position["y"]) + 1,
                "z": int(position["z"]),
            },
        ).get("id", "")
    except Exception:
        return False
    return "water" in str(head)


def player_is_in_water(client: Any, state: dict) -> bool:
    """Return whether the player's feet are in water or aquatic plants."""
    position = state.get("block_position", state.get("position", {})) or {}
    if not all(axis in position for axis in ("x", "y", "z")):
        return False
    try:
        block = str(
            client.transport.dispatch(
                "get_block",
                {
                    "x": int(position["x"]),
                    "y": int(position["y"]),
                    "z": int(position["z"]),
                },
            ).get("id", "")
        )
    except Exception:
        return False
    return any(token in block for token in _WATER_FEET_BLOCKS)


def submerged_too_long(
    client: Any,
    state: dict,
    *,
    max_seconds: float,
    head_is_water: Callable[[Any, dict], bool],
    clock: Callable[[], float] = time.time,
) -> bool:
    """Track continuous head submersion and enforce a wall-clock limit."""
    if not head_is_water(client, state):
        client._submerged_since = None
        return False
    since = getattr(client, "_submerged_since", None)
    now = clock()
    if since is None:
        client._submerged_since = now
        return False
    return (now - since) >= float(max_seconds)


def escape_water_if_submerged(
    client: Any,
    state: dict,
    *,
    tick_limit: int,
    head_is_water: Callable[[Any, dict], bool],
    surface: Callable[..., bool],
) -> bool:
    """Surface after consecutive underwater supervision ticks."""
    if not head_is_water(client, state):
        client._submersion_ticks = 0
        return False
    ticks = int(getattr(client, "_submersion_ticks", 0)) + 1
    client._submersion_ticks = ticks
    if ticks < tick_limit:
        return False
    print(
        f"SURVIVAL: head underwater for {ticks} supervision ticks; "
        "surfacing to avoid drowning"
    )
    if surface(client, timeout=12.0):
        client._submersion_ticks = 0
    return True
