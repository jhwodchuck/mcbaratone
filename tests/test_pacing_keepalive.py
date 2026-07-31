from types import SimpleNamespace

from baritone_client.automator import phase_executor as phase_executor_module
from baritone_client.automator.phase_executor import PhaseExecutor, PhaseHandler
from baritone_client.automator.pacing import wait_with_bridge_keepalive
from baritone_client.automator.state_manager import Phase
from baritone_client.common.tasks import TaskResult


def test_pacing_wait_runs_defense_before_first_sleep_and_every_two_seconds():
    clock = SimpleNamespace(now=0.0, sleeps=[])
    probes = []

    def sleep(seconds):
        clock.sleeps.append(seconds)
        clock.now += seconds

    client = SimpleNamespace(transport=SimpleNamespace())

    assert wait_with_bridge_keepalive(
        client,
        duration=6.0,
        keepalive_interval=10.0,
        safety_interval=2.0,
        safety_check=lambda active_client: probes.append(
            (clock.now, active_client)
        ),
        sleep=sleep,
        monotonic=lambda: clock.now,
    )
    assert clock.sleeps == [2.0, 2.0, 2.0]
    assert probes == [
        (0.0, client),
        (2.0, client),
        (4.0, client),
    ]


def test_pacing_wait_propagates_death_instead_of_hiding_it():
    from baritone_client.common.tasks import PlayerDeathDetected

    def dead(_client):
        raise PlayerDeathDetected("zombie reached idle bot")

    client = SimpleNamespace(transport=SimpleNamespace())

    try:
        wait_with_bridge_keepalive(
            client,
            duration=30.0,
            safety_check=dead,
        )
    except PlayerDeathDetected as exc:
        assert "zombie" in str(exc)
    else:
        raise AssertionError("death must reach top-level recovery")


def test_failed_phase_retry_uses_combat_aware_wait(monkeypatch):
    class FailingHandler(PhaseHandler):
        def execute(self, _client, _resources, _state):
            return TaskResult.fail("hostile interrupted work")

        def get_name(self):
            return "Threatened work"

        def on_exit(self, _client, _resources, _state):
            pass

    client = SimpleNamespace()
    state = SimpleNamespace(
        update_progress=lambda *_args, **_kwargs: None,
        record_phase_payload=lambda *_args, **_kwargs: None,
    )
    resources = SimpleNamespace(refresh_inventory=lambda: None)
    waits = []
    blind_sleeps = []
    monkeypatch.setattr(
        phase_executor_module,
        "wait_with_bridge_keepalive",
        lambda active_client, *, duration: waits.append(
            (active_client, duration)
        )
        or True,
        raising=False,
    )
    monkeypatch.setattr(
        phase_executor_module.time,
        "sleep",
        lambda duration: blind_sleeps.append(duration),
    )
    executor = PhaseExecutor(
        client,
        resources,
        state,
        max_retries=1,
        retry_delay=5,
        screenshot_enabled=False,
    )
    executor.register_handler(Phase.BOOT_SEQUENCE, FailingHandler())

    assert not executor.execute_phase(Phase.BOOT_SEQUENCE)
    assert waits == [(client, 5)]
    assert blind_sleeps == []
