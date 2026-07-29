"""Recovery policy for exhausted objective graphs.

The goal graph intentionally persists failure budgets, but a code repair must
be able to re-open objectives that were abandoned by the repaired behavior.
When a graph still stalls after re-arming, keep the controller attached and
perform only bounded survival work instead of exiting into a restart loop.
"""

from __future__ import annotations

from typing import Any, MutableMapping

from .objective import ObjStatus, ObjectivePlanner


OBJECTIVE_RUNTIME_REVISION = 12


def rearm_abandoned_objectives(
    planner: ObjectivePlanner,
    custom_data: MutableMapping[str, Any],
) -> list[str]:
    """Re-open abandoned objectives once for the current runtime revision."""
    try:
        saved_revision = int(custom_data.get("objective_runtime_revision", 0) or 0)
    except (TypeError, ValueError):
        saved_revision = 0
    if saved_revision >= OBJECTIVE_RUNTIME_REVISION:
        return []

    reopened = []
    for objective in planner.objectives:
        if objective.status is not ObjStatus.ABANDONED:
            continue
        objective.status = ObjStatus.PENDING
        objective.attempts = 0
        objective.interruptions = 0
        objective.no_progress_streak = 0
        objective.last_failure = ""
        reopened.append(objective.phase.name)

    custom_data["objective_runtime_revision"] = OBJECTIVE_RUNTIME_REVISION
    return reopened


def maintain_stalled_survival(client: Any) -> str:
    """Perform one bounded survival pass while progression awaits repair."""
    from ..common.base import wait_for_safe_daylight
    from ..common.combat import (
        acquire_emergency_food,
        defend_or_flee,
        ensure_alive,
        scan_for_threats,
    )

    state = client.transport.dispatch("get_state", {})
    ensure_alive(client, state)
    health = float(state.get("health", 20) or 0)
    food = int(state.get("food_level", state.get("food", 20)) or 0)
    world_time = int(state.get("world_time", 0) or 0) % 24000

    threats = scan_for_threats(client, radius=16, player_state=state)
    if threats:
        defend_or_flee(client)
        return "defended"

    if world_time >= 12000:
        wait_for_safe_daylight(client, max_wait=720.0, poll_interval=5.0)
        return "sheltered"

    if health < 12.0 or food < 14:
        acquire_emergency_food(
            client,
            minimum_health=12.0,
            minimum_food=14,
            timeout=120.0,
        )
        return "food_recovery"

    client.transport.dispatch("cancel", {})
    return "holding"


def rearm_recovered_survival_objectives(
    planner: ObjectivePlanner,
    client: Any,
) -> list[str]:
    """Re-open objectives abandoned only because survival was temporarily low."""
    state = client.transport.dispatch("get_state", {})
    health = float(state.get("health", 20) or 0)
    food = int(state.get("food_level", state.get("food", 20)) or 0)
    if health < 12.0 or food < 14:
        return []

    reopened = []
    for objective in planner.objectives:
        if (
            objective.status is not ObjStatus.ABANDONED
            or objective.last_failure != "survival_recovery"
        ):
            continue
        objective.status = ObjStatus.PENDING
        objective.attempts = 0
        objective.interruptions = 0
        objective.no_progress_streak = 0
        objective.last_failure = ""
        reopened.append(objective.phase.name)
    return reopened
