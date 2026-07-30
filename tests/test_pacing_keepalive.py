from types import SimpleNamespace

from baritone_client.automator.pacing import wait_with_bridge_keepalive


def test_pacing_wait_preserves_quiet_interval_with_read_only_keepalives():
    clock = SimpleNamespace(now=0.0, sleeps=[])
    probes = []

    def sleep(seconds):
        clock.sleeps.append(seconds)
        clock.now += seconds

    def dispatch(route, payload):
        probes.append((clock.now, route, payload))
        return {"health": 20.0}

    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=dispatch),
    )

    assert wait_with_bridge_keepalive(
        client,
        duration=30.0,
        keepalive_interval=10.0,
        sleep=sleep,
        monotonic=lambda: clock.now,
    )
    assert clock.sleeps == [10.0, 10.0, 10.0]
    assert probes == [
        (10.0, "get_state", {}),
        (20.0, "get_state", {}),
        (30.0, "get_state", {}),
    ]
