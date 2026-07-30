from types import SimpleNamespace

from baritone_client.automator.objective import (
    ObjStatus,
    ObjectivePlanner,
    default_objectives,
)
from baritone_client.automator.stall_recovery import (
    OBJECTIVE_RUNTIME_REVISION,
    maintain_stalled_survival,
    rearm_abandoned_objectives,
    rearm_recovered_survival_objectives,
)
from baritone_client.automator.state_manager import Phase


def test_runtime_revision_rearms_abandoned_objective_once():
    planner = ObjectivePlanner(default_objectives())
    objective = planner._by_phase[Phase.BOOT_SEQUENCE]
    objective.status = ObjStatus.ABANDONED
    objective.attempts = 3
    objective.interruptions = 4
    objective.no_progress_streak = 2
    objective.last_failure = "phase_failed"
    custom_data = {}

    assert rearm_abandoned_objectives(planner, custom_data) == [
        "BOOT_SEQUENCE"
    ]
    assert objective.status is ObjStatus.PENDING
    assert objective.attempts == 0
    assert objective.interruptions == 0
    assert objective.no_progress_streak == 0
    assert objective.last_failure == ""
    assert custom_data["objective_runtime_revision"] == OBJECTIVE_RUNTIME_REVISION

    objective.status = ObjStatus.ABANDONED
    objective.attempts = 3
    assert rearm_abandoned_objectives(planner, custom_data) == []
    assert objective.status is ObjStatus.ABANDONED
    assert objective.attempts == 3


def test_runtime_revision_refreshes_blocked_objective_budget():
    planner = ObjectivePlanner(default_objectives())
    objective = planner._by_phase[Phase.BOOT_SEQUENCE]
    objective.status = ObjStatus.BLOCKED
    objective.attempts = 2
    objective.interruptions = 3
    objective.no_progress_streak = 1
    objective.last_failure = "phase_failed"
    custom_data = {}

    assert rearm_abandoned_objectives(planner, custom_data) == [
        "BOOT_SEQUENCE"
    ]
    assert objective.status is ObjStatus.PENDING
    assert objective.attempts == 0
    assert objective.interruptions == 0
    assert objective.no_progress_streak == 0
    assert objective.last_failure == ""


def test_stalled_survival_actively_recovers_low_food(monkeypatch):
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, _payload: {
                "health": 20,
                "food_level": 5,
                "world_time": 1000,
            }
        )
    )
    attempts = []
    monkeypatch.setattr(
        "baritone_client.common.combat.ensure_alive",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "baritone_client.common.combat.scan_for_threats",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "baritone_client.common.combat.acquire_emergency_food",
        lambda _client, **kwargs: attempts.append(kwargs) or True,
    )

    assert maintain_stalled_survival(client) == "food_recovery"
    assert attempts == [
        {
            "minimum_health": 12.0,
            "minimum_food": 14,
            "timeout": 120.0,
        }
    ]


def test_safe_survival_recovery_reopens_abandoned_objective():
    planner = ObjectivePlanner(default_objectives())
    objective = planner._by_phase[Phase.BOOT_SEQUENCE]
    objective.status = ObjStatus.ABANDONED
    objective.attempts = 2
    objective.interruptions = 4
    objective.no_progress_streak = 3
    objective.last_failure = "survival_recovery"
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda _route, _payload: {
                "health": 13.0,
                "food_level": 17,
            }
        )
    )

    assert rearm_recovered_survival_objectives(planner, client) == [
        "BOOT_SEQUENCE"
    ]
    assert objective.status is ObjStatus.PENDING
    assert objective.interruptions == 0
    assert objective.no_progress_streak == 0


def test_unsafe_or_phase_failed_objective_is_not_reopened():
    planner = ObjectivePlanner(default_objectives())
    objective = planner._by_phase[Phase.BOOT_SEQUENCE]
    objective.status = ObjStatus.ABANDONED
    objective.last_failure = "survival_recovery"
    unsafe = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda _route, _payload: {
                "health": 20.0,
                "food_level": 9,
            }
        )
    )
    assert rearm_recovered_survival_objectives(planner, unsafe) == []

    objective.last_failure = "phase_failed"
    safe = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda _route, _payload: {
                "health": 20.0,
                "food_level": 20,
            }
        )
    )
    assert rearm_recovered_survival_objectives(planner, safe) == []


def _abandoned_boot_planner(last_failure="phase_failed"):
    """Reproduces the live checkpoint: the three bootstrap objectives DONE,
    BOOT_SEQUENCE abandoned, everything downstream still PENDING."""
    planner = ObjectivePlanner(default_objectives())
    for done in (Phase.BRIDGE_CHECK, Phase.SPAWN_BOOTSTRAP, Phase.INITIAL_GATHERING):
        planner._by_phase[done].status = ObjStatus.DONE
    objective = planner._by_phase[Phase.BOOT_SEQUENCE]
    objective.status = ObjStatus.ABANDONED
    objective.attempts = 3
    objective.last_failure = last_failure
    return planner, objective


def test_abandoned_mid_chain_objective_bricks_the_whole_graph():
    """Documents the failure this repair exists for: the graph is a strict
    chain, so abandoning BOOT_SEQUENCE leaves every downstream objective
    unreachable forever. Measured live: ~9 days, ~4,800 controller restarts,
    zero phase completions, with BOOT_SEQUENCE abandoned and all ten
    downstream objectives PENDING on a prerequisite that can never be met."""
    from baritone_client.automator.stall_recovery import (
        rearm_any_abandoned_objectives,
    )

    planner, _ = _abandoned_boot_planner()
    assert not planner.runnable(), "nothing should be runnable once boot is abandoned"

    reopened = rearm_any_abandoned_objectives(planner, {}, now=1000.0)

    assert "BOOT_SEQUENCE" in reopened
    assert planner.runnable(), "the graph must be recoverable"


def test_full_rearm_covers_failures_the_survival_repair_ignores():
    """rearm_recovered_survival_objectives only matches last_failure ==
    'survival_recovery'. BOOT_SEQUENCE died with 'player_death' live, so it
    was never re-opened by anything."""
    from baritone_client.automator.stall_recovery import (
        rearm_any_abandoned_objectives,
    )

    planner, objective = _abandoned_boot_planner(last_failure="player_death")
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda *_a, **_k: {"health": 20.0, "food_level": 20}
        )
    )

    assert rearm_recovered_survival_objectives(planner, client) == []
    assert rearm_any_abandoned_objectives(planner, {}, now=1000.0) == ["BOOT_SEQUENCE"]
    assert objective.status is ObjStatus.PENDING


def test_full_rearm_is_rate_limited_so_it_cannot_hot_loop():
    """A phase that fails instantly must not ping-pong between abandonment
    and re-arm."""
    from baritone_client.automator.stall_recovery import (
        rearm_any_abandoned_objectives,
    )

    planner, objective = _abandoned_boot_planner()
    custom_data = {}

    assert rearm_any_abandoned_objectives(
        planner, custom_data, now=1000.0, cooldown_seconds=300.0
    ) == ["BOOT_SEQUENCE"]

    objective.status = ObjStatus.ABANDONED  # fails again immediately
    assert rearm_any_abandoned_objectives(
        planner, custom_data, now=1100.0, cooldown_seconds=300.0
    ) == [], "must respect the cooldown"

    assert rearm_any_abandoned_objectives(
        planner, custom_data, now=1400.0, cooldown_seconds=300.0
    ) == ["BOOT_SEQUENCE"], "and retry once the cooldown expires"
