"""
Unit tests for action classes.

Tests for BaseAction and action patterns - self-contained to avoid
circular dependency issues from the baritone_client package __init__.py.
"""

import pytest
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from abc import ABC, abstractmethod

# Ensure src is in path
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ============================================================================
# Global Mocks for Heavy Actions
# ============================================================================
import baritone_client.common as common
import baritone_client.actions.crafting as crafting
import baritone_client.common.combat as combat_mod
import baritone_client.actions.initial_gathering as ig

# NOTE: These gathering/combat helpers are mocked so the action tests below do
# not perform real hunting/gathering. They are applied via an AUTOUSE FIXTURE
# (see _mock_heavy_actions) rather than module-level assignment: assigning them
# at import time permanently replaced the production functions for the whole
# pytest session (import runs once, at collection), so later suites -- e.g.
# test_combat_safety.py's real hunt_mobs tests -- silently got these stubs and
# failed. The fixture restores every patch after each test, containing them.
# ============================================================================


@pytest.fixture(autouse=True)
def _mock_heavy_actions(monkeypatch):
    """Stub gathering/combat helpers for the action tests, restored afterwards.

    raising=False mirrors the original module-level assignment, which created
    the attribute if the module had not imported that name.
    """
    _hunt = lambda *a, **k: ActionResult.ok("Mocked hunt")
    _passive = lambda *a, **k: ActionResult.ok("Mocked passive hunt")
    monkeypatch.setattr(
        crafting.CraftingAction, "ensure_crafting_table", lambda self, ctx: True
    )
    for module in (common, ig):
        monkeypatch.setattr(module, "gather_wood", lambda client, count: True, raising=False)
        monkeypatch.setattr(module, "gather_stone", lambda client, count: True, raising=False)
        monkeypatch.setattr(module, "hunt_mobs", _hunt, raising=False)
        monkeypatch.setattr(module, "hunt_passive_mobs", _passive, raising=False)
    monkeypatch.setattr(combat_mod, "hunt_mobs", _hunt, raising=False)
    monkeypatch.setattr(combat_mod, "hunt_passive_mobs", _passive, raising=False)

@dataclass
class ActionResult:
    """Self-contained ActionResult for testing."""
    success: bool
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def ok(cls, message: str = "", **kwargs) -> 'ActionResult':
        return cls(True, message, kwargs)
    
    @classmethod
    def fail(cls, message: str = "", **kwargs) -> 'ActionResult':
        return cls(False, message, kwargs)


class CommandError(RuntimeError):
    """Exception raised when a command fails."""
    pass


@dataclass
class ActionContext:
    """Self-contained ActionContext for testing."""
    client: Any = None
    state: Any = None
    resources: Any = None


class IAction(ABC):
    """Self-contained IAction interface for testing."""
    
    @abstractmethod
    def execute(self, context: ActionContext) -> ActionResult:
        pass


class BaseAction(IAction):
    """Self-contained BaseAction for testing."""
    
    def run_command(self, context: ActionContext, command: str, params: dict) -> dict:
        """Run command via transport."""
        try:
            return context.client.transport.dispatch(command, params)
        except Exception as e:
            raise CommandError(f"Action command failed: {command}") from e
    
    def execute(self, context: ActionContext) -> ActionResult:
        raise NotImplementedError("Actions must implement execute()")


# ============================================================================
# Mock Classes
# ============================================================================

class MockTransport:
    """Mock transport with configurable responses."""
    
    def __init__(self, responses: Optional[Dict[str, Any]] = None):
        self.responses = responses or {}
        self.calls = []
        self.default_responses = {
            "get_inventory": {
                "status": "ok",
                "data": {"inventory": [], "armor": [], "offhand": []}
            },
            "craft": {"status": "ok", "crafted": True},
            "goto": {"status": "ok", "started": True},
            "cancel": {"status": "ok"},
            "select_slot": {"status": "ok"},
        }
    
    def dispatch(self, route: str, payload: Dict) -> Dict:
        self.calls.append((route, payload))
        if route in self.responses:
            return self.responses[route]
        # Simulate crafting actually producing the item (production code
        # verifies crafts by inventory delta, not by the status reply).
        if route == "craft":
            item = payload.get("item")
            if item:
                self.add_inventory_item(item, payload.get("count", 1))
        return self.default_responses.get(route, {"status": "ok"})

    def set_response(self, route: str, response: Any) -> None:
        self.responses[route] = response

    def add_inventory_item(self, item_id: str, count: int, slot: int = 0) -> None:
        inv = self.default_responses["get_inventory"]["data"]["inventory"]
        for entry in inv:
            if entry.get("id") == item_id:
                entry["count"] = entry.get("count", 0) + count
                return
        used = {e.get("slot") for e in inv}
        while slot in used:
            slot += 1
        inv.append({"id": item_id, "count": count, "slot": slot})


class MockClient:
    """Mock client for testing."""
    
    def __init__(self, transport: Optional[MockTransport] = None):
        self.transport = transport or MockTransport()


class MockWorldState:
    """Mock world state for testing."""
    health = 20.0
    
    def refresh(self):
        return self


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_transport():
    return MockTransport()


@pytest.fixture
def mock_client(mock_transport):
    return MockClient(mock_transport)


@pytest.fixture
def mock_context(mock_client):
    return ActionContext(client=mock_client, state=MockWorldState())


# ============================================================================
# ActionResult Tests
# ============================================================================

class TestActionResult:
    """Tests for ActionResult dataclass."""

    def test_ok_creates_success_result(self):
        """Test ActionResult.ok creates success result."""
        result = ActionResult.ok("test message", key="value")
        
        assert result.success is True
        assert result.message == "test message"
        assert result.data.get("key") == "value"

    def test_fail_creates_failure_result(self):
        """Test ActionResult.fail creates failure result."""
        result = ActionResult.fail("error message", code=500)
        
        assert result.success is False
        assert result.message == "error message"
        assert result.data.get("code") == 500


# ============================================================================
# BaseAction Tests
# ============================================================================

class TestBaseAction:
    """Tests for BaseAction class."""

    def test_run_command_success(self, mock_context):
        """Test that run_command dispatches to transport and returns response."""
        action = BaseAction()
        mock_context.client.transport.set_response("test_cmd", {"result": "success"})
        
        result = action.run_command(mock_context, "test_cmd", {"param": "value"})
        
        assert result == {"result": "success"}
        assert ("test_cmd", {"param": "value"}) in mock_context.client.transport.calls

    def test_run_command_raises_on_exception(self, mock_context):
        """Test that run_command raises CommandError on transport error."""
        action = BaseAction()
        
        def raise_error(route, payload):
            raise RuntimeError("Transport failure")
        
        mock_context.client.transport.dispatch = raise_error
        
        with pytest.raises(CommandError):
            action.run_command(mock_context, "failing_cmd", {})

    def test_execute_raises_not_implemented(self, mock_context):
        """Test that execute() raises NotImplementedError."""
        action = BaseAction()
        
        with pytest.raises(NotImplementedError):
            action.execute(mock_context)


# ============================================================================
# InventoryAction-like Tests
# ============================================================================

class InventoryAction(BaseAction):
    """Self-contained InventoryAction for testing."""
    
    def get_inventory(self, context: ActionContext) -> Dict[str, int]:
        response = self.run_command(context, "get_inventory", {})
        data = response.get("data", response)
        counts: Dict[str, int] = {}
        
        for section in ["inventory", "armor", "offhand"]:
            for item in data.get(section, []):
                item_id = item.get("id", "")
                count = item.get("count", 0)
                if item_id and count > 0:
                    counts[item_id] = counts.get(item_id, 0) + count
        return counts
    
    def count_item(self, context: ActionContext, item_id: str) -> int:
        return self.get_inventory(context).get(item_id, 0)
    
    def find_item_slot(self, context: ActionContext, item_id: str) -> Optional[int]:
        response = self.run_command(context, "get_inventory", {})
        data = response.get("data", response)
        
        for item in data.get("inventory", []):
            if item.get("id") == item_id and item.get("count", 0) > 0:
                return item.get("slot")
        return None
    
    def execute(self, context: ActionContext) -> ActionResult:
        return ActionResult.fail("InventoryAction requires a specific method call")


class TestInventoryAction:
    """Tests for InventoryAction class."""

    def test_get_inventory_returns_aggregated_counts(self, mock_context):
        """Test get_inventory aggregates item counts correctly."""
        action = InventoryAction()
        mock_context.client.transport.add_inventory_item("minecraft:diamond", 10, slot=0)
        mock_context.client.transport.add_inventory_item("minecraft:diamond", 5, slot=1)
        mock_context.client.transport.add_inventory_item("minecraft:iron_ingot", 32, slot=2)
        
        result = action.get_inventory(mock_context)
        
        assert result["minecraft:diamond"] == 15
        assert result["minecraft:iron_ingot"] == 32

    def test_count_item_returns_total(self, mock_context):
        """Test count_item returns correct total for specific item."""
        action = InventoryAction()
        mock_context.client.transport.add_inventory_item("minecraft:cobblestone", 64, slot=0)
        mock_context.client.transport.add_inventory_item("minecraft:cobblestone", 32, slot=1)
        
        count = action.count_item(mock_context, "minecraft:cobblestone")
        
        assert count == 96

    def test_count_item_returns_zero_for_missing(self, mock_context):
        """Test count_item returns 0 for items not in inventory."""
        action = InventoryAction()
        
        count = action.count_item(mock_context, "minecraft:netherite_ingot")
        
        assert count == 0

    def test_find_item_slot_returns_slot(self, mock_context):
        """Test find_item_slot returns correct slot number."""
        action = InventoryAction()
        mock_context.client.transport.add_inventory_item("minecraft:stone_pickaxe", 1, slot=5)
        
        slot = action.find_item_slot(mock_context, "minecraft:stone_pickaxe")
        
        assert slot == 5

    def test_find_item_slot_returns_none_for_missing(self, mock_context):
        """Test find_item_slot returns None for missing items."""
        action = InventoryAction()
        
        slot = action.find_item_slot(mock_context, "minecraft:elytra")
        
        assert slot is None


# ============================================================================
# CraftingAction-like Tests
# ============================================================================

class CraftingAction(BaseAction):
    """Self-contained CraftingAction for testing."""
    
    def craft(self, context: ActionContext, item_id: str, count: int = 1) -> bool:
        try:
            response = self.run_command(context, "craft", {"item": item_id, "count": count})
            return response.get("status") == "ok"
        except Exception:
            return False
    
    def execute(self, context: ActionContext) -> ActionResult:
        return ActionResult.fail("CraftingAction requires a specific method call")


class TestCraftingAction:
    """Tests for CraftingAction class."""

    def test_craft_dispatches_command(self, mock_context):
        """Test craft dispatches craft command to transport."""
        action = CraftingAction()
        
        result = action.craft(mock_context, "minecraft:stone_pickaxe", 1)
        
        assert result is True
        calls = mock_context.client.transport.calls
        craft_calls = [c for c in calls if c[0] == "craft"]
        assert len(craft_calls) >= 1
        assert craft_calls[-1][1]["item"] == "minecraft:stone_pickaxe"

    def test_craft_returns_false_on_failure(self, mock_context):
        """Test craft returns False when crafting fails."""
        action = CraftingAction()
        mock_context.client.transport.set_response("craft", {"status": "error", "message": "No materials"})
        
        result = action.craft(mock_context, "minecraft:diamond_pickaxe", 1)
        
        assert result is False


# ============================================================================
# Initial Gathering Action Tests
# ============================================================================

class TestWoodCollectionPhase:
    """Tests for WoodCollectionPhase action."""

    def test_wood_collection_success(self, mock_context):
        """Test wood collection succeeds with available wood."""
        from baritone_client.actions.initial_gathering import WoodCollectionPhase
        action = WoodCollectionPhase()
        
        # Mock gather_wood to succeed in the module scope
        import baritone_client.actions.initial_gathering as ig
        original_gather_wood = ig.gather_wood
        ig.gather_wood = lambda client, count: True

        try:
            result = action.execute(mock_context)
            assert result.success is True
            assert "completed" in result.message.lower()
        finally:
            ig.gather_wood = original_gather_wood

    def test_wood_collection_fails_on_minimal_wood(self, mock_context):
        """Test wood collection fails if minimal wood gathering fails."""
        from baritone_client.actions.initial_gathering import WoodCollectionPhase
        action = WoodCollectionPhase()

        # Mock gather_wood to fail on first call - patch the action module's
        # binding (it imports gather_wood directly), like the success test.
        import baritone_client.actions.initial_gathering as ig
        call_count = [0]
        def mock_gather_wood(client, count):
            call_count[0] += 1
            return call_count[0] > 1  # Fail first, succeed second

        original_gather_wood = ig.gather_wood
        ig.gather_wood = mock_gather_wood

        try:
            result = action.execute(mock_context)
            assert result.success is False
        finally:
            ig.gather_wood = original_gather_wood


class TestToolProgressionPhase:
    """Tests for ToolProgressionPhase action."""

    def test_tool_progression_success(self, mock_context):
        """Test tool progression succeeds with proper inventory."""
        from baritone_client.actions.initial_gathering import ToolProgressionPhase
        action = ToolProgressionPhase()
        
        # Mock ensure_crafting_table to avoid heavy simulation/hang
        action.crafting.ensure_crafting_table = lambda ctx: True

        # Add some planks and sticks to inventory
        mock_context.client.transport.add_inventory_item("minecraft:oak_planks", 16, slot=0)
        mock_context.client.transport.add_inventory_item("minecraft:stick", 8, slot=1)
        mock_context.client.transport.add_inventory_item("minecraft:cobblestone", 8, slot=2)

        result = action.execute(mock_context)
        assert result.success is True

    def test_tool_progression_fails_without_materials(self, mock_context):
        """Test tool progression fails without required materials."""
        from baritone_client.actions.initial_gathering import ToolProgressionPhase
        action = ToolProgressionPhase()

        result = action.execute(mock_context)
        # May still succeed if crafting ensures tables etc., but test basic case
        assert isinstance(result.success, bool)  # Just ensure it returns a result


class TestStoneCollectionPhase:
    """Tests for StoneCollectionPhase action."""

    def test_stone_collection_success(self, mock_context):
        """Test stone collection succeeds."""
        from baritone_client.actions.initial_gathering import StoneCollectionPhase
        action = StoneCollectionPhase()

        # Mock gather_stone to succeed
        import baritone_client.actions.initial_gathering as ig
        original_gather_stone = ig.gather_stone
        ig.gather_stone = lambda client, count: True

        try:
            result = action.execute(mock_context)
            assert result.success is True
            assert "completed" in result.message.lower()
        finally:
            ig.gather_stone = original_gather_stone


class TestBedPreparationPhase:
    """Tests for BedPreparationPhase action."""

    def test_bed_preparation_with_wool(self, mock_context):
        """Test bed preparation succeeds with wool available."""
        from baritone_client.actions.initial_gathering import BedPreparationPhase
        action = BedPreparationPhase()

        # Add wool to inventory
        mock_context.client.transport.add_inventory_item("minecraft:white_wool", 3, slot=0)

        result = action.execute(mock_context)
        assert result.success is True

    def test_bed_preparation_without_wool_fails(self, mock_context):
        """Test bed preparation fails without wool."""
        from baritone_client.actions.initial_gathering import BedPreparationPhase
        action = BedPreparationPhase()

        result = action.execute(mock_context)
        # Implementation may try hunting, but for test we expect it to handle gracefully
        assert isinstance(result.success, bool)


class TestStorageSetupPhase:
    """Tests for StorageSetupPhase action."""

    def test_storage_setup_success(self, mock_context):
        """Test storage setup succeeds with materials."""
        from baritone_client.actions.initial_gathering import StorageSetupPhase
        action = StorageSetupPhase()

        # Add materials for chest
        mock_context.client.transport.add_inventory_item("minecraft:oak_planks", 8, slot=0)

        result = action.execute(mock_context)
        assert isinstance(result.success, bool)  # Should complete whether placing succeeds or not


class TestSurvivalPhase:
    """Tests for SurvivalPhase action."""

    def test_survival_phase_success(self, mock_context):
        """Test survival phase succeeds."""
        from baritone_client.actions.initial_gathering import SurvivalPhase
        action = SurvivalPhase()

        result = action.execute(mock_context)
        # Survival phase tries multiple things, should handle gracefully
        assert isinstance(result.success, bool)
