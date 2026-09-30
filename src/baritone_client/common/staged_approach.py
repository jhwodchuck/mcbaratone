"""Long-haul approach to a distant exact goal, in bounded legs.

Split out of navigation.py, which is over its module budget.

Baritone rejects an exact GoalBlock whose chunk and terrain are still unknown,
so a return of 100+ blocks has to be walked in stages and the exact height
retried once the destination column is loaded. The hard-won rule here is that
partial progress must survive a failure: an intermediate waypoint that cannot
be reached says nothing about the route as a whole, and abandoning on it both
discards the distance already covered and skips the whole-route fallback.
"""

from __future__ import annotations

import math


#: How close counts as arriving when Baritone keeps refusing the exact goal.
#: Wide enough to cover a doorway the pathfinder will not stand exactly on,
#: narrow enough that the caller's next move is a short hop in loaded chunks.
ARRIVAL_RADIUS = 8.0


def _horizontal_gap_to(client, target_x: int, target_z: int):
    """Blocks still to cover, or None when the position cannot be read.

    Reported on a blocked leg so the log shows whether a return is closing on
    the target or stuck: a route that keeps failing 17 blocks out is a
    different problem from one that never leaves the start.
    """
    try:
        state = client.transport.dispatch("get_state", {})
        position = state.get("block_position", state.get("position", {}))
        return math.hypot(
            float(position["x"]) - target_x, float(position["z"]) - target_z
        )
    except Exception:
        return None


def staged_goto(
    client,
    target: tuple[int, int, int],
    origin: tuple[int, int, int],
    *,
    maximum_leg: float = 32.0,
    navigate=None,
) -> bool:
    """Approach a distant exact goal through bounded horizontal legs.

    A blocked intermediate waypoint is not a blocked route. This used to
    ``return False`` the moment one leg failed, which threw away every leg
    already walked and skipped the whole-route fallback below -- the one most
    likely to work. Live 2026-08-17 A1Bot did both halves of that: some
    attempts died on the first leg after four seconds without moving, and one
    walked four legs to within 17 blocks of the door before the final approach
    failed and the entire 135-block journey was discarded. The phase then
    restarted the whole return from scratch, every time.

    So a failed leg now stops the staging loop and hands over to the final
    approach from wherever the bot actually reached.
    """
    # Imported at call time: navigation re-exports this module, so a top-level
    # import would be circular, and resolving here keeps these patchable.
    from .navigation import _loaded_stage_y, goto, goto_xz

    navigate = navigate or goto
    current_x, current_y, current_z = origin
    target_x, target_y, target_z = target
    horizontal = math.hypot(target_x - current_x, target_z - current_z)
    if horizontal <= 48:
        return False
    stages = max(1, int(horizontal // maximum_leg))
    for index in range(1, stages + 1):
        ratio = min(1.0, (index * maximum_leg) / horizontal)
        nominal_y = round(current_y + (target_y - current_y) * ratio)
        waypoint = (
            round(current_x + (target_x - current_x) * ratio),
            _loaded_stage_y(
                client,
                round(current_x + (target_x - current_x) * ratio),
                nominal_y,
                round(current_z + (target_z - current_z) * ratio),
            ),
            round(current_z + (target_z - current_z) * ratio),
        )
        print(f"  Staging home approach via {waypoint}...")
        if not navigate(
            client,
            waypoint[0],
            waypoint[1],
            waypoint[2],
            timeout=90,
            check_interval=1.0,
            tolerance=6.0,
        ):
            print(
                "  Exact staging height was rejected; retrying the column "
                "without pinning Y..."
            )
            if not goto_xz(
                client,
                waypoint[0],
                waypoint[2],
                timeout=90,
                tolerance=6.0,
            ):
                remaining = _horizontal_gap_to(client, target_x, target_z)
                if remaining is None:
                    print("  Staging leg blocked; trying the final approach anyway...")
                else:
                    print(
                        f"  Staging leg blocked with {remaining:.0f} blocks left "
                        f"of {horizontal:.0f}; trying the final approach from here..."
                    )
                break
    if navigate(
        client,
        target_x,
        target_y,
        target_z,
        timeout=180,
        check_interval=1.0,
        tolerance=2.0,
    ):
        return True
    # Load the final column, then retry its exact height. A failed column move
    # is not a reason to skip that retry: the bot is often already standing in
    # the column, which is exactly when goto_xz has nothing to do and reports
    # failure.
    goto_xz(client, target_x, target_z, timeout=120, tolerance=6.0)
    if navigate(
        client,
        target_x,
        target_y,
        target_z,
        timeout=90,
        check_interval=1.0,
        tolerance=2.0,
    ):
        return True
    # Standing next to the goal is arriving. Reporting failure here discards
    # the whole journey and makes the caller walk it again from scratch --
    # live, a return that reached 17 blocks from the door was thrown away and
    # restarted repeatedly. The caller's next move is a short local hop in
    # loaded chunks, which is far likelier to succeed than this exact goal.
    remaining = _horizontal_gap_to(client, target_x, target_z)
    if remaining is not None and remaining <= ARRIVAL_RADIUS:
        from .home_surface import below_home_surface, home_route_floor
        guard = home_route_floor(client, target_x, target_y, target_z)
        if guard is not None:
            try:
                live = client.transport.dispatch("get_state", {})
                if below_home_surface(guard, live.get("block_position", live.get("position", {}))):
                    return False
            except Exception:
                return False
        print(
            f"  Exact goal refused, but arrived within {remaining:.0f} blocks; "
            "treating the approach as complete."
        )
        return True
    return False
