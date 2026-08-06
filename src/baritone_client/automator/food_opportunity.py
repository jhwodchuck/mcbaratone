"""Bounded food recovery for resource roles that cannot safely continue."""

from __future__ import annotations

from typing import Any, Mapping

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
            "the iron supplier needs food before bounded mining can resume",
        )
    return None


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
    "run_scheduled_food_recovery",
    "select_food_recovery_opportunity",
]
