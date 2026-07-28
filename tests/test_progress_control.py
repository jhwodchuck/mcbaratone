from types import SimpleNamespace

from baritone_client.automator.progress_control import progression_fingerprint


def _state(*, position=(0, 64, 0), recovery_attempts=0, milestones=None):
    return SimpleNamespace(
        position=position,
        inventory_observations={
            "minecraft:cobblestone": 32,
            "minecraft:dirt": 3,
        },
        phase_progress={"BASE_CONSTRUCTION": 0.5},
        custom_data={
            "base_survival_recovery_failures": recovery_attempts,
            "milestones": milestones or {},
        },
    )


def test_fingerprint_ignores_movement_and_recovery_counter_churn():
    before = progression_fingerprint(
        _state(position=(0, 64, 0), recovery_attempts=1)
    )
    after = progression_fingerprint(
        _state(position=(400, 62, -300), recovery_attempts=9)
    )

    assert before == after


def test_fingerprint_changes_for_durable_milestone():
    before = progression_fingerprint(_state())
    after = progression_fingerprint(
        _state(milestones={"renewable_food": True})
    )

    assert before != after
