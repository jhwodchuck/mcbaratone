"""
Resource gathering actions for initial gathering phase.
"""

from .base import BaseAction
from ..core.interfaces import ActionContext, ActionResult
from ..common import gather_wood, gather_stone, count_item
from .combat import CombatAction
from .inventory import InventoryAction


class ResourceGatheringAction(BaseAction):
    """Action for gathering basic resources like wood, stone, wool, leather, and food."""

    def __init__(self):
        self.combat = CombatAction()
        self.inventory = InventoryAction()

    def execute(self, context: ActionContext) -> ActionResult:
        """
        Gather initial resources needed for progression.

        Handles wood, stone, wool, leather, and food collection.
        """
        print("Action: Gathering initial resources...")

        success = True

        # Gather minimal wood for tools (4 logs)
        if self.inventory.count_item(context, "minecraft:oak_log") < 4:
            print("  Gathering minimal wood...")
            if not self._gather_wood(context, count=4):
                success = False

        # Gather minimal stone for stone pickaxe (3 cobble)
        if self.inventory.count_item(context, "minecraft:cobblestone") < 3:
            print("  Gathering minimal stone...")
            if not self._gather_stone(context, count=3):
                success = False

        # Gather bulk stone for tools and building (64 cobble)
        if self.inventory.count_item(context, "minecraft:cobblestone") < 64:
            print("  Gathering bulk stone...")
            if not self._gather_stone(context, count=64):
                success = False

        # Gather wool for bed (3 of same color)
        if not self._has_bed_materials(context):
            print("  Gathering wool for bed...")
            if not self._gather_wool(context):
                success = False

        # Gather bulk wood for crafting and building (16 logs)
        target_wood = 16
        current_wood = self.inventory.count_item(context, "minecraft:oak_log") + \
                       self.inventory.count_item(context, "minecraft:spruce_log") + \
                       self.inventory.count_item(context, "minecraft:birch_log")
        if current_wood < target_wood:
            print(f"  Gathering bulk wood ({target_wood - current_wood} more)...")
            if not self._gather_wood(context, count=target_wood - current_wood):
                success = False

        # Gather leather for armor (4 leather)
        if self.inventory.count_item(context, "minecraft:leather") < 4:
            print("  Gathering leather...")
            if not self._gather_leather(context):
                success = False

        # Hunt food (10 items)
        food_count = self._count_food_items(context)
        if food_count < 10:
            print(f"  Hunting food ({10 - food_count} more)...")
            if not self._hunt_food(context, target_count=10 - food_count):
                success = False

        if success:
            return ActionResult.ok("Resource gathering complete")
        else:
            return ActionResult.fail("Failed to gather all required resources")

    def _gather_wood(self, context: ActionContext, count: int) -> bool:
        """Gather specified amount of wood."""
        try:
            return gather_wood(context.client, count=count)
        except Exception as e:
            print(f"  Wood gathering failed: {e}")
            return False

    def _gather_stone(self, context: ActionContext, count: int) -> bool:
        """Gather specified amount of stone."""
        try:
            return gather_stone(context.client, count=count)
        except Exception as e:
            print(f"  Stone gathering failed: {e}")
            return False

    def _gather_wool(self, context: ActionContext) -> bool:
        """Gather 3 wool of the same color."""
        wool_colors = [
            "white", "black", "gray", "light_gray", "brown",
            "red", "orange", "yellow", "lime", "green",
            "cyan", "light_blue", "blue", "purple", "magenta", "pink"
        ]

        # Check if we already have 3 of any color
        for color in wool_colors:
            if self.inventory.count_item(context, f"minecraft:{color}_wool") >= 3:
                print(f"  Already have 3 {color}_wool")
                return True

        # Hunt sheep for wool
        result = self.combat.hunt_passive_mobs(context, target_mobs=["sheep"], target_count=3, timeout=180)
        if not result.success:
            print(f"  Wool gathering failed: {result.message}")
            return False

        # Check if we got 3 of same color
        for color in wool_colors:
            if self.inventory.count_item(context, f"minecraft:{color}_wool") >= 3:
                print(f"  Successfully gathered 3 {color}_wool")
                return True

        print("  Failed to get 3 wool of same color")
        return False

    def _gather_leather(self, context: ActionContext) -> bool:
        """Gather leather by hunting cows/sheep."""
        result = self.combat.hunt_passive_mobs(context, target_mobs=["cow", "sheep"], target_loot={"minecraft:leather": 4}, timeout=120)
        if result.success and self.inventory.count_item(context, "minecraft:leather") >= 4:
            print("  Successfully gathered leather")
            return True
        else:
            print(f"  Leather gathering failed: {result.message}")
            return False

    def _hunt_food(self, context: ActionContext, target_count: int) -> bool:
        """Hunt for food items."""
        result = self.combat.hunt_passive_mobs(context, target_count=target_count, timeout=300)
        if result.success and self._count_food_items(context) >= target_count:
            print(f"  Successfully hunted {target_count} food items")
            return True
        else:
            print(f"  Food hunting failed: {result.message}")
            return False

    def _has_bed_materials(self, context: ActionContext) -> bool:
        """Check if we have materials to craft a bed."""
        wool_colors = [
            "white", "black", "gray", "light_gray", "brown",
            "red", "orange", "yellow", "lime", "green",
            "cyan", "light_blue", "blue", "purple", "magenta", "pink"
        ]

        for color in wool_colors:
            if self.inventory.count_item(context, f"minecraft:{color}_wool") >= 3:
                return True
        return False

    def _count_food_items(self, context: ActionContext) -> int:
        """Count edible food items in inventory."""
        food_items = [
            "minecraft:apple", "minecraft:cooked_beef", "minecraft:beef",
            "minecraft:cooked_porkchop", "minecraft:porkchop",
            "minecraft:bread", "minecraft:wheat"
        ]

        total = 0
        for item in food_items:
            total += self.inventory.count_item(context, item)
        return total