"""Read-only routes retry on bridge timeouts; mutating routes never do.

One transient bridge stall (server lag, chunk generation) used to raise
straight into phase logic and burn a whole phase retry: FOOD_AND_IRON failed
live on single get_state / get_block timeouts. Re-sending a query is safe;
re-sending goto/mine/place/craft is not.
"""

from types import SimpleNamespace

import pytest

from baritone_client.core.exceptions import CommandError, TransportError
from baritone_client.transport import transport as transport_module
from baritone_client.transport.transport import TcpTransport


class _DummySocket:
    def sendall(self, _data):
        pass

    def close(self):
        pass

    def recv(self, _size):  # keep the reader thread quietly parked
        import time

        time.sleep(0.05)
        return b""

    def settimeout(self, _t):
        pass


@pytest.fixture
def tcp(monkeypatch):
    monkeypatch.setattr(transport_module, "connect", lambda *_a, **_k: _DummySocket())
    t = TcpTransport(host="test", port=1, timeout=0.2)
    monkeypatch.setattr(transport_module.time, "sleep", lambda _s: None)
    yield t
    t._shutdown_event.set()


def _script_dispatch_once(monkeypatch, t, outcomes):
    """Replace _dispatch_once with a scripted sequence of results/raises."""
    calls = []

    def fake_once(route, payload, timeout=None):
        calls.append(route)
        outcome = outcomes[min(len(calls), len(outcomes)) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(t, "_dispatch_once", fake_once)
    return calls


def test_read_route_retries_past_transient_timeout(monkeypatch, tcp):
    calls = _script_dispatch_once(
        monkeypatch,
        tcp,
        [TransportError("Timeout waiting for bridge response (route: get_state)"), {"health": 20.0}],
    )

    assert tcp.dispatch("get_state", {}) == {"health": 20.0}
    assert calls == ["get_state", "get_state"]


def test_read_route_raises_after_exhausting_attempts(monkeypatch, tcp):
    boom = TransportError("Timeout waiting for bridge response (route: get_block)")
    calls = _script_dispatch_once(monkeypatch, tcp, [boom])

    with pytest.raises(TransportError):
        tcp.dispatch("get_block", {"x": 0, "y": 64, "z": 0})
    assert calls == ["get_block"] * TcpTransport._READ_RETRY_ATTEMPTS


def test_mutating_route_never_retries(monkeypatch, tcp):
    boom = TransportError("Timeout waiting for bridge response (route: goto)")
    calls = _script_dispatch_once(monkeypatch, tcp, [boom])

    with pytest.raises(TransportError):
        tcp.dispatch("goto", {"x": 1, "y": 64, "z": 1})
    assert calls == ["goto"]


def test_player_not_available_error_is_retried_on_read_route(monkeypatch, tcp):
    calls = _script_dispatch_once(
        monkeypatch,
        tcp,
        [CommandError("Player not available"), {"health": 20.0}],
    )

    assert tcp.dispatch("get_state", {}) == {"health": 20.0}
    assert calls == ["get_state", "get_state"]


def test_bridge_command_errors_are_not_retried(monkeypatch, tcp):
    # Non-transient command failures should fail fast.
    calls = _script_dispatch_once(
        monkeypatch, tcp, [CommandError("Invalid recipe requested")]
    )

    with pytest.raises(CommandError):
        tcp.dispatch("get_state", {})
    assert calls == ["get_state"]


def test_best_effort_close_screen_timeout_is_a_noop(tcp):
    """A laggy close_screen must not raise from the low-level transport.

    This is intentionally exercised through ``_dispatch_once`` rather than a
    mock: the timeout handler is where the best-effort contract lives.
    """
    tcp.timeout = 0.01

    assert tcp.dispatch("close_screen", {}) == {}
