from types import SimpleNamespace

from baritone_client.automator.pacing import wait_with_bridge_keepalive


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
