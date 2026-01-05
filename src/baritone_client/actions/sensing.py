"""
Sensing action implementation for gathering environmental and status information.
"""

from typing import Dict, List, Optional, Tuple, Any

from baritone_client.core.interfaces import ActionContext, ActionResult
from baritone_client.actions.base import BaseAction


class SensingAction(BaseAction):
    """Handles sensing operations for gathering information about the environment and player status."""

    def check_health(self, context: ActionContext) -> ActionResult:
        """Check current health status."""
        try:
            state = context.state.refresh()
            health = getattr(state, 'health', 0)
            return ActionResult.ok("Health checked", health=health)
        except Exception as e:
            return ActionResult.fail(f"Failed to check health: {e}")

    def check_hunger(self, context: ActionContext) -> ActionResult:
        """Check current hunger/food level."""
        try:
            state = context.state.refresh()
            food_level = getattr(state, 'food_level', 0)
            return ActionResult.ok("Hunger checked", food_level=food_level)
        except Exception as e:
            return ActionResult.fail(f"Failed to check hunger: {e}")

    def check_position(self, context: ActionContext) -> ActionResult:
        """Check current position coordinates."""
        try:
            state = context.state.refresh()
            position = getattr(state, 'position', [0, 0, 0])
            return ActionResult.ok("Position checked", position=position)
        except Exception as e:
            return ActionResult.fail(f"Failed to check position: {e}")

    def check_inventory(self, context: ActionContext, item_filter: Optional[str] = None) -> ActionResult:
        """Check inventory contents, optionally filtering by item type."""
        try:
            inventory_data = self.run_command(context, "get_inventory", {})
            inventory = inventory_data.get("inventory", [])

            if item_filter:
                filtered_inventory = [item for item in inventory if item_filter.lower() in item.get("name", "").lower()]
                return ActionResult.ok("Inventory filtered", inventory=filtered_inventory)
            else:
                return ActionResult.ok("Inventory checked", inventory=inventory)
        except Exception as e:
            return ActionResult.fail(f"Failed to check inventory: {e}")

    def check_nearby_entities(self, context: ActionContext, entity_types: Optional[List[str]] = None, radius: int = 50) -> ActionResult:
        """Check for nearby entities, optionally filtering by type."""
        try:
            entities_data = self.run_command(context, "get_entities", {"radius": radius})
            entities = entities_data.get("entities", [])

            if entity_types:
                filtered_entities = [entity for entity in entities if entity.get("type", "").lower() in [t.lower() for t in entity_types]]
                return ActionResult.ok("Entities filtered", entities=filtered_entities)
            else:
                return ActionResult.ok("Entities checked", entities=entities)
        except Exception as e:
            return ActionResult.fail(f"Failed to check entities: {e}")

    def check_block_at(self, context: ActionContext, x: int, y: int, z: int) -> ActionResult:
        """Check what block is at specific coordinates."""
        try:
            block_data = self.run_command(context, "get_block", {"x": x, "y": y, "z": z})
            block = block_data.get("block", {})
            return ActionResult.ok("Block checked", block=block)
        except Exception as e:
            return ActionResult.fail(f"Failed to check block: {e}")

    def check_biome(self, context: ActionContext) -> ActionResult:
        """Check current biome information."""
        try:
            state = context.state.refresh()
            biome = getattr(state, 'biome', 'unknown')
            return ActionResult.ok("Biome checked", biome=biome)
        except Exception as e:
            return ActionResult.fail(f"Failed to check biome: {e}")

    def check_time_of_day(self, context: ActionContext) -> ActionResult:
        """Check current time of day."""
        try:
            state = context.state.refresh()
            time_of_day = getattr(state, 'time_of_day', 0)
            return ActionResult.ok("Time checked", time_of_day=time_of_day)
        except Exception as e:
            return ActionResult.fail(f"Failed to check time: {e}")

    def is_night_time(self, context: ActionContext) -> ActionResult:
        """Check if it's currently night time."""
        try:
            time_result = self.check_time_of_day(context)
            if not time_result.success:
                return time_result

            time_of_day = time_result.data.get('time_of_day', 0)
            # Minecraft day/night cycle: 0-12000 is day, 12000-24000 is night
            is_night = 12000 <= time_of_day < 24000
            return ActionResult.ok("Night check completed", is_night=is_night)
        except Exception as e:
            return ActionResult.fail(f"Failed to check night time: {e}")

    def check_weather(self, context: ActionContext) -> ActionResult:
        """Check current weather conditions."""
        try:
            state = context.state.refresh()
            weather = getattr(state, 'weather', 'clear')
            raining = getattr(state, 'raining', False)
            return ActionResult.ok("Weather checked", weather=weather, raining=raining)
        except Exception as e:
            return ActionResult.fail(f"Failed to check weather: {e}")