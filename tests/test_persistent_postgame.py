from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from baritone_client.automator.automator import EndGameAutomator
from baritone_client.automator.postgame_lifecycle import PersistentPostgameLifecycle
from baritone_client.automator.state_manager import Phase


def _decision(*, local_work: bool, summary: str):
    return SimpleNamespace(local_work=local_work, summary=summary)


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
        "baritone_client.automator.automator.recover_survival_before_objective",
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
    automator.postgame = PersistentPostgameLifecycle(automator.state, automator.scheduler)
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
            "baritone_client.automator.automator.recover_survival_before_objective",
            side_effect=[False, True],
        ),
        patch("baritone_client.automator.automator.wait_with_bridge_keepalive") as wait,
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
            "baritone_client.automator.automator.recover_survival_before_objective",
            return_value=True,
        ),
        pytest.raises(RuntimeError, match="callback failed"),
    ):
        automator.run(resume=False, max_postgame_iterations=0)

    assert automator.state.custom_data["persistent_postgame"]["completion_notified"] is True
    assert saved_states
    automator.stop.assert_called_once_with()
