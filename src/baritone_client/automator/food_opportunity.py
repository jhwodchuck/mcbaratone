"""Bounded food recovery for resource roles that cannot safely continue."""

from __future__ import annotations

from math import hypot
from typing import Any, Iterable, Mapping

from ..common.combat import acquire_emergency_food
from ..common.inventory import count_item
from .end_readiness import PREPARED_FOOD_ITEMS
from .local_opportunity import LocalOpportunity, OpportunityKind
from .food_recovery_state import recover_food_from_known_sources
from .phases.iron_age_food import FOOD_ANIMALS


MIN_ROLE_FOOD = 14
#: Minecraft regenerates health only at this food level or above.
REGEN_FOOD = 18
#: The scheduler's local-work comfort gate on health.
LOCAL_WORK_HEALTH = 16.0
BALANCED_PREPARED_FOOD_TARGET = 32


def balanced_prepared_food_hold_reason(signals: Any) -> str:
    """Explain why balanced progression must wait for its next food cycle.

    A bounded production or recovery attempt records a cooldown even when it
    cannot secure food.  That cooldown is pacing, not evidence that a long
    expedition is safe.  Keep the balanced agent out of progression phases
    until the reserve is actually proven by live inventory. The caller limits
    this gate to expedition phases, so it does not block local preparation.
    """
    if not getattr(signals, "observed", False):
        return ""
    prepared = sum(signals.count(item) for item in PREPARED_FOOD_ITEMS)
    if prepared >= BALANCED_PREPARED_FOOD_TARGET:
        return ""
    return (
        f"prepared food reserve is {prepared}/{BALANCED_PREPARED_FOOD_TARGET}; "
        "waiting for the next bounded food cycle"
    )


def needs_regeneration_food(food: int, health: float) -> bool:
    """Wounded below the work threshold with food below the regen floor.

    Health only regenerates at food 18, local work needs health 16, and the
    survival gate only intervenes below 12. In between a bot with no food
    can neither heal nor work: live A1 sat 25 minutes in a cave at health
    15.4 and food 17 doing nothing at all.
    """
    return int(food) < REGEN_FOOD and float(health) < LOCAL_WORK_HEALTH


def select_food_recovery_opportunity(
    food: int, cooldown_ready: bool, health: float = 20.0
) -> LocalOpportunity | None:
    """Return recovery work when hunger blocks a resource role or healing."""
    if cooldown_ready and not int(food) < MIN_ROLE_FOOD and needs_regeneration_food(food, health):
        return LocalOpportunity(
            OpportunityKind.FOOD_RECOVERY,
            220,
            "health cannot regenerate below food 18 and local work needs health 16",
        )
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


def select_balanced_food_production_opportunity(
    signals: Any,
    state: Any,
    completed: Iterable[Any],
    cooldown_ready: bool,
) -> LocalOpportunity | None:
    """Prioritize a real expedition reserve when strategy says it is blocked."""
    if not cooldown_ready or not village_food_production_ready(completed):
        return None
    custom = getattr(state, "custom_data", {}) or {}
    strategic = custom.get("strategic_state", {}) if isinstance(custom, Mapping) else {}
    blocker = str(strategic.get("blocking_condition", "")) if isinstance(strategic, Mapping) else ""
    if "prepared_food_32" not in blocker:
        return None
    prepared = sum(signals.count(item) for item in PREPARED_FOOD_ITEMS)
    if prepared >= BALANCED_PREPARED_FOOD_TARGET:
        return None
    return LocalOpportunity(
        OpportunityKind.FOOD_PRODUCTION,
        215,
        f"prepared food reserve is {prepared}/{BALANCED_PREPARED_FOOD_TARGET}",
        target_item="minecraft:bread",
        assigned_role="balanced",
    )


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


def run_balanced_food_production(
    client: Any,
    state: Any,
) -> tuple[bool, str, int, int]:
    """Run one food cycle while retaining A1's required expedition reserve.

    A nearby herd refills the reserve far faster than wheat, so a bounded
    hunt-and-cook batch runs first; the wheat cycle still follows.
    """
    from ..common.food_supply import run_food_cycle
    from ..common.livestock_food import run_livestock_food_cycle

    def prepared_total() -> int:
        return sum(count_item(client, item) for item in PREPARED_FOOD_ITEMS)

    before = prepared_total()
    livestock = run_livestock_food_cycle(
        client,
        state,
        prepared_now=before,
        target=BALANCED_PREPARED_FOOD_TARGET,
    )
    cycle = run_food_cycle(
        client,
        state,
        personal_food_reserve=BALANCED_PREPARED_FOOD_TARGET,
    )
    after = prepared_total()
    detail = f"{livestock.detail}; {cycle.detail}"
    return bool(livestock.success or cycle.success), detail, before, after


def run_food_production(
    client: Any,
    state: Any,
    runtime: Mapping[str, Any],
    assigned_role: str,
) -> tuple[bool, str, int, int]:
    """Route balanced reserve work separately from a fleet banking cycle."""
    if assigned_role == "balanced":
        return run_balanced_food_production(client, state)
    return run_village_food_production(client, state, runtime)


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


def _health(client: Any) -> float:
    try:
        state = client.transport.dispatch("get_state", {})
    except Exception:
        return 20.0
    if not isinstance(state, Mapping):
        return 20.0
    try:
        return float(state.get("health", 20.0) or 0.0)
    except (TypeError, ValueError):
        return 20.0


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
    if needs_regeneration_food(before, _health(client)):
        minimum_food = max(minimum_food, REGEN_FOOD)
    if before >= minimum_food:
        return True, "food already meets the mining threshold", before, before

    # A local recovery pass can bake/harvest without an expedition. Its bool
    # targets full hunger, so a partial meal still needs a fresh floor check.
    from ..common.survival_farm import local_farm_wait_reason, tend_local_farm_for_food

    tend_local_farm_for_food(client, state)
    after = _food_level(client)
    if after >= minimum_food:
        return True, "recovered food at the nearby farm", before, after

    live = client.transport.dispatch("get_state", {})
    wait_reason = local_farm_wait_reason(client, state, live)
    if wait_reason:
        client.transport.dispatch("cancel", {})
        return False, wait_reason, before, after

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
    "BALANCED_PREPARED_FOOD_TARGET",
    "MIN_ROLE_FOOD",
    "balanced_prepared_food_hold_reason",
    "cooldown_ready",
    "reachable_farm_location",
    "run_scheduled_food_recovery",
    "run_balanced_food_production",
    "run_food_production",
    "run_village_food_production",
    "select_food_recovery_opportunity",
    "select_balanced_food_production_opportunity",
    "select_village_food_production_opportunity",
    "village_food_production_ready",
]
