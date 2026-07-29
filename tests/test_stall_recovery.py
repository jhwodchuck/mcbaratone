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
