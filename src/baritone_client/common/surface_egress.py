"""Bounded escape from thin high-altitude shelves and spawn platforms."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from .movement_recovery import (
    MovementWatchdog,
    block_position,
    nearest_lower_surface_candidates,
)
from .navigation import goto
from .shelf_escape import harvest_shelf_dirt, supported_column_descent


_SURFACE_BLOCKS = [
    "minecraft:grass_block",
    "minecraft:dirt",
    "minecraft:coarse_dirt",
    "minecraft:podzol",
    "minecraft:stone",
]


def try_lower_surface_egress(
    client: Any,
    initial_state: Dict[str, Any],
    *,
    search_radius: int = 40,
    attempt_limit: int = 8,
    timeout_per_candidate: float = 24.0,
) -> Optional[Tuple[int, int, int]]:
    """Walk to nearby lower terrain before attempting a vertical tunnel.

    A one-block-thick shelf has no safe block to mine underfoot, but it may
    connect horizontally to the mountain.  Faraway base/storage goals often
    fail path calculation from that shelf; short local surface candidates give
    Baritone a solvable first leg.
    """
    origin = block_position(initial_state)
    if origin[1] < 96:
        return None
    try:
        below_floor = client.transport.dispatch(
            "get_block",
            {"x": origin[0], "y": origin[1] - 2, "z": origin[2]},
        ).get("id", "")
    except Exception:
        below_floor = ""
    if not below_floor or "air" in str(below_floor):
        descended = supported_column_descent(client, initial_state)
        if descended is not None:
            print(f"DEBUG: Supported shelf descent reached {descended}")
            return descended
    found = []
    try:
        radii = sorted({min(16, search_radius), min(24, search_radius), search_radius})
        for radius in radii:
            response = client.transport.dispatch(
                "find_blocks",
                {
                    "blocks": _SURFACE_BLOCKS,
                    "radius": int(radius),
                    "limit": 4096,
                },
            )
            found.extend(response.get("found", []))
    except Exception as exc:
        print(f"DEBUG: Lower-surface egress search failed: {exc}")
        return None

    watchdog = MovementWatchdog(last_position=origin)
    candidates = list(
        nearest_lower_surface_candidates(
            {"found": found},
            origin,
            maximum_distance=float(search_radius),
            limit=attempt_limit,
        )
    )

    def attempt(candidate_limit: int, timeout: float):
        for x, y, z in candidates[:candidate_limit]:
            print(f"DEBUG: Trying local lower-surface egress via ({x}, {y}, {z})")
            if not goto(
                client,
                x,
                y,
                z,
                timeout=timeout,
                tolerance=2.0,
            ):
                continue
            try:
                state = client.transport.dispatch("get_state", {})
            except Exception:
                continue
            watchdog.observe(state)
            current = block_position(state)
            if watchdog.stationary_checks == 0 and current[1] <= origin[1] - 2:
                print(
                    "DEBUG: Reached lower connected terrain "
                    f"at ({current[0]}, {current[1]}, {current[2]})"
                )
                return current
        return None

    reached = attempt(min(3, attempt_limit), timeout_per_candidate)
    if reached is not None:
        return reached

    # Prefer a supported one-block-at-a-time descent when any full block is
    # already carried. This conserves material and avoids dropping shelf items
    # into the void.
    shelf_state = client.transport.dispatch("get_state", {})
    descended = supported_column_descent(client, shelf_state)
    if descended is not None:
        print(f"DEBUG: Supported shelf descent reached {descended}")
        return descended

    # With no usable support, make one bounded attempt to recover throwaway
    # blocks from behind the bot and retry the nearby targets.
    if harvest_shelf_dirt(client, shelf_state, target_count=10) >= 6:
        client.transport.dispatch("chat", {"message": "#set allowPlace true"})
        refreshed = client.transport.dispatch("get_state", {})
        watchdog.reset(refreshed)
        return attempt(attempt_limit, max(timeout_per_candidate, 30.0))
    return None
