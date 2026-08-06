"""Selection policy for durable and temporarily borrowed fleet specialties."""

from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

from ..common.storage_organizer import quartermaster_work_available
from . import food_opportunity
from .common.role_opportunities import select_role_opportunity
from .end_readiness import FleetRole
from .iron_scheduler import iron_cycle_ready
from .local_opportunity import LocalOpportunity, OpportunityKind
from .state_manager import Phase


CooldownReady = Callable[[OpportunityKind, float], bool]


def select_specialty_opportunity(
    *,
    client: Any,
    state: Any,
    role: FleetRole,
    signals: Any,
    completed: Sequence[Phase],
    current_time: float,
    cooldown_ready: CooldownReady,
    allow_recovery: bool,
) -> Optional[LocalOpportunity]:
    """Select work for one primary or temporarily borrowed specialty."""
    completed_set = set(completed)
    if role is FleetRole.BALANCED:
        return None
    if allow_recovery:
        recovery = food_opportunity.select_food_recovery_opportunity(
            signals.food,
            cooldown_ready(OpportunityKind.FOOD_RECOVERY, current_time),
        )
        if recovery is not None:
            return recovery
    if role in {
        FleetRole.END_RUNNER,
        FleetRole.NETHER_SUPPLY,
        FleetRole.ENCHANTING,
    }:
        candidate = select_role_opportunity(
            role, signals, state, cooldown_ready=True
        )
        if candidate and cooldown_ready(candidate.kind, current_time):
            return candidate
        return None
    if role is FleetRole.QUARTERMASTER:
        if (
            signals.safe_for_local_work
            and Phase.BOOT_SEQUENCE in completed_set
            and cooldown_ready(OpportunityKind.STORAGE_MAINTENANCE, current_time)
            and quartermaster_work_available(client, state)
        ):
            return LocalOpportunity(
                OpportunityKind.STORAGE_MAINTENANCE,
                230,
                "shared fleet storage has a leased capacity or migration job",
            )
        return None
    if role is FleetRole.IRON_SUPPLY:
        if (
            iron_cycle_ready(
                signals,
                worker_bootstrapped=Phase.INITIAL_GATHERING in completed_set,
            )
            and cooldown_ready(OpportunityKind.IRON_MINE, current_time)
        ):
            return LocalOpportunity(
                OpportunityKind.IRON_MINE,
                200,
                "the iron supplier can mine, smelt, and bank a bounded batch",
            )
        return None
    if role is FleetRole.VILLAGE_FOOD:
        if not signals.safe_for_local_work:
            return None
        return food_opportunity.select_village_food_production_opportunity(
            signals.food,
            completed,
            cooldown_ready(OpportunityKind.FOOD_PRODUCTION, current_time),
        )
    if role is FleetRole.WOOD_SUPPLY:
        if (
            signals.safe_for_local_work
            and Phase.BOOT_SEQUENCE in completed_set
            and cooldown_ready(OpportunityKind.WOOD_FARM, current_time)
        ):
            return LocalOpportunity(
                OpportunityKind.WOOD_FARM,
                200,
                "the wood supplier can harvest, replant, and bank a bounded batch",
            )
    return None


__all__ = ["select_specialty_opportunity"]
