"""Bounded food recovery for resource roles that cannot safely continue."""

from __future__ import annotations

from math import hypot
from typing import Any, Iterable, Mapping

from ..common.combat import acquire_emergency_food
from .local_opportunity import LocalOpportunity, OpportunityKind
from .food_recovery_state import recover_food_from_known_sources
from .phases.iron_age_food import FOOD_ANIMALS


MIN_ROLE_FOOD = 14


def select_food_recovery_opportunity(
    food: int, cooldown_ready: bool
) -> LocalOpportunity | None:
    """Return recovery work when hunger blocks a resource role."""
    if int(food) < MIN_ROLE_FOOD and cooldown_ready:
        return LocalOpportunity(
            OpportunityKind.FOOD_RECOVERY,
            220,
            "the resource role needs food before bounded work can resume",
        )
    return None


def village_food_production_ready(completed: Iterable[Any]) -> bool:
    """Return whether the dedicated village worker can safely run a food cycle.

    Production begins after the same safe bootstrap used by renewable workers.
    It can therefore supply breeding food before village infrastructure is
    verified; it is recurring work, not a terminal graph objective.
    """
    names = {getattr(phase, "name", "") for phase in completed}
    return {"SPAWN_BOOTSTRAP", "INITIAL_GATHERING", "BOOT_SEQUENCE"}.issubset(names)


def select_village_food_production_opportunity(
    food: int,
    completed: Iterable[Any],
    cooldown_ready: bool,
) -> LocalOpportunity | None:
    """Choose recovery first, then one bounded village food-production cycle."""
    if int(food) < MIN_ROLE_FOOD:
        return select_food_recovery_opportunity(food, cooldown_ready)
    if village_food_production_ready(completed) and cooldown_ready:
        return LocalOpportunity(
            OpportunityKind.FOOD_PRODUCTION,
            210,
            "the dedicated village worker can grow, cook, and bank a bounded food batch",
        )
    return None


def cooldown_ready(
    runtime: Mapping[str, Any],
    kind: OpportunityKind,
    now: float,
    cooldowns: Mapping[OpportunityKind, float],
) -> bool:
    """Return whether an opportunity has waited out its recorded cooldown."""
    attempts = runtime.get("opportunities", {})
    record = attempts.get(kind.value, {}) if isinstance(attempts, Mapping) else {}
    try:
        last_attempt = float(record.get("last_attempt", 0) or 0)
    except (AttributeError, TypeError, ValueError):
        last_attempt = 0.0
    return now - last_attempt >= cooldowns[kind]


def reachable_farm_location(signals: Any) -> tuple[int, int, int] | None:
    """Return a nearby observed or checkpointed crop patch."""
    if signals.crop_location is not None:
        return signals.crop_location
    known = signals.known_farm_location
    if known is None or hypot(known[0] - signals.position[0], known[2] - signals.position[2]) > 64:
        return None
    return known


def _banked_total(cycle: Any, fallback: int) -> int:
    """Read the cumulative bank from the public food-cycle result contract."""
    for name in ("total_food_banked", "total_banked", "food_banked", "banked"):
        try:
            value = getattr(cycle, name)
            if value is not None:
                return max(fallback, int(value))
        except (AttributeError, TypeError, ValueError):
            continue
    return fallback


def run_village_food_production(
    client: Any,
    state: Any,
    runtime: Mapping[str, Any],
) -> tuple[bool, str, int, int]:
    """Run one public food-supply cycle and return durable banked totals.

    The import remains local while the core food-supply module lands, keeping
    scheduler importability independent of merge order.
    """
    from ..common.food_supply import run_food_cycle

    before = int(runtime.get("food_banked", 0) or 0)
    cycle = run_food_cycle(client, state)
    after = _banked_total(cycle, before)
    return bool(cycle.success), str(cycle.detail), before, after


def _food_level(client: Any) -> int:
    try:
        state = client.transport.dispatch("get_state", {})
    except Exception:
        return 0
    if not isinstance(state, Mapping):
        return 0
    try:
        return int(state.get("food_level", state.get("food", 0)) or 0)
    except (TypeError, ValueError):
        return 0


def _current_center(client: Any) -> tuple[float, float, float] | None:
    try:
        state = client.transport.dispatch("get_state", {})
    except Exception:
        return None
    if not isinstance(state, Mapping):
        return None
    position = state.get("block_position", state.get("position", {}))
    if not isinstance(position, Mapping):
        return None
    try:
        return (
            float(position["x"]),
            float(position.get("y", 64) or 64),
            float(position["z"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def run_scheduled_food_recovery(
    client: Any,
    state: Any,
    *,
    minimum_food: int = MIN_ROLE_FOOD,
    timeout: float = 120.0,
) -> tuple[bool, str, int, int]:
    """Recover food before a role enters its safety hold.

    Known storage, farm, and herd landmarks are tried first.  If those are
    unavailable, the shared bounded passive-food search gets one opportunity
    around the bot's current position.  The mining safety threshold remains
    unchanged; this only supplies the missing recovery work.
    """
    before = _food_level(client)
    if before >= minimum_food:
        return True, "food already meets the mining threshold", before, before

    if recover_food_from_known_sources(client, state, FOOD_ANIMALS):
        after = _food_level(client)
        if after >= minimum_food:
            return True, "recovered food from a checkpointed source", before, after

    center = _current_center(client)
    recovered = acquire_emergency_food(
        client,
        minimum_health=12.0,
        minimum_food=minimum_food,
        timeout=timeout,
        max_exploration_distance=96.0,
        exploration_center=center,
        return_to_exploration_center=True,
    )
    after = _food_level(client)
    if recovered and after >= minimum_food:
        return True, "recovered food with bounded passive search", before, after
    return False, "no safe food source was secured in the bounded recovery", before, after


__all__ = [
    "MIN_ROLE_FOOD",
    "cooldown_ready",
    "reachable_farm_location",
    "run_scheduled_food_recovery",
    "run_village_food_production",
    "select_food_recovery_opportunity",
    "select_village_food_production_opportunity",
    "village_food_production_ready",
]
