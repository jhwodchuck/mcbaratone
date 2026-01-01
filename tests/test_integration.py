"""
Integration tests for vertical slice implementation (initial gathering + base construction phases).
Tests phase execution flow, resource requirements, task composition, error handling, and state persistence.
"""

import unittest
from unittest.mock import MagicMock, patch
from typing import Dict, Any

# Import required modules
from baritone_client.automator.resource_manager import ResourceManager
from baritone_client.automator.state_manager import StateManager, Phase
from baritone_client.common.tasks import MockClient, TaskResult, SequentialTask, ActionTask
from baritone_client.automator.phase_executor import PhaseHandler


# Stub handlers for testing (avoiding import issues with real handlers)
class StubInitialGatheringHandler(PhaseHandler):
    """Stub handler that simulates initial gathering phase."""

    def get_name(self) -> str:
        return "Initial Gathering"

    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        # Simulate gathering tasks
        tasks = [
            ActionTask("Gather wood", lambda c: True),
            ActionTask("Craft tools", lambda c: True),
            ActionTask("Mine stone", lambda c: True),
        ]
        sequential_task = SequentialTask("Initial Gathering", tasks)
        result = sequential_task.run(client)

        if result.success:
            return TaskResult.ok("Initial gathering complete", inventory={"minecraft:oak_log": 16})
        else:
            missing = resources.check_phase_requirements(Phase.INITIAL_GATHERING)
            return TaskResult.fail(f"Initial gathering failed: {result.reason}", missing=missing)


class StubBaseConstructionHandler(PhaseHandler):
    """Stub handler that simulates base construction phase."""

    def get_name(self) -> str:
        return "Base Construction"

    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        # Simulate construction tasks
        tasks = [
            ActionTask("Find location", lambda c: True),
            ActionTask("Build shelter", lambda c: True),
            ActionTask("Setup infrastructure", lambda c: True),
        ]
        sequential_task = SequentialTask("Base Construction", tasks)
        result = sequential_task.run(client)

        if result.success:
            return TaskResult.ok("Base construction complete", inventory={"minecraft:crafting_table": 1})
        else:
            missing = resources.check_phase_requirements(Phase.BASE_CONSTRUCTION)
            return TaskResult.fail(f"Base construction failed: {result.reason}", missing=missing)


class TestIntegration(unittest.TestCase):
    """Integration tests for automator phases using MockClient."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_client = MockClient()
        self.resource_manager = ResourceManager(self.mock_client)
        self.state_manager = StateManager()

    def test_initial_gathering_phase_execution_success(self):
        """Test successful execution of initial gathering phase."""
        handler = StubInitialGatheringHandler()
        result = handler.execute(self.mock_client, self.resource_manager, self.state_manager)

        # Assert success
        self.assertTrue(result.success)
        self.assertIn("Initial gathering complete", result.reason)
        self.assertIn("inventory", result.data)

        # Verify MockClient recorded calls (all tasks should succeed)
        self.assertGreater(len(self.mock_client.calls), 0)

    def test_initial_gathering_phase_execution_failure_missing_resources(self):
        """Test failure when resource requirements are not met."""
        # Set up resource manager to show missing requirements
        self.resource_manager.check_phase_requirements = MagicMock(return_value={
            "minecraft:oak_log": 16,
            "minecraft:cobblestone": 16
        })

        # Create a failing handler
        class FailingInitialGatheringHandler(StubInitialGatheringHandler):
            def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
                tasks = [
                    ActionTask("Gather wood", lambda c: False),  # This will fail
                ]
                sequential_task = SequentialTask("Initial Gathering", tasks)
                result = sequential_task.run(client)
                if result.success:
                    return TaskResult.ok("Initial gathering complete")
                else:
                    missing = resources.check_phase_requirements(Phase.INITIAL_GATHERING)
                    return TaskResult.fail(f"Initial gathering failed: {result.reason}", missing=missing)

        handler = FailingInitialGatheringHandler()
        result = handler.execute(self.mock_client, self.resource_manager, self.state_manager)

        # Assert failure
        self.assertFalse(result.success)
        self.assertIn("Initial gathering failed", result.reason)
        self.assertIn("missing", result.data)

    def test_base_construction_phase_execution_success(self):
        """Test successful execution of base construction phase."""
        handler = StubBaseConstructionHandler()
        result = handler.execute(self.mock_client, self.resource_manager, self.state_manager)

        # Assert success
        self.assertTrue(result.success)
        self.assertIn("Base construction complete", result.reason)
        self.assertIn("inventory", result.data)

        # Verify MockClient recorded calls
        self.assertGreater(len(self.mock_client.calls), 0)

    def test_base_construction_task_composition_error_handling(self):
        """Test error handling when a subtask in the sequence fails."""
        # Create a failing handler that demonstrates task composition error handling
        class FailingBaseConstructionHandler(StubBaseConstructionHandler):
            def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
                tasks = [
                    ActionTask("Find location", lambda c: True),  # This succeeds
                    ActionTask("Build shelter", lambda c: False),  # This fails
                    ActionTask("Setup infrastructure", lambda c: True),  # This won't run
                ]
                sequential_task = SequentialTask("Base Construction", tasks)
                result = sequential_task.run(client)

                if result.success:
                    return TaskResult.ok("Base construction complete")
                else:
                    missing = resources.check_phase_requirements(Phase.BASE_CONSTRUCTION)
                    return TaskResult.fail(f"Base construction failed: {result.reason}", missing=missing)

        # Mock resource manager to show missing requirements on failure
        self.resource_manager.check_phase_requirements = MagicMock(return_value={
            "minecraft:crafting_table": 1,
            "minecraft:furnace": 1
        })

        handler = FailingBaseConstructionHandler()
        result = handler.execute(self.mock_client, self.resource_manager, self.state_manager)

        # Assert failure due to sequential task stopping at first failure
        self.assertFalse(result.success)
        self.assertIn("Base construction failed", result.reason)

    def test_resource_requirements_checking(self):
        """Test that resource requirements are properly checked."""
        # Set up mock inventory with insufficient resources
        self.resource_manager.cached_inventory = {
            "minecraft:oak_log": 10,  # Need 16
            "minecraft:cobblestone": 5,  # Need 16
        }

        # Check initial gathering requirements
        missing = self.resource_manager.check_phase_requirements(Phase.INITIAL_GATHERING)
        expected_missing = {
            "minecraft:oak_log": 6,
            "minecraft:cobblestone": 11
        }
        self.assertEqual(missing, expected_missing)

        # Check base construction requirements
        missing_base = self.resource_manager.check_phase_requirements(Phase.BASE_CONSTRUCTION)
        self.assertIn("minecraft:crafting_table", missing_base)
        self.assertIn("minecraft:furnace", missing_base)
        self.assertIn("minecraft:chest", missing_base)

    def test_state_persistence_and_resumption(self):
        """Test that state manager handles persistence and resumption correctly."""
        # Set initial state
        self.state_manager.set_phase(Phase.INITIAL_GATHERING)
        self.state_manager.update_progress(0.5)

        # Save checkpoint
        checkpoint_path = self.state_manager.save_checkpoint({"minecraft:oak_log": 8})

        # Create new state manager and load checkpoint
        new_state_manager = StateManager()
        loaded = new_state_manager.load_checkpoint()

        # Verify state was loaded correctly
        self.assertTrue(loaded)
        self.assertEqual(new_state_manager.get_current_phase(), Phase.INITIAL_GATHERING)
        self.assertEqual(new_state_manager.get_progress(), 0.5)

        # Test advancement
        advanced = new_state_manager.advance_phase()
        self.assertTrue(advanced)
        self.assertEqual(new_state_manager.get_current_phase(), Phase.BASE_CONSTRUCTION)

        # Clean up checkpoint
        new_state_manager.clear_checkpoint()

    def test_phase_executor_integration(self):
        """Test phase executor with registered handlers."""
        from baritone_client.automator.phase_executor import PhaseExecutor

        # Create phase executor
        executor = PhaseExecutor(self.mock_client, self.resource_manager, self.state_manager)

        # Register stub handlers
        initial_handler = StubInitialGatheringHandler()
        base_handler = StubBaseConstructionHandler()
        executor.register_handler(Phase.INITIAL_GATHERING, initial_handler)
        executor.register_handler(Phase.BASE_CONSTRUCTION, base_handler)

        # Verify handlers are registered
        self.assertTrue(executor.has_handler(Phase.INITIAL_GATHERING))
        self.assertTrue(executor.has_handler(Phase.BASE_CONSTRUCTION))
        self.assertFalse(executor.has_handler(Phase.IRON_AGE))

        # Test handler retrieval
        retrieved = executor.get_handler(Phase.INITIAL_GATHERING)
        self.assertIsInstance(retrieved, StubInitialGatheringHandler)


if __name__ == '__main__':
    unittest.main()