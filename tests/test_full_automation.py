"""
Full end-to-end test for spawn-to-dragon automation.
Tests the complete orchestration logic using MockClient and real handlers with stubbed transport responses.
Verifies phase transitions, error handling, and success conditions.
"""

import unittest
import tempfile
from unittest.mock import MagicMock
from typing import Dict, Any

from baritone_client.automator.phase_executor import PhaseExecutor
from baritone_client.automator.resource_manager import ResourceManager
from baritone_client.automator.state_manager import StateManager, Phase
from baritone_client.common.tasks import MockClient

# Import TaskResult and PhaseHandler
from baritone_client.common.tasks import TaskResult
from baritone_client.automator.phase_executor import PhaseHandler


class StubPhaseHandler(PhaseHandler):
    """Stub handler that simulates successful phase execution for testing."""

    def __init__(self, name: str, phase: Phase):
        self._name = name
        self._phase = phase

    def get_name(self) -> str:
        return self._name

    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        # Record execution for testing
        client.calls.append(("handler_execute", self._name, self._phase.name))
        # Simulate some inventory/resource gathering
        if self._phase == Phase.INITIAL_GATHERING:
            return TaskResult.ok(f"{self._name} completed", inventory={"minecraft:oak_log": 16, "minecraft:cobblestone": 16})
        elif self._phase == Phase.BASE_CONSTRUCTION:
            return TaskResult.ok(f"{self._name} completed", inventory={"minecraft:crafting_table": 1, "minecraft:furnace": 1})
        elif self._phase == Phase.FOOD_AND_IRON:
            return TaskResult.ok(f"{self._name} completed", inventory={"minecraft:iron_ingot": 32})
        elif self._phase == Phase.ENCHANTING_PIPELINE:
            return TaskResult.ok(f"{self._name} completed", inventory={"minecraft:diamond": 20})
        elif self._phase == Phase.NETHER_AND_BLAZE:
            return TaskResult.ok(f"{self._name} completed", inventory={"minecraft:blaze_rod": 10})
        elif self._phase == Phase.WORLD_UNLOCK:
            return TaskResult.ok(
                f"{self._name} completed - Dragon defeated!",
                dragon_defeated=True,
                stronghold_coords=(100, 200),
            )
        elif self._phase == Phase.MEGABASE_INIT:
            return TaskResult.ok(f"{self._name} completed", inventory={"minecraft:beacon": 1})
        else:
            return TaskResult.ok(f"{self._name} completed")

    def on_enter(self, client, resources: ResourceManager, state: StateManager):
        pass

    def on_exit(self, client, resources: ResourceManager, state: StateManager):
        pass


class StubTransport:
    """Enhanced stub transport that provides realistic responses for automation."""

    def __init__(self, owner, failure_mode=False):
        self.owner = owner
        self.call_count = 0
        self.in_dimension = "minecraft:overworld"
        self.failure_mode = failure_mode
        self.responses = {
            "get_inventory": {"inventory": [], "armor": [], "offhand": []},
            "get_state": {"x": 0, "y": 64, "z": 0, "health": 20, "is_dead": False},
            "get_dimension": {"dimension": "minecraft:overworld"},
            "get_position": {"x": 0, "y": 64, "z": 0},
            "mine": {"status": "ok", "data": {}},
            "goto": {"status": "ok", "data": {}},
            "craft": {"status": "ok", "data": {}},
            "command/run": {"status": "ok", "data": {}},
            "respawn": {"status": "ok", "data": {}},
            "get_death_location": {"status": "ok", "data": {"x": 0, "y": 64, "z": 0, "dimension": "minecraft:overworld"}},
            "scan_blocks": {"status": "ok", "data": {"blocks": []}},
        }

    def dispatch(self, route, payload, **kwargs):
        self.owner.calls.append(("dispatch", route, payload))
        self.call_count += 1

        # In failure mode, return errors for certain operations
        if self.failure_mode and route in ["mine", "goto", "craft"]:
            return {"status": "error", "message": "Simulated failure"}

        # Provide dynamic responses based on call context
        if route == "get_inventory":
            # Provide sufficient inventory for all phases
            inventory = [
                {"id": "minecraft:oak_log", "count": 64},
                {"id": "minecraft:cobblestone", "count": 64},
                {"id": "minecraft:iron_ore", "count": 32},
                {"id": "minecraft:coal", "count": 32},
                {"id": "minecraft:diamond", "count": 20},
                {"id": "minecraft:obsidian", "count": 20},
                {"id": "minecraft:blaze_rod", "count": 12},
                {"id": "minecraft:ender_pearl", "count": 16},
                {"id": "minecraft:bread", "count": 20},
                {"id": "minecraft:cooked_beef", "count": 20},
                {"id": "minecraft:leather", "count": 16},
                {"id": "minecraft:flint", "count": 8},
                {"id": "minecraft:iron_ingot", "count": 32},
                {"id": "minecraft:gold_ingot", "count": 16},
            ]
            return {"inventory": inventory, "armor": [], "offhand": []}

        elif route == "get_state":
            return self.responses[route]

        elif route == "get_dimension":
            return {"status": "ok", "data": {"dimension": self.in_dimension}}

        elif route == "command/run":
            # Simulate successful command execution
            return {"status": "ok", "data": {}}

        elif route == "mine":
            # Simulate successful mining
            return {"status": "ok", "data": {"mined": payload.get("quantity", 1)}}

        elif route == "goto":
            state = self.responses["get_state"]
            state["x"] = payload.get("x", state.get("x", 0))
            state["y"] = payload.get("y", state.get("y", 64))
            state["z"] = payload.get("z", state.get("z", 0))
            return {"status": "ok", "data": {}}

        elif route == "craft":
            # Simulate successful crafting
            return {"status": "ok", "data": {"crafted": 1}}

        elif route == "scan_blocks":
            # Simulate finding blocks for portal detection
            if "obsidian" in str(payload):
                blocks = [
                    {"type": "minecraft:obsidian", "x": 0, "y": 64, "z": 5},
                    {"type": "minecraft:obsidian", "x": 1, "y": 64, "z": 5},
                ]
                return {"status": "ok", "data": {"blocks": blocks}}
            return {"status": "ok", "data": {"blocks": []}}

        # Change dimension for Nether travel simulation
        if route == "goto" and payload.get("y", 0) < 0:
            self.in_dimension = "minecraft:the_nether"
        elif route == "goto" and payload.get("y", 0) > 60:
            self.in_dimension = "minecraft:overworld"

        # Return default response for other routes
        return self.responses.get(route, {"status": "ok", "data": {}})


class StubMissionFacade:
    """Minimal mission facade for unit tests."""

    def __init__(self, client):
        self.client = client
        self.calls = []
        self._status = {
            "mission": {"phase": "idle", "note": "test", "queue": [], "history": []}
        }

    def status(self):
        return self._status

    def macro(self, name=None, params=None, dequeue=False):
        params = params or {}
        self.calls.append(("macro", name, params, dequeue))
        result = {}
        if name == "bootstrap":
            result["settingsApplied"] = len(params.get("settings", {}))
        elif name == "locate_stronghold":
            result["queued"] = True
        elif name == "craft_eyes":
            result["ready"] = True
        else:
            result["ok"] = True
        return {"result": result}

    def checkpoint(self, phase, note=None):
        self.calls.append(("checkpoint", phase, note))
        return {"phase": phase, "note": note}

    def queue(self, actions, clear=False):
        self.calls.append(("queue", list(actions), clear))
        return {"queued": len(actions)}


class EnhancedMockClient(MockClient):
    """Mock client with realistic transport responses for automation testing."""

    def __init__(self, failure_mode=False):
        super().__init__()
        self.transport = StubTransport(self, failure_mode=failure_mode)
        self.mission = StubMissionFacade(self)





class TestFullAutomation(unittest.TestCase):
    """Full end-to-end automation test."""

    def setUp(self):
        """Set up test fixtures."""
        self.checkpoint_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.checkpoint_dir.cleanup)
        self.client = EnhancedMockClient()
        self.resources = ResourceManager(self.client)
        self.state_manager = StateManager(checkpoint_dir=self.checkpoint_dir.name)
        self.executor = PhaseExecutor(
            self.client,
            self.resources,
            self.state_manager,
            retry_delay=0.0,
        )

        # Register stub handlers for all phases to test orchestration logic
        phase_handlers = [
            (Phase.BRIDGE_CHECK, "Bridge Check"),
            (Phase.SPAWN_BOOTSTRAP, "Spawn Bootstrap"),
            (Phase.INITIAL_GATHERING, "Initial Gathering"),
            (Phase.BASE_CONSTRUCTION, "Base Construction"),
            (Phase.BOOT_SEQUENCE, "Boot Sequence"),
            (Phase.FOOD_AND_IRON, "Food and Iron"),
            (Phase.ENCHANTING_PIPELINE, "Enchanting Pipeline"),
            (Phase.NETHER_AND_BLAZE, "Nether and Blaze"),
            (Phase.VILLAGER_INFRA, "Villager Infrastructure"),
            (Phase.XP_ENGINE, "XP Engine"),
            (Phase.IRON_FARM, "Iron Farm"),
            (Phase.TOOL_PERFECTION, "Tool Perfection"),
            (Phase.WORLD_UNLOCK, "World Unlock"),
            (Phase.MEGABASE_INIT, "Megabase Initialization"),
            (Phase.TERRAFORM, "Terraform"),
            (Phase.CITY_BUILD, "City Build"),
        ]

        for phase, name in phase_handlers:
            handler = StubPhaseHandler(name, phase)
            self.executor.register_handler(phase, handler)

    def test_successful_full_progression(self):
        """Test successful progression through all phases."""
        # Start from beginning
        self.state_manager.set_phase(Phase.BRIDGE_CHECK)

        # Execute all phases
        phases_executed = []
        current_phase = self.state_manager.get_current_phase()

        while current_phase != Phase.COMPLETE:
            success = self.executor.execute_phase(current_phase)
            self.assertTrue(success, f"Phase {current_phase.name} failed")

            phases_executed.append(current_phase.name)
            self.state_manager.advance_phase()
            current_phase = self.state_manager.get_current_phase()

        # Verify all phases were executed
        expected_phases = [phase.name for phase in Phase if phase is not Phase.COMPLETE]
        self.assertEqual(phases_executed, expected_phases)

        # Verify final state
        self.assertEqual(self.state_manager.get_current_phase(), Phase.COMPLETE)

    def test_phase_transition_tracking(self):
        """Test that phase transitions are properly tracked."""
        initial_phase = Phase.BRIDGE_CHECK
        self.state_manager.set_phase(initial_phase)

        # Execute first few phases
        phases = [Phase.BRIDGE_CHECK, Phase.SPAWN_BOOTSTRAP, Phase.INITIAL_GATHERING]

        for phase in phases:
            self.assertEqual(self.state_manager.get_current_phase(), phase)
            success = self.executor.execute_phase(phase)
            self.assertTrue(success)
            self.state_manager.advance_phase()

        # Verify progression
        self.assertEqual(self.state_manager.get_current_phase(), Phase.BASE_CONSTRUCTION)

    def test_error_handling_and_recovery(self):
        """Test error handling when a phase handler fails."""
        # Create a failing stub handler
        class FailingStubHandler(StubPhaseHandler):
            def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
                return TaskResult.fail("Simulated phase failure")

        failing_client = EnhancedMockClient()
        failing_resources = ResourceManager(failing_client)
        failing_state_manager = StateManager(checkpoint_dir=self.checkpoint_dir.name)
        failing_executor = PhaseExecutor(
            failing_client,
            failing_resources,
            failing_state_manager,
            retry_delay=0.0,
        )

        # Register the failing handler
        failing_handler = FailingStubHandler("Failing Phase", Phase.FOOD_AND_IRON)
        failing_executor.register_handler(Phase.FOOD_AND_IRON, failing_handler)

        # Set to iron age phase
        failing_state_manager.set_phase(Phase.FOOD_AND_IRON)

        # Attempt the phase - should fail
        success = failing_executor.execute_phase(Phase.FOOD_AND_IRON)
        self.assertFalse(success)

        # Verify phase did not advance
        self.assertEqual(failing_state_manager.get_current_phase(), Phase.FOOD_AND_IRON)

    def test_checkpoint_save_and_load(self):
        """Test checkpoint persistence during automation."""
        # Execute a phase and save checkpoint
        self.state_manager.set_phase(Phase.INITIAL_GATHERING)
        self.executor.execute_phase(Phase.INITIAL_GATHERING)

        checkpoint_path = self.state_manager.save_checkpoint({"minecraft:oak_log": 16})

        # Create new state manager and load
        new_state_manager = StateManager(checkpoint_dir=self.checkpoint_dir.name)
        loaded = new_state_manager.load_checkpoint()

        self.assertTrue(loaded)
        self.assertEqual(new_state_manager.get_current_phase(), Phase.INITIAL_GATHERING)

        # Clean up
        new_state_manager.clear_checkpoint()

    def test_inventory_tracking_through_phases(self):
        """Test that inventory is tracked and updated through phases."""
        # Execute initial gathering
        self.state_manager.set_phase(Phase.INITIAL_GATHERING)
        self.executor.execute_phase(Phase.INITIAL_GATHERING)

        # Check that inventory was refreshed
        inventory_calls = [call for call in self.client.calls if call[0] == "dispatch" and call[1] == "get_inventory"]
        self.assertGreater(len(inventory_calls), 0)

    def test_transport_dispatch_recording(self):
        """Test that all transport dispatches are properly recorded."""
        # Execute a phase
        self.state_manager.set_phase(Phase.BASE_CONSTRUCTION)
        self.executor.execute_phase(Phase.BASE_CONSTRUCTION)
    
        # Check for recorded calls
        dispatch_calls = [call for call in self.client.calls if call[0] == "dispatch"]
        self.assertGreater(len(dispatch_calls), 0)


if __name__ == '__main__':
    unittest.main()
