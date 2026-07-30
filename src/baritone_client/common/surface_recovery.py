"""Bounded recovery from underground positions with unreachable surface goals."""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from .movement_recovery import block_position


_SURFACE_BLOCKS = [
    "minecraft:grass_block",
    "minecraft:dirt",
    "minecraft:coarse_dirt",
    "minecraft:podzol",
    "minecraft:sand",
    "minecraft:gravel",
    "minecraft:stone",
]

_NON_BREATHABLE_BLOCK_TOKENS = (
    "water",
    "lava",
    "bubble_column",
    "kelp",
    "seagrass",
)


def _configure_surface_pathing(
    client: Any,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Apply upward and water-surface constraints before starting movement."""
    for command in (
        "#set allowBreak true",
        "#set allowPlace true",
        "#set allowDownward false",
        # This is a Baritone pathfinding assumption, not a movement ability.
        # Enabling it makes routes treat water as solid while Minecraft still
        # lets the player sink, which can strand the bot on a riverbed.
        "#set assumeWalkOnWater false",
    ):
        client.transport.dispatch("chat", {"message": command})
        # Chat settings apply on Minecraft ticks. Starting a path in the same
        # instant can retain the previous unsafe value for its first plan.
        sleep(0.1)


def _block_is_breathable(block_id: object) -> bool:
    """Return whether a player's head can breathe in the reported block."""
    value = str(block_id)
    return not any(token in value for token in _NON_BREATHABLE_BLOCK_TOKENS)


def _loaded_breathing_level_above(
    client: Any,
    position: tuple[int, int, int],
    *,
    scan_height: int = 32,
) -> Optional[int]:
    """Return the feet Y just below loaded breathing air in this column."""
    x, y, z = position
    for head_y in range(y + 1, y + max(1, int(scan_height)) + 1):
        try:
            block = client.transport.dispatch(
                "get_block",
                {"x": x, "y": head_y, "z": z},
            ).get("id", "")
        except Exception:
            return None
        if _block_is_breathable(block):
            return head_y - 1
    return None


def _start_loaded_column_ascent(
    client: Any,
    position: tuple[int, int, int],
) -> bool:
    """Prefer an upward-only Y goal when the water surface is already loaded."""
    target_y = _loaded_breathing_level_above(client, position)
    if target_y is None or target_y <= position[1]:
        return False
    try:
        client.transport.dispatch("cancel", {})
        client.transport.dispatch(
            "goal",
            {"type": "yLevel", "value": target_y},
        )
    except Exception:
        return False
    print(f"SURVIVAL: ascending loaded water column to y={target_y}")
    return True


def _head_is_dry(client: Any, position: tuple[int, int, int]) -> bool:
    try:
        block = client.transport.dispatch(
            "get_block",
            {"x": position[0], "y": position[1] + 1, "z": position[2]},
        ).get("id", "")
    except Exception:
        return False
    return _block_is_breathable(block)


def reach_breathing_air(
    client: Any,
    *,
    timeout: float,
    ensure_alive: Callable[[Any, Optional[dict]], None],
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Run ``#surface`` without allowing a bad route to descend farther."""
    initial = block_position(client.transport.dispatch("get_state", {}))
    _configure_surface_pathing(client, sleep=sleep)
    if not _start_loaded_column_ascent(client, initial):
        client.transport.dispatch("chat", {"message": "#surface"})
    deadline = clock() + max(0.0, timeout)
    try:
        while clock() < deadline:
            state = client.transport.dispatch("get_state", {})
            ensure_alive(client, state)
            current = block_position(state)
            if _head_is_dry(client, current):
                return True
            if current[1] < initial[1] - 2:
                print("SURVIVAL: surface route moved downward; aborting it")
                return False
            sleep(0.5)
        return False
    finally:
        client.transport.dispatch("chat", {"message": "#stop"})
        client.transport.dispatch("cancel", {})


def reach_dry_surface(
    client: Any,
    *,
    origin: tuple[int, int, int],
    expected_y: int,
    goto: Callable[..., bool],
    search_radius: int = 48,
    attempt_limit: int = 8,
    command_timeout: float = 90.0,
) -> Optional[tuple[int, int, int]]:
    """Try broader surface columns, then a verified upward-only surface command."""
    ox, oy, oz = origin
    _configure_surface_pathing(client)
    try:
        response = client.transport.dispatch(
            "find_blocks",
            {
                "blocks": _SURFACE_BLOCKS,
                "radius": int(search_radius),
                "limit": 4096,
            },
        )
    except Exception:
        response = {}
    highest_by_column = {}
    for block in response.get("found", []):
        x, y, z = int(block["x"]), int(block["y"]) + 1, int(block["z"])
        highest_by_column[(x, z)] = max(y, highest_by_column.get((x, z), -64))
    candidates = []
    for (x, z), target_y in highest_by_column.items():
        distance_sq = (x - ox) ** 2 + (z - oz) ** 2
        if distance_sq < 8**2 or target_y < expected_y - 8:
            continue
        candidates.append((abs(target_y - expected_y), distance_sq, x, target_y, z))
    for _height_delta, _distance, x, target_y, z in sorted(candidates)[:attempt_limit]:
        if not goto(client, x, target_y, z, timeout=45.0):
            continue
        current = block_position(client.transport.dispatch("get_state", {}))
        if current[1] >= target_y - 3 and _head_is_dry(client, current):
            return current

    if not _start_loaded_column_ascent(client, origin):
        client.transport.dispatch("chat", {"message": "#surface"})
    deadline = time.monotonic() + max(0.0, command_timeout)
    try:
        while time.monotonic() < deadline:
            current = block_position(client.transport.dispatch("get_state", {}))
            if current[1] < oy - 2:
                return None
            if current[1] >= expected_y - 3 and _head_is_dry(client, current):
                return current
            time.sleep(0.5)
    finally:
        client.transport.dispatch("chat", {"message": "#stop"})
        client.transport.dispatch("cancel", {})
    return None
