"""Global fail-closed guards for the offline pytest suite."""

from __future__ import annotations

import sys
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest
import pytest_socket


_ORIGINAL_CONNECT = socket.socket.connect

# Restrict outbound connects while still allowing socket creation for local
# asyncio event-loop plumbing on Windows. Applying this while conftest imports
# also protects collection-time imports, before pytest markers are evaluated.
pytest_socket.socket_allow_hosts([])


def _guarded_socketpair(family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0):
    """Create Python's internal pair without opening a general loopback hole."""
    if family == socket.AF_INET:
        host = "127.0.0.1"
    elif family == socket.AF_INET6:
        host = "::1"
    else:
        raise ValueError("only AF_INET and AF_INET6 socket pairs are supported")
    if type != socket.SOCK_STREAM or proto != 0:
        raise ValueError("only SOCK_STREAM with protocol zero is supported")

    listener = socket.socket(family, type, proto)
    try:
        listener.bind((host, 0))
        listener.listen()
        address = listener.getsockname()
        client = socket.socket(family, type, proto)
        try:
            client.setblocking(False)
            try:
                _ORIGINAL_CONNECT(client, address)
            except (BlockingIOError, InterruptedError):
                pass
            client.setblocking(True)
            server, _peer = listener.accept()
        except Exception:
            client.close()
            raise
    finally:
        listener.close()
    return server, client


if sys.platform == "win32":
    socket.socketpair = _guarded_socketpair


@pytest.fixture(autouse=True)
def _block_outbound_socket_connects(request, pytestconfig):
    """Require both a live marker and explicit socket opt-in for connects."""
    live_marked = any(
        request.node.get_closest_marker(marker)
        for marker in ("live_readonly", "live_mutating")
    )
    if live_marked and pytestconfig.getoption("--force-enable-socket"):
        pytest_socket.enable_socket()
    else:
        pytest_socket.socket_allow_hosts([])
    yield

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))


class MockTransport:
    """Mock transport with configurable responses and call tracking."""

    def __init__(self, responses: Optional[Dict[str, Any]] = None):
        self.responses = responses or {}
        self.calls: List[tuple] = []
        self.default_responses = {
            "get_inventory": {
                "status": "ok",
                "data": {"inventory": [], "armor": [], "offhand": []},
            },
            "get_state": {
                "status": "ok",
                "health": 20.0,
                "food": 20,
                "saturation": 5.0,
                "position": {"x": 0.0, "y": 64.0, "z": 0.0},
                "block_position": {"x": 0, "y": 64, "z": 0},
                "is_pathing": False,
                "is_dead": False,
            },
            "get_view": {"voxels": []},
            "get_entities": {"entities": []},
            "craft": {"status": "ok", "crafted": True},
            "goto": {"status": "ok", "started": True},
            "cancel": {"status": "ok"},
        }

    def dispatch(self, route: str, payload: Dict) -> Dict:
        self.calls.append((route, payload))
        if route in self.responses:
            return self.responses[route]
        return self.default_responses.get(route, {"status": "ok"})

    def set_response(self, route: str, response: Any) -> None:
        """Configure a specific response."""
        self.responses[route] = response

    def add_inventory_item(self, item_id: str, count: int, slot: int = 0) -> None:
        """Add one item to the mock inventory."""
        inventory = self.default_responses["get_inventory"]["data"]["inventory"]
        inventory.append({"id": item_id, "count": count, "slot": slot})


class MockWorldState:
    """Mock world state for testing."""

    def __init__(self):
        self.health = 20.0
        self.food = 20
        self.saturation = 5.0
        self.position = {"x": 0.0, "y": 64.0, "z": 0.0}
        self._phase = None

    def refresh(self):
        return self

    def get_current_phase(self):
        return self._phase

    def set_phase(self, phase):
        self._phase = phase


class MockClient:
    """Mock client for action testing."""

    def __init__(self, transport: Optional[MockTransport] = None):
        self.transport = transport or MockTransport()
        self.command = MagicMock()
        self.mission = MagicMock()


@dataclass
class MockActionContext:
    """Mock ActionContext for isolated testing."""

    client: MockClient = field(default_factory=MockClient)
    state: MockWorldState = field(default_factory=MockWorldState)
    resources: Any = None
    coordination: Any = None


@pytest.fixture
def mock_transport():
    """Create a fresh MockTransport for each test."""
    return MockTransport()


@pytest.fixture
def mock_client(mock_transport):
    """Create a fresh MockClient for each test."""
    return MockClient(mock_transport)


@pytest.fixture
def mock_context(mock_client):
    """Create a fresh MockActionContext for each test."""
    return MockActionContext(client=mock_client, state=MockWorldState())
