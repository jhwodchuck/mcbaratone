"""Typed timeout behavior for supervised navigation."""

from types import SimpleNamespace

import pytest

from baritone_client.common import navigation
from baritone_client.core.exceptions import (
    BridgeResponseTimeout,
    CommandError,
    RouteError,
    TransportError,
)


def _state(x=0.0, *, health=20.0):
    return {
        "health": health,
        "food_level": 20,
        "is_pathing": True,
        "block_position": {"x": x, "y": 64.0, "z": 0.0},
    }


def _timeout(route, *, sent=True):
    return BridgeResponseTimeout(
        route, transport_type="tcp", request_sent=sent
    )


def _prepare(monkeypatch):
    monkeypatch.setattr(navigation.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "baritone_client.common.combat.survival_tick", lambda *_a, **_k: False
    )


def test_confirmed_goto_timeout_can_finish_from_verified_movement(monkeypatch):
    _prepare(monkeypatch)
    outcomes = iter([_state(), _timeout("goto"), _state(10.0)])

    class Transport:
        calls = []

        def dispatch(self, route, payload):
            self.calls.append(route)
            if route == "cancel":
                return {}
            return next(outcomes)

    client = SimpleNamespace(transport=Transport())
    assert navigation.goto(
        client, 10, 64, 0, tolerance=1.0, on_defense=lambda: False
    )
    assert client.transport.calls.count("cancel") == 1


@pytest.mark.parametrize(
    "error",
    [
        CommandError("rejected"),
        RouteError("goto", "tcp"),
        ValueError("callback bug"),
        TransportError("Timeout waiting for bridge response (route: goto)"),
        _timeout("chat"),
        _timeout("goto", sent=False),
    ],
)
def test_goto_does_not_swallow_nonmatching_dispatch_failures(monkeypatch, error):
    _prepare(monkeypatch)

    class Transport:
        reads = 0

        def dispatch(self, route, _payload):
            if route == "get_state":
                self.reads += 1
                return _state()
            if route == "goto":
                raise error
            return {}

    with pytest.raises(type(error)):
        navigation.goto(SimpleNamespace(transport=Transport()), 10, 64, 0)


def test_two_state_misses_then_success_and_reset_are_bounded(monkeypatch):
    _prepare(monkeypatch)
    states = iter(
        [
            _state(),
            _timeout("get_state"),
            _timeout("get_state"),
            _state(2.0),
            _timeout("get_state"),
            _timeout("get_state"),
            _state(10.0),
        ]
    )

    class Transport:
        cancels = 0

        def dispatch(self, route, _payload):
            if route == "get_state":
                outcome = next(states)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
            if route == "cancel":
                self.cancels += 1
            return {}

    transport = Transport()
    assert navigation.goto(
        SimpleNamespace(transport=transport),
        10,
        64,
        0,
        on_defense=lambda: False,
    )
    assert transport.cancels == 1


def test_third_state_miss_cancels_once_and_raises(monkeypatch):
    _prepare(monkeypatch)
    reads = iter([_state(), _timeout("get_state"), _timeout("get_state"), _timeout("get_state")])

    class Transport:
        cancels = 0

        def dispatch(self, route, _payload):
            if route == "get_state":
                outcome = next(reads)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
            if route == "cancel":
                self.cancels += 1
            return {}

    transport = Transport()
    with pytest.raises(BridgeResponseTimeout):
        navigation.goto(
            SimpleNamespace(transport=transport),
            10,
            64,
            0,
            on_defense=lambda: False,
        )
    assert transport.cancels == 1


def test_wrong_route_state_timeout_cancels_and_propagates(monkeypatch):
    _prepare(monkeypatch)
    reads = iter([_state(), _timeout("get_block")])

    class Transport:
        cancels = 0

        def dispatch(self, route, _payload):
            if route == "get_state":
                outcome = next(reads)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
            if route == "cancel":
                self.cancels += 1
            return {}

    transport = Transport()
    with pytest.raises(BridgeResponseTimeout):
        navigation.goto_xz(
            SimpleNamespace(transport=transport),
            10,
            0,
            on_defense=lambda: False,
        )
    assert transport.cancels == 1


def test_goto_xz_tolerates_two_state_misses_then_arrives(monkeypatch):
    _prepare(monkeypatch)
    states = iter([_state(), _timeout("get_state"), _timeout("get_state"), _state(10)])

    class Transport:
        def dispatch(self, route, _payload):
            if route == "get_state":
                outcome = next(states)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
            return {}

    assert navigation.goto_xz(
        SimpleNamespace(transport=Transport()),
        10,
        0,
        tolerance=1,
        on_defense=lambda: False,
    )


def test_callback_failure_cancels_and_propagates(monkeypatch):
    _prepare(monkeypatch)

    class Transport:
        cancels = 0

        def dispatch(self, route, _payload):
            if route == "get_state":
                return _state()
            if route == "cancel":
                self.cancels += 1
            return {}

    transport = Transport()

    def broken_callback():
        raise ValueError("callback bug")

    with pytest.raises(ValueError, match="callback bug"):
        navigation.goto(
            SimpleNamespace(transport=transport),
            10,
            64,
            0,
            on_tick=broken_callback,
            on_defense=lambda: False,
        )
    assert transport.cancels == 1


def test_arrival_is_observed_before_cleanup_cancel(monkeypatch):
    """A cleanup cancel must not turn an accepted arrival into cancellation."""
    _prepare(monkeypatch)
    states = iter([_state(), _state(10.0), {**_state(10.0), "is_pathing": False}])

    class Transport:
        def __init__(self):
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append(route)
            if route == "get_state":
                return next(states)
            return {}

    client = SimpleNamespace(transport=Transport())
    assert navigation.goto(
        client, 10, 64, 0, tolerance=0.5, on_defense=lambda: False
    )
    assert client._last_navigation_outcome == "arrived"
    assert client._last_navigation_evidence["observed_arrival"] is True
    assert client._last_navigation_cancel["cancel_status"] == "stopped"
    assert client.transport.calls.count("goto") == 1
    assert client.transport.calls.count("cancel") == 1


def test_stalled_navigation_uses_elapsed_progress_budget(monkeypatch):
    """The no movement watchdog is 30 seconds, independent of sample cadence."""
    _prepare(monkeypatch)
    elapsed = [0.0]
    monkeypatch.setattr(navigation.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(
        navigation.time,
        "sleep",
        lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds),
    )

    class Transport:
        def __init__(self):
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append(route)
            if route == "get_state":
                return _state()
            return {}

    client = SimpleNamespace(transport=Transport())
    assert not navigation.goto(
        client,
        100,
        64,
        0,
        timeout=100,
        check_interval=2,
        on_defense=lambda: False,
    )
    assert elapsed[0] >= 30.0
    assert elapsed[0] < 100.0
    assert client._last_navigation_outcome == "unknown"
    assert client._last_navigation_evidence["requested_outcome"] == "stalled"


def test_cancel_with_pathing_still_true_is_explicitly_unknown(monkeypatch):
    _prepare(monkeypatch)

    class Transport:
        def __init__(self):
            self.states = iter([{**_state(), "is_pathing": False}, _state()])

        def dispatch(self, route, payload):
            if route == "get_state":
                return next(self.states)
            return {}

    client = SimpleNamespace(transport=Transport())
    assert not navigation.goto(
        client, 100, 64, 0, timeout=0, on_defense=lambda: False
    )
    assert client._last_navigation_outcome == "unknown"
    assert client._last_navigation_evidence["cancel_status"] == "unknown"


def test_cancel_status_uses_post_cancel_state_not_pre_cancel_state(monkeypatch):
    _prepare(monkeypatch)

    class Transport:
        def __init__(self):
            self.states = iter([_state(), {**_state(), "is_pathing": False}])

        def dispatch(self, route, payload):
            if route == "get_state":
                return next(self.states)
            return {}

    client = SimpleNamespace(transport=Transport())
    assert not navigation.goto(
        client, 100, 64, 0, timeout=0, on_defense=lambda: False
    )
    assert client._last_navigation_cancel["cancel_status"] == "stopped"


def test_malformed_initial_state_does_not_start_a_goal(monkeypatch):
    _prepare(monkeypatch)

    class Transport:
        def __init__(self):
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append(route)
            if route == "get_state":
                return {"health": 20, "block_position": {"x": 0}}
            return {}

    transport = Transport()
    assert not navigation.goto(
        SimpleNamespace(transport=transport), 10, 64, 0
    )
    assert "goto" not in transport.calls
    assert "cancel" not in transport.calls
