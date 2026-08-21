"""Read-only routes retry on bridge timeouts; mutating routes never do.

One transient bridge stall (server lag, chunk generation) used to raise
straight into phase logic and burn a whole phase retry: FOOD_AND_IRON failed
live on single get_state / get_block timeouts. Re-sending a query is safe;
re-sending goto/mine/place/craft is not.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from baritone_client.core.exceptions import (
    BridgeResponseTimeout,
    CommandError,
    TransportError,
)
from baritone_client.transport import transport as transport_module
from baritone_client.transport.transport import TcpTransport, WebSocketTransport
from baritone_client.transport.tcp_protocol import is_traced_command


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


class _DummyFuture:
    def result(self, timeout=None):
        return None


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
        [
            BridgeResponseTimeout(
                "get_state", transport_type="tcp", request_sent=True
            ),
            {"health": 20.0},
        ],
    )

    assert tcp.dispatch("get_state", {}) == {"health": 20.0}
    assert calls == ["get_state", "get_state"]


def test_read_route_raises_after_exhausting_attempts(monkeypatch, tcp):
    boom = BridgeResponseTimeout(
        "get_block", transport_type="tcp", request_sent=True
    )
    calls = _script_dispatch_once(monkeypatch, tcp, [boom])

    with pytest.raises(TransportError):
        tcp.dispatch("get_block", {"x": 0, "y": 64, "z": 0})
    assert calls == ["get_block"] * TcpTransport._READ_RETRY_ATTEMPTS


def test_mutating_route_never_retries(monkeypatch, tcp):
    boom = BridgeResponseTimeout("goto", transport_type="tcp", request_sent=True)
    calls = _script_dispatch_once(monkeypatch, tcp, [boom])

    with pytest.raises(TransportError):
        tcp.dispatch("goto", {"x": 1, "y": 64, "z": 1})
    assert calls == ["goto"]


def test_place_recipe_emits_mutation_lifecycle_telemetry():
    assert is_traced_command("place_recipe") is True


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


def test_tcp_timeout_records_logical_route_and_confirmed_send(tcp):
    tcp.timeout = 0

    with pytest.raises(BridgeResponseTimeout) as raised:
        tcp._dispatch_once("command/run", {"command": "#help"})

    assert raised.value.route == "command/run"
    assert raised.value.transport_type == "tcp"
    assert raised.value.request_sent is True


def test_tcp_send_failure_is_not_a_response_timeout(tcp):
    class BrokenSocket(_DummySocket):
        def sendall(self, _data):
            raise OSError("broken pipe")

    tcp._socket = BrokenSocket()

    with pytest.raises(TransportError) as raised:
        tcp._dispatch_once("goto", {"x": 1, "y": 64, "z": 1})

    assert not isinstance(raised.value, BridgeResponseTimeout)
    assert isinstance(raised.value.original_error, OSError)


def test_tcp_timeout_preserves_socket_needed_by_peer_request(monkeypatch, tcp):
    """One slow read must not strand another request on the shared socket."""
    reconnects = []
    tcp.timeout = 0
    tcp._response_queues["still-waiting"] = object()
    monkeypatch.setattr(
        tcp,
        "_reconnect_after_failure",
        lambda *_args: reconnects.append(True),
    )

    with pytest.raises(BridgeResponseTimeout):
        tcp._dispatch_once("get_block", {"x": 0, "y": 64, "z": 0})

    assert reconnects == []


def test_tcp_sole_timed_out_request_may_reconnect(monkeypatch, tcp):
    reconnects = []
    tcp.timeout = 0
    monkeypatch.setattr(
        tcp,
        "_reconnect_after_failure",
        lambda *_args: reconnects.append(True),
    )

    with pytest.raises(BridgeResponseTimeout):
        tcp._dispatch_once("get_block", {"x": 0, "y": 64, "z": 0})

    assert reconnects == [True]


def test_websocket_timeout_records_logical_route_and_confirmed_send(monkeypatch):
    with patch.object(WebSocketTransport, "_connect", lambda self: None):
        transport = WebSocketTransport(
            "ws://test", timeout=0, enable_event_storage=False
        )
    sent = []
    transport._loop = object()
    transport._send_message = lambda message: sent.append(message)
    monkeypatch.setattr(
        transport_module.asyncio,
        "run_coroutine_threadsafe",
        lambda _coro, _loop: _DummyFuture(),
    )

    with pytest.raises(BridgeResponseTimeout) as raised:
        transport.dispatch("goal/apply", {})

    assert raised.value.route == "goal/apply"
    assert raised.value.transport_type == "websocket"
    assert raised.value.request_sent is True
    assert sent[0]["method"] == "goal.apply"
    assert not transport._response_queues


def test_websocket_rpc_error_remains_command_error(monkeypatch):
    with patch.object(WebSocketTransport, "_connect", lambda self: None):
        transport = WebSocketTransport(
            "ws://test", timeout=0, enable_event_storage=False
        )
    transport._loop = object()

    def send_with_error(message):
        transport._response_queues[message["id"]].put(
            {"id": message["id"], "error": {"message": "rejected"}}
        )

    transport._send_message = send_with_error
    monkeypatch.setattr(
        transport_module.asyncio,
        "run_coroutine_threadsafe",
        lambda _coro, _loop: _DummyFuture(),
    )

    with pytest.raises(CommandError, match="rejected"):
        transport.dispatch("goto", {})
