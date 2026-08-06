from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from baritone_client.automator.automator import EndGameAutomator
from baritone_client.automator import postgame_lifecycle as lifecycle_mod
from baritone_client.automator.postgame_lifecycle import PersistentPostgameLifecycle
from baritone_client.automator.state_manager import Phase


def _decision(*, local_work: bool, summary: str):
    return SimpleNamespace(local_work=local_work, summary=summary)


def _growth_lifecycle(city_progress=None):
    state = SimpleNamespace(custom_data={"city_progress": city_progress or {}})
    state.get_phase_payload = lambda phase: {"center": [10, 20], "target_y": 72}
    scheduler = MagicMock()
    scheduler.next_step.return_value = _decision(local_work=False, summary="idle")
    return state, scheduler, PersistentPostgameLifecycle(state, scheduler, client=MagicMock())


def test_lifecycle_records_terminal_entry_once_and_runs_scheduler_work():
    state = SimpleNamespace(custom_data={})
    scheduler = MagicMock()
    scheduler.next_step.return_value = _decision(
        local_work=True, summary="ADAPTIVE WORK: crop farm verified"
    )
    lifecycle = PersistentPostgameLifecycle(state, scheduler)

    assert lifecycle.enter() is True
    assert lifecycle.claim_completion_notification() is True
    assert lifecycle.enter() is False
    assert lifecycle.claim_completion_notification() is False
    turn = lifecycle.run_turn(MagicMock())

    assert turn.did_work is True
    assert "verified" in turn.reason
    assert state.custom_data["persistent_postgame"] == {
        "entered": True,
        "terminal_objective": "CITY_BUILD",
        "completion_notified": True,
    }


def test_completed_campaign_keeps_checkpoint_and_runs_bounded_postgame_work():
    automator = object.__new__(EndGameAutomator)
    automator.client = MagicMock()
    automator.state = MagicMock()
    automator.state.custom_data = {}
    automator.state.get_current_phase.return_value = Phase.CITY_BUILD
    automator.state.get_overall_progress.return_value = 1.0
    automator.planner = MagicMock()
    automator.planner.is_complete.return_value = True
    automator.scheduler = MagicMock()
    automator.scheduler.next_step.return_value = _decision(
        local_work=True, summary="ADAPTIVE WORK: animal farm verified"
    )
    automator.postgame = PersistentPostgameLifecycle(
        automator.state, automator.scheduler
    )
    automator.resources = MagicMock()
    automator.systems = []
    automator.on_complete = MagicMock()
    automator._persist_objective_progress = MagicMock()
    automator._save_checkpoint = MagicMock()
    automator.stop = MagicMock()
    automator._handle_death_recovery = MagicMock(return_value=False)

    with patch(
        "baritone_client.automator.postgame_lifecycle.recover_survival_before_objective",
        return_value=True,
    ):
        assert automator.run(resume=False, max_postgame_iterations=1) is True

    automator.state.set_phase.assert_called_with(Phase.COMPLETE)
    automator.state.clear_checkpoint.assert_not_called()
    automator.on_complete.assert_called_once_with()
    automator.scheduler.next_step.assert_called_once_with(automator.planner)
    assert automator._save_checkpoint.call_count >= 2


def test_terminal_callback_is_not_repeated_when_postgame_resumes_from_checkpoint():
    state = SimpleNamespace(custom_data={})
    lifecycle = PersistentPostgameLifecycle(state, MagicMock())
    callback = MagicMock()

    lifecycle.enter()
    assert lifecycle.claim_completion_notification() is True
    resumed = PersistentPostgameLifecycle(state, MagicMock())
    resumed.enter()
    assert resumed.claim_completion_notification() is False

    callback.assert_not_called()


def _completed_automator():
    automator = object.__new__(EndGameAutomator)
    automator.client = MagicMock()
    automator.state = MagicMock()
    automator.state.custom_data = {}
    automator.state.get_current_phase.return_value = Phase.CITY_BUILD
    automator.state.get_overall_progress.return_value = 1.0
    automator.planner = MagicMock()
    automator.planner.is_complete.return_value = True
    automator.scheduler = MagicMock()
    automator.scheduler.next_step.return_value = _decision(
        local_work=True, summary="ADAPTIVE WORK: animal farm verified"
    )
    automator.postgame = PersistentPostgameLifecycle(automator.state, automator.scheduler, automator.client)
    automator.resources = MagicMock()
    automator.systems = []
    automator.on_complete = MagicMock()
    automator._persist_objective_progress = MagicMock()
    automator._save_checkpoint = MagicMock()
    automator.stop = MagicMock()
    automator._handle_death_recovery = MagicMock(return_value=False)
    return automator


def test_postgame_checks_survival_before_scheduling_work():
    automator = _completed_automator()
    with (
        patch(
            "baritone_client.automator.postgame_lifecycle.recover_survival_before_objective",
            side_effect=[False, True],
        ),
        patch(
            "baritone_client.automator.postgame_lifecycle.wait_with_bridge_keepalive"
        ) as wait,
    ):
        assert automator.run(resume=False, max_postgame_iterations=0) is True

    automator._handle_death_recovery.assert_called()
    automator.scheduler.next_step.assert_not_called()
    wait.assert_called_once_with(automator.client, duration=5.0)


def test_callback_failure_has_a_durable_notification_claim_before_reraising():
    automator = _completed_automator()
    saved_states = []

    def checkpoint():
        saved_states.append(dict(automator.state.custom_data["persistent_postgame"]))

    def callback():
        assert saved_states[-1]["completion_notified"] is True
        raise RuntimeError("callback failed")

    automator._save_checkpoint.side_effect = checkpoint
    automator.on_complete.side_effect = callback

    with (
        patch(
            "baritone_client.automator.postgame_lifecycle.recover_survival_before_objective",
            return_value=True,
        ),
        pytest.raises(RuntimeError, match="callback failed"),
    ):
        automator.run(resume=False, max_postgame_iterations=0)

    assert automator.state.custom_data["persistent_postgame"]["completion_notified"] is True
    assert saved_states
    automator.stop.assert_called_once_with()


def test_scheduler_empty_falls_back_to_exactly_one_verified_city_district(monkeypatch):
    state, scheduler, lifecycle = _growth_lifecycle()
    district = MagicMock(return_value=SimpleNamespace(success=True, reason="built"))
    monkeypatch.setattr(lifecycle_mod, "build_district", district)

    turn = lifecycle.run_turn(MagicMock())

    assert turn.did_work
    district.assert_called_once()
    assert district.call_args.kwargs["flatten"] is True
    assert state.custom_data["city_progress"] == {
        "ring": 1, "ring_progress": {"next_index": 1, "total": 8}, "districts_completed": 1,
    }


def test_city_growth_resumes_final_district_then_advances_to_next_unbounded_ring(monkeypatch):
    state, _, lifecycle = _growth_lifecycle(
        {"ring": 1, "ring_progress": {"next_index": 7, "total": 8}, "districts_completed": 7}
    )
    district = MagicMock(return_value=SimpleNamespace(success=True, reason="built"))
    monkeypatch.setattr(lifecycle_mod, "build_district", district)

    assert lifecycle.run_turn(MagicMock()).did_work
    assert state.custom_data["city_progress"] == {
        "ring": 2,
        "ring_progress": {},
        "districts_completed": 8,
        "rings_completed": 2,
    }
    assert lifecycle.run_turn(MagicMock()).did_work
    assert district.call_args_list[1].args[3:5] == tuple(next(lifecycle_mod.district_ring_offsets(2)))


def test_completed_ring_cursor_normalizes_and_builds_first_next_district(monkeypatch):
    state, _, lifecycle = _growth_lifecycle(
        {
            "ring": 1,
            "ring_progress": {"next_index": 8, "total": 8},
            "districts_completed": 8,
        }
    )
    district = MagicMock(return_value=SimpleNamespace(success=True, reason="built"))
    monkeypatch.setattr(lifecycle_mod, "build_district", district)

    turn = lifecycle.run_turn(MagicMock())

    assert turn.did_work
    assert district.call_args.args[3:5] == tuple(
        next(lifecycle_mod.district_ring_offsets(2))
    )
    city = state.custom_data["city_progress"]
    assert city["ring"] == 2
    assert city["ring_progress"]["next_index"] == 1


def test_failed_city_district_is_not_credited_and_records_no_progress(monkeypatch):
    state, _, lifecycle = _growth_lifecycle()
    state.custom_data["last_position"] = [999, 70, 999]
    monkeypatch.setattr(lifecycle_mod, "build_district", MagicMock(return_value=SimpleNamespace(success=False, reason="no blocks placed")))

    turn = lifecycle.run_turn(MagicMock())

    assert not turn.did_work and turn.state_changed
    assert state.custom_data["city_progress"] == {"ring_progress": {}}
    assert state.custom_data["productive_work"]["no_progress_streak"] == 1
    assert "last_position" not in lifecycle_mod.productive_snapshot(state)


def test_automator_checkpoints_failed_city_growth_ledger(monkeypatch):
    automator = _completed_automator()
    automator.scheduler.next_step.return_value = _decision(local_work=False, summary="idle")
    automator.state.get_phase_payload.return_value = {"center": [0, 0], "target_y": 64}
    monkeypatch.setattr(
        lifecycle_mod,
        "build_district",
        MagicMock(return_value=SimpleNamespace(success=False, reason="no output")),
    )
    with patch(
        "baritone_client.automator.postgame_lifecycle.wait_with_bridge_keepalive"
    ) as wait:
        automator.postgame.run_controller_turn(
            automator.planner,
            automator._persist_objective_progress,
            automator._save_checkpoint,
        )

    automator._save_checkpoint.assert_called_once_with()
    assert automator.state.custom_data["productive_work"]["no_progress_streak"] == 1
    wait.assert_called_once_with(automator.client, duration=30.0)
