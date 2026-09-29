"""Selection policy for durable and temporarily borrowed fleet specialties."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Optional, Sequence

from ..common.storage_organizer import quartermaster_work_available
from . import food_opportunity
from .common.role_opportunities import select_role_opportunity
from .end_readiness import FleetRole
from .fleet_coverage import configured_specialty_roles
from .iron_scheduler import iron_cycle_ready
from .local_opportunity import LocalOpportunity, OpportunityKind
from .safety_recovery import select_self_defense
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
    if recovery := select_self_defense(signals):
        return recovery
    completed_set = set(completed)
    if allow_recovery:
        recovery = food_opportunity.select_food_recovery_opportunity(
            signals.food,
            cooldown_ready(OpportunityKind.FOOD_RECOVERY, current_time),
            getattr(signals, "health", 20.0),
        )
        if recovery is not None:
            return recovery
    if role is FleetRole.BALANCED:
        from .local_opportunity import survival_work_allowed

        if signals.safe_for_local_work or survival_work_allowed(signals):
            return food_opportunity.select_balanced_food_production_opportunity(
                signals, state, completed,
                cooldown_ready(OpportunityKind.FOOD_PRODUCTION, current_time),
            )
        return None
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


def select_profile_specialty_opportunities(
    *,
    client: Any,
    state: Any,
    primary_role: FleetRole,
    signals: Any,
    completed: Sequence[Phase],
    current_time: float,
    cooldown_ready: CooldownReady,
    primary_has_work: bool = False,
) -> list[LocalOpportunity]:
    """Select bounded work from explicit recurring secondary roles."""
    selected: list[LocalOpportunity] = []
    for role in configured_specialty_roles(state, primary_role):
        candidate = select_specialty_opportunity(
            client=client,
            state=state,
            role=role,
            signals=signals,
            completed=completed,
            current_time=current_time,
            cooldown_ready=cooldown_ready,
            allow_recovery=False,
        )
        if candidate is not None:
            selected.append(replace(candidate, assigned_role=role.value))
    from .aid_response import select_clear_hostiles_opportunity

    aid = select_clear_hostiles_opportunity(
        client, state, signals, primary_role,
        role_held=primary_role is not FleetRole.BALANCED and not primary_has_work,
        now=current_time,
    )
    if aid is not None:
        selected.append(aid)
    return selected


__all__ = [
    "select_profile_specialty_opportunities",
    "select_specialty_opportunity",
]
