from types import SimpleNamespace

from baritone_client.common.mining_acceleration import (
    accelerated_mining,
    selected_fast_break_mode,
)
from baritone_client.core.exceptions import CommandError
from baritone_client.world_identity import WorldIdentity


def test_local_server_defaults_to_aggressive(monkeypatch):
    monkeypatch.delenv("MC_FAST_BREAK_MODE", raising=False)
    state = SimpleNamespace(
        bound_world_identity=WorldIdentity(server_address="127.0.0.1:25565")
    )

    assert selected_fast_break_mode(state) == "aggressive"


def test_remote_server_defaults_to_legit(monkeypatch):
    monkeypatch.delenv("MC_FAST_BREAK_MODE", raising=False)
    state = SimpleNamespace(
        bound_world_identity=WorldIdentity(server_address="example.org:25565")
    )

    assert selected_fast_break_mode(state) == "legit"


def test_transport_host_is_used_when_checkpoint_identity_is_missing(monkeypatch):
    monkeypatch.delenv("MC_FAST_BREAK_MODE", raising=False)
    client = SimpleNamespace(
        transport=SimpleNamespace(host="localhost", port=5695)
    )

    assert selected_fast_break_mode(SimpleNamespace(), client) == "aggressive"


def test_invalid_operator_override_fails_closed(monkeypatch):
    monkeypatch.setenv("MC_FAST_BREAK_MODE", "warp-speed")

    assert selected_fast_break_mode(SimpleNamespace()) == "off"


def test_context_enables_and_disables_selected_mode(monkeypatch):
    monkeypatch.setenv("MC_FAST_BREAK_MODE", "aggressive")
    calls = []
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, payload: calls.append((route, payload))
        )
    )

    with accelerated_mining(client, SimpleNamespace()) as applied:
        assert applied == "aggressive"

    assert calls == [
        ("set_fast_break", {"mode": "aggressive"}),
        ("set_fast_break", {"mode": "off"}),
    ]


def test_older_bridge_falls_back_without_cleanup(monkeypatch):
    monkeypatch.setenv("MC_FAST_BREAK_MODE", "legit")
    calls = []

    def dispatch(route, payload):
        calls.append((route, payload))
        raise CommandError("unknown command")

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))

    with accelerated_mining(client, SimpleNamespace()) as applied:
        assert applied == "unavailable"

    assert calls == [("set_fast_break", {"mode": "legit"})]
