"""
Tool progression actions for initial gathering phase.
"""

import time
from .base import BaseAction
from ..core.interfaces import ActionContext, ActionResult
from .crafting import CraftingAction
from .inventory import InventoryAction


class ToolProgressionAction(BaseAction):
    """Action for crafting tools in progression order as resources become available."""

    def __init__(self):
        self.crafting = CraftingAction()
        self.inventory = InventoryAction()

    def execute(self, context: ActionContext) -> ActionResult:
        """
        Craft tools in logical progression: wooden tools -> stone tools -> bed.
        """
        print("Action: Crafting tools in progression...")

        success = True

        # 1. Craft wooden tools if needed
        if not self._has_wooden_tools(context):
            print("  Crafting wooden tools...")
            if not self._craft_wooden_tools(context):
                success = False

        # 2. Craft stone pickaxe if needed
        if not self.inventory.count_item(context, "minecraft:stone_pickaxe"):
            print("  Crafting stone pickaxe...")
            if not self._craft_stone_pickaxe(context):
                success = False

        # 3. Craft remaining stone tools if needed
        if not self._has_all_stone_tools(context):
            print("  Crafting remaining stone tools...")
            if not self._craft_remaining_stone_tools(context):
                success = False

        # 4. Craft bed if we have wool
        if not self._has_bed(context):
            print("  Crafting bed...")
            if not self._craft_bed(context):
                # Bed is nice to have but not critical
                print("  Bed crafting failed, continuing...")

        if success:
            return ActionResult.ok("Tool progression complete")
        else:
            return ActionResult.fail("Failed to craft required tools")

    def _has_wooden_tools(self, context: ActionContext) -> bool:
        """Check if wooden tools are available."""
        return self.inventory.count_item(context, "minecraft:wooden_pickaxe") > 0

    def _has_all_stone_tools(self, context: ActionContext) -> bool:
        """Check if all basic stone tools are available."""
        stone_tools = ["minecraft:stone_pickaxe", "minecraft:stone_sword", "minecraft:stone_axe"]
        return all(self.inventory.count_item(context, tool) > 0 for tool in stone_tools)

    def _has_bed(self, context: ActionContext) -> bool:
        """Check if any bed is available."""
        bed_types = [
            "minecraft:white_bed", "minecraft:black_bed", "minecraft:gray_bed",
            "minecraft:light_gray_bed", "minecraft:brown_bed", "minecraft:red_bed",
            "minecraft:orange_bed", "minecraft:yellow_bed", "minecraft:lime_bed",
            "minecraft:green_bed", "minecraft:cyan_bed", "minecraft:light_blue_bed",
            "minecraft:blue_bed", "minecraft:purple_bed", "minecraft:magenta_bed",
            "minecraft:pink_bed"
        ]
        return any(self.inventory.count_item(context, bed) > 0 for bed in bed_types)

    def _craft_wooden_tools(self, context: ActionContext) -> bool:
        """Craft basic wooden tools."""
        # Ensure we have enough planks and sticks
        plank_types = ["oak", "spruce", "birch", "dark_oak", "acacia", "jungle", "mangrove", "cherry"]
        planks = sum(self.inventory.count_item(context, f"minecraft:{wood}_planks") for wood in plank_types)

        if planks < 12:
            print("    Not enough planks for wooden tools")
            return False

        sticks = self.inventory.count_item(context, "minecraft:stick")
        if sticks < 4:
            print("    Crafting sticks...")
            self.crafting.craft(context, "minecraft:stick", 4)
            time.sleep(0.3)

        # Ensure crafting table
        if not self.crafting.ensure_crafting_table(context):
            return False

        print("    Crafting wooden pickaxe...")
        result = self.crafting.craft(context, "minecraft:wooden_pickaxe", 1)
        context.client.transport.dispatch("close_screen", {})

        return result

    def _craft_stone_pickaxe(self, context: ActionContext) -> bool:
        """Craft stone pickaxe."""
        cobble = self.inventory.count_item(context, "minecraft:cobblestone")
        if cobble < 3:
            print("    Not enough cobblestone for stone pickaxe")
            return False

        if not self.crafting.ensure_crafting_table(context):
            return False

        print("    Crafting stone pickaxe...")
        result = self.crafting.craft(context, "minecraft:stone_pickaxe", 1)
        context.client.transport.dispatch("close_screen", {})

        return result

    def _craft_remaining_stone_tools(self, context: ActionContext) -> bool:
        """Craft remaining stone tools (sword and axe)."""
        cobble = self.inventory.count_item(context, "minecraft:cobblestone")
        if cobble < 4:  # Need 2 for sword + 3 for axe
            print("    Not enough cobblestone for remaining stone tools")
            return False

        sticks = self.inventory.count_item(context, "minecraft:stick")
        if sticks < 3:  # 1 for sword + 2 for axe
            print("    Not enough sticks for remaining stone tools")
            return False

        if not self.crafting.ensure_crafting_table(context):
            return False

        success = True

        # Craft stone sword if needed
        if not self.inventory.count_item(context, "minecraft:stone_sword"):
            print("    Crafting stone sword...")
            if not self.crafting.craft(context, "minecraft:stone_sword", 1):
                success = False

        # Craft stone axe if needed
        if not self.inventory.count_item(context, "minecraft:stone_axe"):
            print("    Crafting stone axe...")
            if not self.crafting.craft(context, "minecraft:stone_axe", 1):
                success = False

        context.client.transport.dispatch("close_screen", {})

        # Equip best weapon
        self.inventory.equip_best_weapon(context)

        return success

    def _craft_bed(self, context: ActionContext) -> bool:
        """Craft a bed from available wool."""
        wool_colors = [
            "white", "black", "gray", "light_gray", "brown",
            "red", "orange", "yellow", "lime", "green",
            "cyan", "light_blue", "blue", "purple", "magenta", "pink"
        ]

        for color in wool_colors:
            wool_id = f"minecraft:{color}_wool"
            bed_id = f"minecraft:{color}_bed"

            if self.inventory.count_item(context, wool_id) >= 3:
                print(f"    Found 3 {wool_id}, crafting {bed_id}...")
                if self.crafting.ensure_crafting_table(context):
                    result = self.crafting.craft(context, bed_id, 1)
                    if result:
                        context.client.transport.dispatch("close_screen", {})
                        print(f"    Successfully crafted {bed_id}")
                        return True
                break

        print("    No suitable wool found for bed crafting")
        return False