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
#: ...but back that cadence off as the barren streak grows. A fixed every-10th
#: probe never stops costing: Bot17 reached streak 1804, which is roughly 180
#: full expeditions into a world with no animals, and it died repeatedly on
#: them. Doubling the interval keeps rediscovery possible while the price of a
#: hopeless search falls away.
MAX_RETRY_INTERVAL = 320
#: An expedition is a real hunt in the open. Below this the bot is sent out to
#: die rather than to gather; the fleet's leather worker was hunting at 1/4
#: armor while accumulating 17 deaths in six hours.
EXPEDITION_MIN_ARMOR_PIECES = 3
EXPEDITION_MIN_HEALTH = 12.0


def _retry_interval(streak: int) -> int:
    """Return the current forced-full-retry cadence for a barren streak.

    Doubles once per ``FULL_RETRY_EVERY`` failures beyond the limit so early
    stalls still probe often and a long-dead world is probed rarely.
    """
    if streak <= 0:
        return FULL_RETRY_EVERY
    interval = FULL_RETRY_EVERY
    while interval < MAX_RETRY_INTERVAL and streak >= interval * 2:
        interval *= 2
    return interval


def expedition_is_too_dangerous(client: Any) -> bool:
    """True when the bot is too poorly equipped to survive a hunt.

    Deliberately separate from ``search_is_futile``: that asks whether the
    search can succeed, this asks whether the bot can survive attempting it.
    """
    try:
        from ...common.inventory import get_equipped_armor

        armor = len(get_equipped_armor(client))
        state = client.transport.dispatch("get_state", {})
        health = float(state.get("health", 20) or 20)
        difficulty = str(state.get("difficulty", "")).lower()
    except Exception:
        return False
    if difficulty == "peaceful" and health >= EXPEDITION_MIN_HEALTH:
        return False
    if armor >= EXPEDITION_MIN_ARMOR_PIECES and health >= EXPEDITION_MIN_HEALTH:
        return False
    print(
        f"  Deferring leather expedition: {armor}/4 armor and {health:.1f} "
        "health cannot survive an open hunt."
    )
    return True

#: Search rings, in blocks from the expedition origin.
#:
#: The hunt was capped at 160 blocks and rotated 22 sectors whose offsets are
#: all within +/-128, so every "expedition" re-swept one ~300-block box around
#: the starter house. Bot17 ran 1804 of them. Live inspection settled why that
#: can never work: it stands in a savanna -- prime cow/sheep/horse biome -- at
#: Y=105 with 22 hostiles and zero passive mobs inside 128 blocks. Hostiles
#: respawn continuously; passive mobs essentially only spawn at chunk
#: generation, so a region the fleet has lived in for weeks is permanently
#: empty of them. The animals are not gone from the world, only from here, and
#: the sole way to reach one is to leave the box.
EXPEDITION_RING_KEY = "leather_expedition_ring"
EXPEDITION_RINGS = (160.0, 512.0, 1024.0)
#: Barren attempts within one ring before promoting to the next.
RING_EXHAUSTED_STREAK = 12

#: Expiry for a herd waypoint proven empty or unreachable.
HERD_EXHAUSTED_KEY = "leather_herd_exhausted_until"
HERD_EXHAUSTED_SECONDS = 3 * 3600.0

SURVEY_RADIUS = 64


#: Wool shares every property that made leather fail: sheep are passive mobs
#: that do not repopulate a hunted region, and the bed hunt carried the same
#: 160-block cap with its own +/-128 offsets. It tracks its own streak and ring
#: so exhausting one objective's search does not fling the other outward.
WOOL_NO_GAIN_KEY = "wool_attempts_without_gain"
WOOL_RING_KEY = "wool_expedition_ring"


def _streak(state: Any, streak_key: str = NO_GAIN_KEY) -> int:
    try:
        return int(state.custom_data.get(streak_key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def record_attempt(
    state: Any, *, gained: bool, streak_key: str = NO_GAIN_KEY
) -> None:
    """Track consecutive attempts that produced nothing."""
    state.custom_data[streak_key] = 0 if gained else _streak(state, streak_key) + 1


def _ring_index(state: Any, ring_key: str = EXPEDITION_RING_KEY) -> int:
    try:
        return int(state.custom_data.get(ring_key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def expedition_distance(
    state: Any, *, ring_key: str = EXPEDITION_RING_KEY
) -> float:
    """Return how far the current ring may range from the origin."""
    return EXPEDITION_RINGS[
        min(_ring_index(state, ring_key), len(EXPEDITION_RINGS) - 1)
    ]


def sector_scale(state: Any, *, ring_key: str = EXPEDITION_RING_KEY) -> float:
    """Scale factor for sector offsets, so rotation follows the ring outward."""
    return expedition_distance(state, ring_key=ring_key) / EXPEDITION_RINGS[0]


def escalate_ring_if_exhausted(
    state: Any,
    *,
    ring_key: str = EXPEDITION_RING_KEY,
    streak_key: str = NO_GAIN_KEY,
    label: str = "leather",
) -> bool:
    """Promote to the next search ring once this one is proven barren.

    Resets the no-gain streak: that evidence described the old ring, and
    carrying it forward would immediately suppress the wider search before it
    ever ran. Returns True when a promotion happened.
    """
    index = _ring_index(state, ring_key)
    if index >= len(EXPEDITION_RINGS) - 1:
        return False
    if _streak(state, streak_key) < RING_EXHAUSTED_STREAK:
        return False
    state.custom_data[ring_key] = index + 1
    state.custom_data[streak_key] = 0
    print(
        f"  Local {label} sectors are exhausted; expanding the search to "
        f"{EXPEDITION_RINGS[index + 1]:.0f} blocks and resetting the streak."
    )
    return True


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
    interval = _retry_interval(streak)
    if streak % interval == 0:
        print(
            f"  Leather search has stalled {streak}x; forcing a full retry "
            f"(probing every {interval} now)."
        )
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
