from __future__ import annotations

import pytest

from baritone_client.exceptions import TransportError
from baritone_client.mcp_server import BridgeConfig, BridgeSession


class DummyClient:
    def __init__(self) -> None:
        self.shutdown_calls = 0

    def shutdown(self) -> None:
        self.shutdown_calls += 1


def test_bridge_session_reuses_single_client_instance() -> None:
    created: list[DummyClient] = []

    def factory() -> DummyClient:
        client = DummyClient()
        created.append(client)
        return client

    session = BridgeSession(BridgeConfig(), client_factory=factory)
    seen = []

    def record(client: DummyClient) -> str:
        seen.append(client)
        return "ok"

    assert session.run(record) == "ok"
    assert session.run(record) == "ok"
    assert len(created) == 1
    assert seen[0] is seen[1]

    session.close()
    assert created[0].shutdown_calls == 1


def test_bridge_session_reconnects_after_transport_error() -> None:
    created: list[DummyClient] = []

    def factory() -> DummyClient:
        client = DummyClient()
        created.append(client)
        return client

    session = BridgeSession(BridgeConfig(), client_factory=factory)

    def fail_once(client: DummyClient) -> None:
        raise TransportError("boom")

    with pytest.raises(TransportError):
        session.run(fail_once)

    assert len(created) == 1
    assert created[0].shutdown_calls == 1

    def succeed(client: DummyClient) -> str:
        return "ok"

    assert session.run(succeed) == "ok"
    assert len(created) == 2
    assert created[1].shutdown_calls == 0

    session.close()
    assert created[1].shutdown_calls == 1


def test_bridge_session_close_is_idempotent() -> None:
    session = BridgeSession(BridgeConfig(), client_factory=DummyClient)
    # No client created yet; close should be a no-op
    session.close()

    def run_once(client: DummyClient) -> str:
        return "done"

    assert session.run(run_once) == "done"
    session.close()
    # Second close should not raise or double shutdown
    session.close()
