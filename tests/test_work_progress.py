from types import SimpleNamespace

from baritone_client.automator.work_progress import (
    productive_snapshot,
    record_productive_attempt,
)


def _state(custom_data=None):
    return SimpleNamespace(custom_data=custom_data or {})


def test_snapshot_ignores_movement_and_attempt_cursors():
    state = _state(
        {
            "food_worker": {
                "attempts": 9,
                "plot_cursor": 4,
                "plots": 2,
                "prepared_food_banked": 18,
            },
            "last_position": [100, 70, 200],
        }
    )

    assert productive_snapshot(state) == {
        "food_worker.plots": 2,
        "food_worker.prepared_food_banked": 18,
    }


def test_unchanged_attempts_raise_durable_escalation_level():
    state = _state()
    before = {"wood_worker.logs_banked": 8}

    results = [
        record_productive_attempt(
            state,
            "wood_farm",
            before,
            before,
            now=float(index),
        )
        for index in range(1, 6)
    ]

    assert [result.escalation_level for result in results] == [1, 1, 2, 2, 3]
    assert state.custom_data["productive_work"]["no_progress_streak"] == 5


def test_verified_counter_delta_resets_no_progress_streak():
    state = _state()
    before = {"iron_worker.iron_banked": 10}
    record_productive_attempt(state, "iron_mine", before, before, now=1.0)

    result = record_productive_attempt(
        state,
        "iron_mine",
        before,
        {"iron_worker.iron_banked": 17},
        detail="banked seven iron",
        now=2.0,
    )

    assert result.progressed
    assert result.delta == {"iron_worker.iron_banked": 7}
    assert result.no_progress_streak == 0
    assert state.custom_data["productive_work"]["last_progress_at"] == 2.0


def test_success_text_without_counter_delta_is_not_productive_progress():
    state = _state()

    result = record_productive_attempt(
        state,
        "nether_supply",
        {},
        {},
        detail="command started successfully",
        now=4.0,
    )

    assert not result.progressed
    assert result.no_progress_streak == 1
