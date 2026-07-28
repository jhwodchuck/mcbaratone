"""Bounded relocation and excavation for stalled base-site discovery."""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from .escape_recovery import destination_safe
from .movement_recovery import block_position


_GROUND_BLOCKS = [
    "minecraft:grass_block",
    "minecraft:dirt",
    "minecraft:coarse_dirt",
    "minecraft:podzol",
    "minecraft:sand",
    "minecraft:gravel",
    "minecraft:stone",
]


def relocate_build_site_search(
    client: Any,
    *,
    attempt: int,
    goto: Optional[Callable[..., bool]] = None,
    search_radius: int = 64,
    attempt_limit: int = 8,
) -> bool:
    """Move far enough to load a different candidate build-site view."""
    if goto is None:
        from .navigation import goto

    origin = block_position(client.transport.dispatch("get_state", {}))
    try:
        response = client.transport.dispatch(
            "find_blocks",
            {
                "blocks": _GROUND_BLOCKS,
                "radius": int(search_radius),
                "limit": 4096,
            },
        )
    except Exception:
        return False
    highest_by_column = {}
    for block in response.get("found", []):
        x, y, z = int(block["x"]), int(block["y"]) + 1, int(block["z"])
        highest_by_column[(x, z)] = max(y, highest_by_column.get((x, z), -64))
    candidates = []
    for (x, z), y in highest_by_column.items():
        distance_sq = (x - origin[0]) ** 2 + (z - origin[2]) ** 2
        if 16**2 <= distance_sq <= search_radius**2:
            candidate = (x, y, z)
            if destination_safe(client, *candidate):
                candidates.append((distance_sq, candidate))
    ranked = [candidate for _distance, candidate in sorted(candidates)]
    if ranked:
        offset = max(0, int(attempt) - 1) % len(ranked)
        ranked = ranked[offset:] + ranked[:offset]
    for candidate in ranked[:attempt_limit]:
        print(f"  Relocating build-site search to {candidate}")
        if not goto(client, *candidate, timeout=120.0, tolerance=3.0):
            continue
        current = block_position(client.transport.dispatch("get_state", {}))
        moved_sq = (
            (current[0] - origin[0]) ** 2
            + (current[2] - origin[2]) ** 2
        )
        if moved_sq >= 12**2 and destination_safe(client, *current):
            return True
    return False


def excavate_surface_egress(
    client: Any,
    *,
    origin: tuple[int, int, int],
    expected_y: int,
    timeout_per_attempt: float = 45.0,
) -> Optional[tuple[int, int, int]]:
    """Tunnel diagonally upward when walk-only and ``#surface`` routes fail."""
    ox, oy, oz = origin
    for command in (
        "#set allowBreak true",
        "#set allowPlace true",
        "#set allowDownward false",
    ):
        client.transport.dispatch("chat", {"message": command})
    for dx, dz in ((8, 0), (-8, 0), (0, 8), (0, -8)):
        target = {"x": ox + dx, "y": expected_y, "z": oz + dz, "radius": 2}
        print(
            "  Surface routes were unreachable; excavating an upward egress "
            f"toward ({target['x']}, {target['y']}, {target['z']})"
        )
        client.transport.dispatch("tunnel", target)
        deadline = time.monotonic() + max(0.0, timeout_per_attempt)
        checks = 0
        while time.monotonic() < deadline:
            state = client.transport.dispatch("get_state", {})
            current = block_position(state)
            if current[1] >= expected_y - 3 and current[1] >= oy + 3:
                client.transport.dispatch("cancel", {})
                return current
            checks += 1
            if checks >= 2 and not state.get("is_pathing", True):
                break
            time.sleep(0.5)
        client.transport.dispatch("cancel", {})
    return None
