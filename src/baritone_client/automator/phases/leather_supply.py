"""Cost control for the leather objective in an animal-depleted world.

``ENCHANTING_PIPELINE`` needs 46 leather (45 books for 15 bookshelves plus one
for the enchanting table), which is roughly 46 kills. Passive mobs only spawn
during chunk generation, so a region hunted to zero never refills: once the
fleet has cleared its surroundings, every later expedition searches an area
that cannot contain a target.

That interacts badly with the objective graph. Abandoned objectives are
re-armed roughly once a minute so a run can never permanently stall, so an
unconditional expedition here is retried forever. Measured live: one worker
logged 90 consecutive failed leather attempts -- zero leather gained, zero
deaths, zero other progress -- while a live survey showed no cow, sheep, pig,
or chicken within 256 blocks of any worker, and the operator's known-herd
waypoint held no animals at all.

The rule these helpers enforce is the one that generalises: measure closing
progress (leather actually gained), not effort spent, and stop paying full
price for a search that has repeatedly returned nothing -- without ever
declaring the goal permanently impossible, because a bred herd or newly
generated chunks can make it reachable again.
"""

from __future__ import annotations

import time
from typing import Any

from ...common.husbandry import survey_leather_sources

#: Consecutive attempts that gained no leather at all.
NO_GAIN_KEY = "leather_attempts_without_gain"
#: Attempts allowed to fail normally before a survey must justify another
#: full expedition.
NO_GAIN_LIMIT = 3
#: Even while suppressed, run one real expedition this often so a world that
#: regains animals is still discovered.
FULL_RETRY_EVERY = 10

#: Expiry for a herd waypoint proven empty or unreachable.
HERD_EXHAUSTED_KEY = "leather_herd_exhausted_until"
HERD_EXHAUSTED_SECONDS = 3 * 3600.0

SURVEY_RADIUS = 64


def _streak(state: Any) -> int:
    try:
        return int(state.custom_data.get(NO_GAIN_KEY, 0) or 0)
    except (TypeError, ValueError):
        return 0


def record_attempt(state: Any, *, gained: bool) -> None:
    """Track consecutive attempts that produced no leather."""
    state.custom_data[NO_GAIN_KEY] = 0 if gained else _streak(state) + 1


def search_is_futile(client: Any, state: Any) -> bool:
    """True when another full expedition is not worth its cost right now.

    Once several consecutive expeditions have gained nothing, a live survey
    decides: with no leather-dropping animal loaded nearby, the ten-minute
    hunt and the walk to the herd would repeat a known-empty search. Failing
    in seconds instead leaves the runtime free for objectives that can still
    move.
    """
    streak = _streak(state)
    if streak < NO_GAIN_LIMIT:
        return False
    if streak % FULL_RETRY_EVERY == 0:
        print(f"  Leather search has stalled {streak}x; forcing a full retry.")
        return False

    sources = survey_leather_sources(client, radius=SURVEY_RADIUS)
    if sources:
        summary = ", ".join(f"{name}={count}" for name, count in sources.items())
        print(f"  Leather sources are in range ({summary}); hunting.")
        return False

    print(
        f"  No leather-dropping animal is loaded nearby and the last {streak} "
        "expeditions gained none; skipping this pass."
    )
    record_attempt(state, gained=False)
    return True


def herd_waypoint_is_exhausted(state: Any) -> bool:
    """True while the known-herd waypoint is suppressed."""
    try:
        until = float(state.custom_data.get(HERD_EXHAUSTED_KEY, 0) or 0)
    except (TypeError, ValueError):
        return False
    if time.time() >= until:
        return False
    print("  Known herd waypoint is marked empty/unreachable; skipping it.")
    return True


def mark_herd_waypoint_exhausted(state: Any) -> None:
    """Suppress the waypoint for a while, but never permanently.

    The cooldown expires so a herd that is bred back up, or a route that
    becomes passable, is still found later.
    """
    state.custom_data[HERD_EXHAUSTED_KEY] = time.time() + HERD_EXHAUSTED_SECONDS
    print(
        "  Known herd yielded nothing; suppressing that waypoint for "
        f"{HERD_EXHAUSTED_SECONDS / 3600:.0f}h."
    )
