"""Recovery policy for failed or exhausted objective graphs.

The goal graph intentionally persists failure budgets, but a code repair must
be able to re-open objectives that were blocked or abandoned by the repaired
behavior.
When a graph still stalls after re-arming, keep the controller attached and
perform only bounded survival work instead of exiting into a restart loop.
"""

from __future__ import annotations

from typing import Any, MutableMapping

from .objective import ObjStatus, ObjectivePlanner


OBJECTIVE_RUNTIME_REVISION = 39


def report_stall(planner: ObjectivePlanner) -> None:
    """Print the terminal-looking diagnostic for a genuinely exhausted graph."""
    abandoned = [
        objective.phase.name
        for objective in planner.objectives
        if objective.status is ObjStatus.ABANDONED
    ]
    pending = [
        objective.phase.name
        for objective in planner.objectives
        if objective.status in {ObjStatus.PENDING, ObjStatus.BLOCKED}
    ]
    print("\n" + "=" * 60)
    print("  Automation stalled: no runnable objective remains.")
    if abandoned:
        print(f"  Abandoned: {', '.join(abandoned)}")
    if pending:
        print(f"  Still pending (prerequisites unmet): {', '.join(pending)}")
    print("=" * 60 + "\n")


def rearm_abandoned_objectives(
    planner: ObjectivePlanner,
    custom_data: MutableMapping[str, Any],
) -> list[str]:
    """Re-open failed objectives once for the current runtime revision."""
    try:
        saved_revision = int(custom_data.get("objective_runtime_revision", 0) or 0)
    except (TypeError, ValueError):
        saved_revision = 0
    if saved_revision >= OBJECTIVE_RUNTIME_REVISION:
        return []

    reopened = []
    for objective in planner.objectives:
        if objective.status not in {ObjStatus.BLOCKED, ObjStatus.ABANDONED}:
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


def rearm_any_abandoned_objectives(
    planner: ObjectivePlanner,
    custom_data: MutableMapping[str, Any],
    *,
    now: float,
    cooldown_seconds: float = 45.0,
) -> list[str]:
    """Last-resort re-open of ANY abandoned objective when nothing can run.

    A permanent stall is never a correct terminal state for a bot meant to run
    for weeks. The objective graph is a strict chain -- BOOT_SEQUENCE gates
    BASE_CONSTRUCTION, which gates FOOD_AND_IRON, which gates everything else
    -- so abandoning one mid-chain objective makes the entire remaining
    mission unreachable forever.

    Measured on the live fleet after ~9 days: BOOT_SEQUENCE was abandoned
    after 3 attempts (transient causes -- "Failed to gather wood", "Failed to
    gather cobblestone", a camped creeper), and all ten downstream objectives
    then sat PENDING on a prerequisite that could never be satisfied. Neither
    existing repair could help: ``rearm_abandoned_objectives`` only fires when
    the persisted runtime revision is older than the code constant (a one-shot
    migration hook -- the checkpoint already matched), and
    ``rearm_recovered_survival_objectives`` only re-opens objectives whose
    ``last_failure`` is exactly ``survival_recovery`` (BOOT_SEQUENCE's was
    ``player_death``/``phase_failed``). The result was ~4,800 controller
    restarts with zero phase completions.

    The blockers are overwhelmingly environmental and time-varying -- a
    creeper wanders off, trees regrow, the bot respawns elsewhere -- so
    retrying later is genuinely likely to succeed. Rate-limited so a phase
    that fails instantly cannot hot-loop between abandonment and re-arm.

    The cooldown was 300s, which dominated fleet wall-clock: the stall branch
    sleeps 5s per pass, so one abandonment cost ~60 idle passes. Measured over
    2026-07-31, Bot07 logged 9,296 stall holds (~12.9 hours asleep) against
    1,002 block placements, and Bot18 5,398 (~7.5 hours) against 234. The
    bots were not slow, they were idle. 45s still blocks the hot-loop this
    guard exists to prevent (a phase that fails in under a second cannot
    ping-pong) while cutting the idle tax by ~85%.
    """
    last = custom_data.get("last_full_objective_rearm")
    try:
        last_at = float(last) if last is not None else None
    except (TypeError, ValueError):
        last_at = None
    if last_at is not None and now - last_at < cooldown_seconds:
        return []

    reopened = []
    for objective in planner.objectives:
        if objective.status not in {ObjStatus.BLOCKED, ObjStatus.ABANDONED}:
            continue
        objective.status = ObjStatus.PENDING
        objective.attempts = 0
        objective.interruptions = 0
        objective.no_progress_streak = 0
        objective.last_failure = ""
        reopened.append(objective.phase.name)

    if reopened:
        custom_data["last_full_objective_rearm"] = now
    return reopened


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
