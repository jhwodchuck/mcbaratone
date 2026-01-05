import sys
from pathlib import Path
from unittest.mock import MagicMock
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ============================================================================
# Mock Classes for Action Testing
# ============================================================================

class MockTransport:
    """Mock transport with configurable responses and call tracking."""
    
    def __init__(self, responses: Optional[Dict[str, Any]] = None):
        self.responses = responses or {}
        self.calls: List[tuple] = []
        self.default_responses = {
            "get_inventory": {
                "status": "ok",
                "data": {
                    "inventory": [],
                    "armor": [],
                    "offhand": [],
                }
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
        """Helper to add items to mock inventory."""
        inv = self.default_responses["get_inventory"]["data"]["inventory"]
        inv.append({"id": item_id, "count": count, "slot": slot})


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


# ============================================================================
# Pytest Fixtures
# ============================================================================

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
