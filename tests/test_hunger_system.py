
import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from baritone_client.automator.systems import HungerSystem
from baritone_client.automator.coordination_hub import CoordinationHub

class TestHungerSystem(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock()
        self.mock_coordination = MagicMock(spec=CoordinationHub)
        # Mock transport dispatch
        self.mock_client.transport.dispatch = MagicMock()
        
        self.hunger_system = HungerSystem(self.mock_client, self.mock_coordination)

    def test_eating_trigger(self):
        # Setup low hunger
        state_response = {"food_level": 10, "food": 10} # < 18 check
        
        # Setup inventory with apple
        inventory_response = {
            "inventory": [
                {"id": "minecraft:stone", "count": 64, "slot": 0},
                {"id": "minecraft:apple", "count": 5, "slot": 8}
            ],
            "offhand": []
        }
        
        def dispatch_side_effect(command, params, timeout=None):
            if command == "get_state":
                return state_response
            if command == "get_inventory":
                return inventory_response
            if command == "select_slot":
                return {"success": True}
            if command == "use_item":
                return {"used": True}
            return {}
            
        self.mock_client.transport.dispatch.side_effect = dispatch_side_effect

        # Run tick
        self.hunger_system.tick()
        
        # Verify get_state called
        self.mock_client.transport.dispatch.assert_any_call("get_state", {}, timeout=1.0)
        
        # Verify get_inventory called (because hunger < 18)
        self.mock_client.transport.dispatch.assert_any_call("get_inventory", {})
        
        # Verify select_slot called for apple (slot 8) - Checking logic in EatAction
        # EatAction logic: check inventory, find apple at slot 8.
        # Current slot defaults to 0 in mock if not specified.
        # EatAction should switch to slot 8.
        self.mock_client.transport.dispatch.assert_any_call("select_slot", {"slot": 8})
        
        # Verify use_item called
        self.mock_client.transport.dispatch.assert_any_call("use_item", {"duration_ms": 1600})

    def test_no_food_found(self):
        # Setup low hunger
        self.mock_client.transport.dispatch.side_effect = None
        self.mock_client.transport.dispatch.return_value = {"food_level": 10}
        
        def dispatch_side_effect(command, params, timeout=None):
            if command == "get_state":
                return {"food_level": 10}
            if command == "get_inventory":
                return {"inventory": [{"id": "minecraft:stone", "count": 1}], "offhand": []}
            return {}
            
        self.mock_client.transport.dispatch.side_effect = dispatch_side_effect
        
        # Run tick
        self.hunger_system.tick()
        
        # Should call get_inventory
        self.mock_client.transport.dispatch.assert_any_call("get_inventory", {})
        
        # Should NOT call use_item
        with self.assertRaises(AssertionError):
            self.mock_client.transport.dispatch.assert_any_call("use_item", {"duration_ms": 1600})

if __name__ == '__main__':
    unittest.main()
