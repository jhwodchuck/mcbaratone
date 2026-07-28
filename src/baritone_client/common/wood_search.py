"""Explicit tree search used when Baritone's mine process becomes idle."""

from __future__ import annotations

from typing import Any, Callable, Optional, Set


LogPosition = tuple[int, int, int]


def start_explicit_wood_search(
    client: Any,
    *,
    log_blocks: list[str],
    failed_positions: Set[LogPosition],
    exploration_waypoints: Any,
    bounded: bool,
    find_blocks: Callable[..., Optional[dict]],
    find_nearby: Callable[..., Optional[LogPosition]],
    approach_and_break: Callable[[Any, LogPosition], bool],
) -> Optional[bool]:
    """Start a reachable-log action and report whether it is exploration.

    ``None`` means a bounded expedition found no remaining candidate and
    should return home. ``False`` means a direct log was approached; ``True``
    means an exploration command was started.
    """
    found_log = _nearest_untried_log(
        client,
        log_blocks=log_blocks,
        failed_positions=failed_positions,
        find_blocks=find_blocks,
        find_nearby=find_nearby,
    )
    if found_log is None:
        if bounded:
            print("DEBUG: No logs in bounded search radius; returning home")
            client.transport.dispatch("cancel", {})
            return None
        print("DEBUG: No logs found in radius 128. Random exploration...")
        _start_exploration(client, exploration_waypoints)
        return True

    lx, ly, lz = found_log
    print(
        f"DEBUG: Found log at ({lx}, {ly}, {lz}). "
        "Approaching and breaking it directly..."
    )
    if approach_and_break(client, found_log):
        return False

    print(
        "DEBUG: Could not approach explicit log; "
        "exploring for a reachable tree..."
    )
    failed_positions.add(found_log)
    _start_exploration(client, exploration_waypoints)
    return True


def _nearest_untried_log(
    client: Any,
    *,
    log_blocks: list[str],
    failed_positions: Set[LogPosition],
    find_blocks: Callable[..., Optional[dict]],
    find_nearby: Callable[..., Optional[LogPosition]],
) -> Optional[LogPosition]:
    """Return the closest reported log that has not already failed."""
    try:
        response = find_blocks(
            client,
            {
                "blocks": log_blocks,
                "radius": 128,
                "limit": 4096,
            },
            label="Wood nearby log search",
        )
        found = response.get("found", []) if response else []
        candidates = [
            (
                float(item.get("distance", 0)),
                int(item["x"]),
                int(item["y"]),
                int(item["z"]),
            )
            for item in found
            if (int(item["x"]), int(item["y"]), int(item["z"]))
            not in failed_positions
        ]
        if candidates:
            _, x, y, z = min(candidates)
            return (x, y, z)
        return None
    except Exception:
        return find_nearby(client, log_blocks, radius=128)


def _start_exploration(client: Any, exploration_waypoints: Any) -> None:
    """Dispatch the next bounded expanding-ring exploration waypoint."""
    target_x, target_z = exploration_waypoints.next()
    client.transport.dispatch("explore", {"x": target_x, "z": target_z})
