"""Small, transport-agnostic helpers for emergency food recovery."""

import time
from typing import Callable, Iterable


_EDIBLE_DROP_NAMES = (
    "beef",
    "porkchop",
    "chicken",
    "mutton",
    "rabbit",
    "salmon",
    "cod",
    "tropical fish",
    "bread",
    "potato",
    "carrot",
    "apple",
    "rotten flesh",
)


def bounded_exploration_origin(
    position: dict,
    requested_center,
    max_distance: float,
) -> tuple[float, float]:
    """Rebase a stale bounded-search center to the live player position."""
    current = (
        float(position.get("x", 0) or 0),
        float(position.get("z", 0) or 0),
    )
    if requested_center is None:
        return current
    requested = (
        float(requested_center[0]),
        float(requested_center[1]),
    )
    distance = (
        (current[0] - requested[0]) ** 2
        + (current[1] - requested[1]) ** 2
    ) ** 0.5
    if distance > max(0.0, float(max_distance)):
        print(
            "RECOVERY: rebasing out-of-range exploration center "
            f"after {distance:.1f} blocks"
        )
        return current
    return requested


def collect_edible_drop(
    client,
    entities: Iterable[dict],
    goto: Callable[..., bool],
) -> bool:
    """Collect the closest reachable edible item entity."""
    drops = [
        entity
        for entity in entities
        if entity.get("type") == "minecraft:item"
        and any(
            name in str(entity.get("name", "")).lower()
            for name in _EDIBLE_DROP_NAMES
        )
    ]
    for drop in sorted(
        drops,
        key=lambda value: float(value.get("distance", 999)),
    ):
        position = drop.get("position") or {}
        if not all(axis in position for axis in ("x", "y", "z")):
            continue
        if goto(
            client,
            int(position["x"]),
            int(position["y"]),
            int(position["z"]),
            timeout=30,
            check_interval=0.25,
            tolerance=0.75,
        ):
            time.sleep(1)
            return True
    return False


def must_hold_for_critical_food(
    state: dict,
    *,
    minimum_health: float,
    current_food: int,
    critical_health_floor: float = 6.0,
) -> bool:
    """Return whether blind exploration is unsafe without a loaded target.

    ``minimum_health`` is the caller's general "safe to resume work" target
    (every production call site passes 12.0) -- NOT a near-death threshold.
    This function used to gate on ``health < minimum_health``, which is true
    for essentially the entire duration of every recovery attempt, since
    that is exactly the condition that triggered recovery in the first
    place. The exploration this gates is already bounded and safe (sprint
    disabled, distance-capped, aborts the moment a threat appears -- see
    ``acquire_emergency_food``), so blocking it for the whole recovery
    permanently soft-locks a bot at low health/food: it can never eat
    without exploring, and health never regenerates below food 18. Live
    fleet symptom: Bot07/Bot09 held at 6.3/20 and 3.5/20 indefinitely,
    re-entering "holding instead of expanding the search" every cycle.
    Compare against a fixed, much lower critical floor instead so the hold
    only fires when actually near death, not for the whole recovery.
    """
    health = float(state.get("health", 20) or 0)
    return current_food <= 2 or (
        health < critical_health_floor and current_food < 18
    )
