"""
Survival check actions for initial gathering phase.
"""

import time
from .base import BaseAction
from ..core.interfaces import ActionContext, ActionResult
from .crafting import CraftingAction
from .inventory import InventoryAction
from ..common.inventory import (
    count_item,
    craft,
    find_item_slot,
    dump_to_chest,
    persist_storage_location,
    resolve_storage_location,
)


class SurvivalCheckAction(BaseAction):
    """Action for handling survival needs like health, hunger, armor, and storage."""

    def __init__(self):
        self.crafting = CraftingAction()
        self.inventory = InventoryAction()

    def execute(self, context: ActionContext) -> ActionResult:
        """
        Perform survival checks and maintenance: health, armor, storage.
        """
        print("Action: Performing survival checks...")

        success = True

        # 1. Craft leather armor if we have leather
        if not self._has_basic_armor(context):
            print("  Crafting leather armor...")
            if not self._craft_leather_armor(context):
                print("  Armor crafting failed, continuing...")

        # 2. Setup storage system
        if not self._has_storage(context):
            print("  Setting up storage...")
            if not self._setup_storage(context):
                return ActionResult.fail("Storage setup failed")

        # 3. Deposit excess items to storage
        print("  Depositing excess items...")
        if not self._deposit_excess(context):
            return ActionResult.fail("Storage deposit could not be verified")

        # Health and hunger checks could be added here if needed
        # For now, the resource gathering and night survival handle the main threats

        return ActionResult.ok("Survival checks complete")

    def _has_basic_armor(self, context: ActionContext) -> bool:
        """Check if basic leather armor is equipped."""
        armor_pieces = ["minecraft:leather_helmet", "minecraft:leather_chestplate",
                       "minecraft:leather_leggings", "minecraft:leather_boots"]
        return any(self.inventory.count_item(context, piece) > 0 for piece in armor_pieces)

    def _has_storage(self, context: ActionContext) -> bool:
        """Check if storage system is already set up."""
        return resolve_storage_location(
            context.client, state=context.state, verify=True
        ) is not None

    def _craft_leather_armor(self, context: ActionContext) -> bool:
        """Craft leather armor pieces."""
        leather = self.inventory.count_item(context, "minecraft:leather")
        if leather < 5:  # Minimum for basic boots
            print("    Not enough leather for armor")
            return False

        armor_pieces = [
            ("minecraft:leather_boots", 4),      # Most important for thorns/etc
            ("minecraft:leather_helmet", 5),
            ("minecraft:leather_chestplate", 8),
            ("minecraft:leather_leggings", 7)
        ]

        success = True
        for piece, required_leather in armor_pieces:
            if self.inventory.count_item(context, piece) > 0:
                continue  # Already have this piece

            if leather >= required_leather:
                print(f"    Crafting {piece}...")
                if not craft(context.client, piece, 1):
                    print(f"    Failed to craft {piece}")
                    success = False
            else:
                print(f"    Not enough leather for {piece} ({leather}/{required_leather})")

        return success

    def _setup_storage(self, context: ActionContext) -> bool:
        """Craft and place a chest for storage."""
        # Check if we already have a chest
        if self.inventory.count_item(context, "minecraft:chest") == 0:
            # Need to craft a chest - requires 8 planks
            plank_types = ["oak", "spruce", "birch", "dark_oak", "acacia", "jungle", "mangrove", "cherry"]
            planks = sum(count_item(context.client, f"minecraft:{wood}_planks") for wood in plank_types)

            if planks < 8:
                # Convert logs to planks
                logs = sum(count_item(context.client, f"minecraft:{wood}_log") for wood in ["oak", "spruce", "birch"])
                if logs == 0:
                    print("    No logs available to craft chest")
                    return False

                craft(context.client, "minecraft:oak_planks", 2)
                context.client.transport.dispatch("get_inventory", {})
                time.sleep(0.5)

            if not self.crafting.ensure_crafting_table(context):
                print("    Cannot craft chest without crafting table")
                return False

            print("    Crafting chest...")
            if not craft(context.client, "minecraft:chest", 1):
                print("    Failed to craft chest")
                return False
            context.client.transport.dispatch("close_screen", {})
            time.sleep(0.5)

        # Find a spot and place the chest
        state = context.client.transport.dispatch('get_state', {})
        pos = state.get('block_position', {})
        x, y, z = int(pos.get('x', 0)), int(pos.get('y', 0)), int(pos.get('z', 0))

        chest_pos = None
        for dx, dz in [(1,0), (-1,0), (0,1), (0,-1), (2,0), (-2,0), (0,2), (0,-2)]:
            tx, ty, tz = x+dx, y, z+dz
            check = context.client.transport.dispatch('get_block', {'x': tx, 'y': ty, 'z': tz})
            bid = check.get('id', '')
            if 'air' in bid or 'grass' in bid:
                # Try to place chest
                slot = find_item_slot(context.client, "minecraft:chest")
                if slot is not None:
                    if slot >= 9:
                        context.client.transport.dispatch('select_slot', {'slot': 0})
                        context.client.transport.dispatch('inventory_click', {'slot': slot, 'type': 'PICKUP', 'button': 0})
                        time.sleep(0.1)
                        context.client.transport.dispatch('inventory_click', {'slot': 36, 'type': 'PICKUP', 'button': 0})
                        time.sleep(0.1)
                        context.client.transport.dispatch('inventory_click', {'slot': slot, 'type': 'PICKUP', 'button': 0})
                        context.client.transport.dispatch('select_slot', {'slot': 0})
                    else:
                        context.client.transport.dispatch('select_slot', {'slot': slot})

                    time.sleep(0.3)
                    try:
                        context.client.transport.dispatch(
                            'place_block',
                            {'x': tx, 'y': ty, 'z': tz, 'block': 'minecraft:chest'}
                        )
                    except Exception as e:
                        continue

                    time.sleep(0.5)
                    check = context.client.transport.dispatch('get_block', {'x': tx, 'y': ty, 'z': tz})
                    if 'chest' in check.get('id', ''):
                        chest_pos = (tx, ty, tz)
                        break

        if chest_pos:
            print(f"    Storage initialized at {chest_pos}")
            if not persist_storage_location(
                context.client, chest_pos, state=context.state
            ):
                return False

            # Blacklist chest from mining
            context.client.transport.dispatch("chat", {"message": "#blacklist minecraft:chest"})
            time.sleep(0.5)

            # Step away from chest
            px, py, pz = chest_pos
            context.client.transport.dispatch("goto", {"x": px+1, "y": py, "z": pz})
            time.sleep(1.0)

            return True

        print("    Failed to place storage chest")
        return False

    def _deposit_excess(self, context: ActionContext) -> bool:
        """Deposit non-essential items to storage."""
        keep_items = [
            # Tools
            "minecraft:wooden_pickaxe", "minecraft:stone_pickaxe",
            "minecraft:stone_sword", "minecraft:stone_axe",
            "minecraft:crafting_table", "minecraft:furnace",
            # Resources
            "minecraft:coal", "minecraft:stick", "minecraft:torch",
            # Wood/Stone (keep some)
            "minecraft:oak_log", "minecraft:cobblestone",
            "minecraft:oak_planks",
            # Food
            "minecraft:apple", "minecraft:cooked_beef", "minecraft:beef",
            "minecraft:cooked_porkchop", "minecraft:porkchop",
            "minecraft:bread", "minecraft:wheat",
            # Armor
            "minecraft:leather_helmet", "minecraft:leather_chestplate",
            "minecraft:leather_leggings", "minecraft:leather_boots",
            # Other
            "minecraft:chest", "minecraft:white_bed"
        ]

        print("    Depositing excess items to storage...")
        return dump_to_chest(
            context.client,
            keep_items=keep_items,
            state=context.state,
        ) >= 0
