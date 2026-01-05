"""
Action components for initial gathering phases.
"""

from typing import List
from ..core.interfaces import ActionContext, ActionResult
from ..actions.base import BaseAction
from ..actions.crafting import CraftingAction
from ..actions.inventory import InventoryAction
from ..common import gather_wood, gather_stone
from ..common.combat import hunt_passive_mobs


class WoodCollectionPhase(BaseAction):
    """Handle wood gathering for initial tools and bulk collection."""

    def execute(self, context: ActionContext) -> ActionResult:
        """Gather wood in phases: minimal for tools, then bulk."""
        client = context.client

        # Phase 1: Minimal wood for tools (4 logs = planks for table + tools)
        print("Phase: Gathering minimal wood for tools...")
        if not gather_wood(client, count=4):
            return ActionResult.fail("Failed to gather minimal wood for tools")

        # Phase 2: Bulk wood gathering (now with stone axe)
        print("Phase: Gathering bulk wood...")
        if not gather_wood(client, count=16):
            return ActionResult.fail("Failed to gather bulk wood")

        return ActionResult.ok("Wood collection completed")


class ToolProgressionPhase(BaseAction):
    """Handle crafting progression: wooden tools -> stone pickaxe -> stone tools."""

    def __init__(self):
        self.crafting = CraftingAction()
        self.inventory = InventoryAction()

    def execute(self, context: ActionContext) -> ActionResult:
        """Craft tools in progression order."""
        # Craft wooden tools
        if not self._craft_wooden_tools(context):
            return ActionResult.fail("Failed to craft wooden tools")

        # Craft stone pickaxe
        if not self._craft_stone_pickaxe(context):
            return ActionResult.fail("Failed to craft stone pickaxe")

        # Craft remaining stone tools
        if not self._craft_remaining_stone_tools(context):
            return ActionResult.fail("Failed to craft remaining stone tools")

        return ActionResult.ok("Tool progression completed")

    def _craft_wooden_tools(self, context: ActionContext) -> bool:
        """Craft wooden tools using existing logic."""
        if self.inventory.count_item(context, "minecraft:wooden_pickaxe") > 0:
            print("  Already have wooden pickaxe!")
            return True

        # Check existing planks
        plank_types = ["oak", "spruce", "birch", "dark_oak", "acacia", "jungle", "mangrove", "cherry"]
        planks = sum(self.inventory.count_item(context, f"minecraft:{wood}_planks") for wood in plank_types)
        print(f"  Existing planks: {planks}")

        if planks < 12:
            needed_planks = 12 - planks
            logs_to_convert = (needed_planks + 3) // 4
            print(f"  Need {needed_planks} more planks, converting {logs_to_convert} logs...")

            self.crafting.craft(context, "minecraft:oak_planks", logs_to_convert)

        planks = sum(self.inventory.count_item(context, f"minecraft:{wood}_planks") for wood in plank_types)
        if planks < 9:
            print(f"  Still not enough planks ({planks})")
            return False

        sticks = self.inventory.count_item(context, "minecraft:stick")
        if sticks < 4:
            print(f"  Crafting sticks...")
            self.crafting.craft(context, "minecraft:stick", 4)

        if not self.crafting.ensure_crafting_table(context):
            return False

        print("  Crafting wooden pickaxe...")
        result = self.crafting.craft(context, "minecraft:wooden_pickaxe", 1)
        context.client.transport.dispatch("close_screen", {})
        return result

    def _craft_stone_pickaxe(self, context: ActionContext) -> bool:
        """Craft stone pickaxe."""
        if self.inventory.count_item(context, "minecraft:stone_pickaxe") > 0:
            return True

        if not self.crafting.ensure_crafting_table(context):
            return False

        print("  Crafting stone pickaxe...")
        result = self.crafting.craft(context, "minecraft:stone_pickaxe", 1)
        context.client.transport.dispatch("close_screen", {})
        return result

    def _craft_remaining_stone_tools(self, context: ActionContext) -> bool:
        """Craft remaining stone tools: sword and axe."""
        need_sword = self.inventory.count_item(context, "minecraft:stone_sword") == 0
        need_axe = self.inventory.count_item(context, "minecraft:stone_axe") == 0

        needed_sticks = 0
        if need_sword: needed_sticks += 1
        if need_axe: needed_sticks += 2

        current_sticks = self.inventory.count_item(context, "minecraft:stick")
        if current_sticks < needed_sticks:
            print(f"  Not enough sticks (Have {current_sticks})")
            # Check planks
            plank_types = ["oak", "spruce", "birch", "dark_oak", "acacia", "jungle", "mangrove", "cherry"]
            planks = sum(self.inventory.count_item(context, f"minecraft:{wood}_planks") for wood in plank_types)

            if planks < 2:
                self.crafting.craft(context, "minecraft:oak_planks", 1)

            self.crafting.craft(context, "minecraft:stick", 4)

        if not (need_sword or need_axe):
            print("  Already have remaining stone tools!")
            return True

        if not self.crafting.ensure_crafting_table(context):
            return False

        success = True
        if need_sword:
            print("  Crafting stone sword...")
            if not self.crafting.craft(context, "minecraft:stone_sword", 1): success = False
        if need_axe:
            print("  Crafting stone axe...")
            if not self.crafting.craft(context, "minecraft:stone_axe", 1): success = False

        context.client.transport.dispatch("close_screen", {})
        self.inventory.equip_best_weapon(context)
        return success


class StoneCollectionPhase(BaseAction):
    """Handle stone gathering: minimal for pickaxe, then bulk."""

    def execute(self, context: ActionContext) -> ActionResult:
        """Gather stone in phases."""
        client = context.client

        # Phase 1: Minimal stone for stone pickaxe
        print("Phase: Gathering minimal stone for pickaxe...")
        if not gather_stone(client, count=3):
            return ActionResult.fail("Failed to gather minimal stone")

        # Phase 2: Bulk stone gathering (with stone pick)
        print("Phase: Gathering bulk stone...")
        if not gather_stone(client, count=64):
            return ActionResult.fail("Failed to gather bulk stone")

        return ActionResult.ok("Stone collection completed")


class BedPreparationPhase(BaseAction):
    """Handle bed preparation: gather wool and craft bed."""

    def __init__(self):
        self.crafting = CraftingAction()
        self.inventory = InventoryAction()

    def execute(self, context: ActionContext) -> ActionResult:
        """Gather wool and craft bed."""
        # Gather wool using hunt_mobs logic adapted
        if not self._gather_wool(context):
            return ActionResult.fail("Failed to gather wool for bed")

        # Craft bed
        if not self._craft_bed(context):
            return ActionResult.fail("Failed to craft bed")

        return ActionResult.ok("Bed preparation completed")

    def _gather_wool(self, context: ActionContext) -> bool:
        """Gather 3 wool of the same color efficiently."""
        client = context.client
        print("Action: Gathering wool (Hunting sheep for bed)...")

        # Minecraft beds require 3 wool of the same color.
        wool_colors = [
            "white", "black", "gray", "light_gray", "brown",
            "red", "orange", "yellow", "lime", "green",
            "cyan", "light_blue", "blue", "purple", "magenta", "pink"
        ]

        current_wool_counts = {}
        best_color = "white"
        max_count = 0

        for color in wool_colors:
            id = f"minecraft:{color}_wool"
            c = self.inventory.count_item(context, id)
            current_wool_counts[color] = c
            if c >= 3:
                print(f"  Already have 3 {id}, skipping hunt.")
                return True
            if c > max_count:
                max_count = c
                best_color = color

        print(f"  Current best wool: {best_color} ({max_count}/3)")

        # Hunt sheep for wool
        from ..common.combat import hunt_mobs
        result = hunt_mobs(
            client,
            mob_types=["sheep"],
            required_loot={f"minecraft:{best_color}_wool": 3},
            search_radius=120,
            timeout=180,
        )

        # Re-check all colors in case we got a different set of 3
        for color in wool_colors:
            if self.inventory.count_item(context, f"minecraft:{color}_wool") >= 3:
                print(f"  Wool hunt successful! Found 3 {color}_wool.")
                return True

        print("  Wool hunt failed to get 3 matching wool")
        return False

    def _craft_bed(self, context: ActionContext) -> bool:
        """Craft a bed (tries all wool colors)."""
        # Generic check for ANY bed
        beds = ["minecraft:white_bed", "minecraft:black_bed", "minecraft:gray_bed", "minecraft:light_gray_bed", "minecraft:brown_bed", "minecraft:red_bed"]
        for bed in beds:
            if self.inventory.count_item(context, bed) > 0:
                print(f"  Already have bed ({bed})")
                return True

        wool_colors = [
            "white", "black", "gray", "light_gray", "brown",
            "red", "orange", "yellow", "lime", "green",
            "cyan", "light_blue", "blue", "purple", "magenta", "pink"
        ]

        for color in wool_colors:
            bed_id = f"minecraft:{color}_bed"
            wool_id = f"minecraft:{color}_wool"

            if self.inventory.count_item(context, wool_id) >= 3:
                print(f"  Found 3 {wool_id}, attempting to craft {bed_id}...")
                if self.crafting.ensure_crafting_table(context):
                    result = self.crafting.craft(context, bed_id, 1)
                    if result:
                        context.client.transport.dispatch("close_screen", {})
                        print(f"  Successfully crafted {bed_id}")
                        return True
        return False


class SurvivalPhase(BaseAction):
    """Handle survival essentials: leather, armor, food."""

    def __init__(self):
        self.inventory = InventoryAction()

    def execute(self, context: ActionContext) -> ActionResult:
        """Gather leather, craft armor, hunt food."""
        # Gather leather
        if not self._gather_leather(context):
            # Not critical, continue
            print("Leather gathering failed, continuing...")

        # Craft leather armor
        self._craft_leather_armor(context)

        # Hunt food
        if not self._hunt_food(context):
            return ActionResult.fail("Failed to hunt food")

        return ActionResult.ok("Survival phase completed")


class StorageSetupPhase(BaseAction):
    """Handle storage setup: craft/place chest and deposit excess items."""

    def __init__(self):
        self.crafting = CraftingAction()
        self.inventory = InventoryAction()

    def execute(self, context: ActionContext) -> ActionResult:
        """Setup storage and deposit excess items."""
        client = context.client

        # Setup storage
        if not self._setup_storage(context):
            # Non-fatal, continue
            print("Storage setup failed, continuing...")

        # Deposit excess
        self._deposit_excess(context)

        return ActionResult.ok("Storage setup completed")

    def _setup_storage(self, context: ActionContext) -> bool:
        """Craft/Place a chest and remember it."""
        client = context.client
        from ..common.state import WorldState
        from ..common.inventory import count_item, craft, find_item_slot

        ws = WorldState(client)
        if ws.load_checkpoint("storage"):
            print("  Storage location already known.")
            return True

        print("  Setting up storage system...")

        # Ensure planks (8 needed)
        planks = sum(count_item(client, f"minecraft:{wood}_planks") for wood in ["oak", "spruce", "birch", "dark_oak", "acacia", "jungle", "mangrove", "cherry"])
        if planks < 8:
            print("  Not enough planks for chest, converting logs...")
            # Check if we have logs!
            logs = sum(count_item(client, block) for block in ["minecraft:oak_log", "minecraft:spruce_log", "minecraft:birch_log", "minecraft:jungle_log", "minecraft:acacia_log", "minecraft:dark_oak_log", "minecraft:mangrove_log", "minecraft:cherry_log"])
            if logs == 0:
                print("  No logs to convert to planks!")
                return False

            self.crafting.craft(context, "minecraft:oak_planks", 2)

            # Force refresh
            client.transport.dispatch("get_inventory", {})

        # Craft Chest
        if count_item(client, "minecraft:chest") == 0:
            if not self.crafting.ensure_crafting_table(context):
                print("  Warning: Failed to ensure crafting table for storage (Skipping - Non-fatal)")
                return True

            print("  Crafting chest...")
            if not self.crafting.craft(context, "minecraft:chest", 1):
                print("  Warning: Failed to craft chest (Skipping storage - Non-fatal)")
                client.transport.dispatch("close_screen", {})
                return True
            client.transport.dispatch("close_screen", {})

        # Place Chest
        # Find spot near player
        state = client.transport.dispatch('get_state', {})
        pos = state.get('block_position', {})
        x, y, z = int(pos.get('x', 0)), int(pos.get('y', 0)), int(pos.get('z', 0))

        chest_pos = None

        # Try a few spots
        for dx, dz in [(1,0), (-1,0), (0,1), (0,-1), (2,0), (-2,0), (0,2), (0,-2)]:
            tx, ty, tz = x+dx, y, z+dz
            check = client.transport.dispatch('get_block', {'x': tx, 'y': ty, 'z': tz})
            bid = check.get('id', '')
            if 'air' in bid or 'grass' in bid:
                # Good spot
                slot = find_item_slot(client, "minecraft:chest")
                if slot is not None:
                    if slot >= 9:
                        client.transport.dispatch('select_slot', {'slot': 0})
                        client.transport.dispatch('inventory_click', {'slot': slot, 'type': 'PICKUP', 'button': 0})
                        client.transport.dispatch('inventory_click', {'slot': 36, 'type': 'PICKUP', 'button': 0})
                        client.transport.dispatch('inventory_click', {'slot': slot, 'type': 'PICKUP', 'button': 0})
                        client.transport.dispatch('select_slot', {'slot': 0})
                    else:
                        client.transport.dispatch('select_slot', {'slot': slot})

                    try:
                        client.transport.dispatch('place_block', {'x': tx, 'y': ty, 'z': tz})
                    except Exception as e:
                        print(f"  Placement error at {tx, ty, tz}: {e}")
                        continue

                    # Verify
                    check = client.transport.dispatch('get_block', {'x': tx, 'y': ty, 'z': tz})
                    if 'chest' in check.get('id', ''):
                        chest_pos = (tx, ty, tz)
                        break

        if chest_pos:
            print(f"  Storage initialized at {chest_pos}")
            if hasattr(context, "state") and context.state:
                context.state.add_location("chest", chest_pos[0], chest_pos[1], chest_pos[2], tags=["storage"], client=client)

            # Safeguard: Blacklist chests from mining
            print("  Safeguard: Blacklisting chests from mining...")
            client.transport.dispatch("chat", {"message": "#blacklist minecraft:chest"})

            # Step away
            print("  Stepping back from chest...")
            px, py, pz = chest_pos
            client.transport.dispatch("goto", {"x": px+1, "y": py, "z": pz})

            return True

        print("  Warning: Failed to place storage chest (Skipping - Non-fatal)")
        return True

    def _deposit_excess(self, context: ActionContext) -> bool:
        """Dump non-essential items to storage."""
        client = context.client
        from ..common.inventory import dump_to_chest

        # Keep essentials
        keep = [
            # Tools
            "minecraft:wooden_pickaxe", "minecraft:stone_pickaxe",
            "minecraft:stone_sword", "minecraft:stone_axe",
            "minecraft:crafting_table", "minecraft:furnace",
            # Resources
            "minecraft:coal", "minecraft:stick", "minecraft:torch",
            # Wood/Stone (keep some for building/crafting)
            "minecraft:oak_log", "minecraft:cobblestone",
            "minecraft:oak_planks",
            # Food
            "minecraft:apple", "minecraft:cooked_beef", "minecraft:beef",
            "minecraft:cooked_porkchop", "minecraft:porkchop",
            "minecraft:bread", "minecraft:wheat"
        ]

        print("  Depositing excess items to storage...")
        dump_to_chest(client, keep_items=keep)
        return True

    def _gather_leather(self, context: ActionContext) -> bool:
        """Gather leather by hunting cows/sheep."""
        client = context.client
        print("Action: Gathering leather (Hunting cows/sheep)...")

        # Check if we already have enough leather (4 is enough for boots)
        existing_leather = self.inventory.count_item(context, "minecraft:leather")
        if existing_leather >= 4:
            print(f"  Already have {existing_leather} leather, skipping hunt")
            return True

        # Hunt mobs for leather
        from ..common.combat import hunt_mobs
        import time

        max_attempts = 2
        for attempt in range(max_attempts):
            print(f"  Hunt attempt {attempt+1}/{max_attempts}...")
            result = hunt_mobs(
                client,
                mob_types=["cow", "sheep"],
                required_loot={"minecraft:leather": 4},
                search_radius=50,
                timeout=120,
                heal_threshold=5.0,
            )

            if result.success:
                print(f"  Leather hunt successful! Kills: {result.data.get('kills', 0)}")
                return True

            if "Night detected" in result.reason:
                print("Action: Night detected during hunt! Surviving night...")
                from ..common.base import sleep_through_night
                if not sleep_through_night(client):
                    from ..common.base import build_emergency_shelter
                    print("No bed or sleep failed. Building emergency shelter...")
                    build_emergency_shelter(client)
                    time.sleep(10)

                print("Waiting for morning...")
                for _ in range(30):
                    state = client.transport.dispatch("get_state", {})
                    if state.get("world_time", 0) % 24000 < 1000:
                        print("Morning has broken!")
                        break
                    time.sleep(10)
                continue

            # Check if we got some leather even if not full count
            current_leather = self.inventory.count_item(context, "minecraft:leather")
            if current_leather >= 4:
                print(f"  Have {current_leather} leather, good enough!")
                return True

            print(f"  Hunt failed: {result.reason}")

        # Even if we failed, don't block the whole phase
        current_leather = self.inventory.count_item(context, "minecraft:leather")
        print(f"  Final leather count: {current_leather}")
        return True  # Always return True to avoid progression loops

    def _craft_leather_armor(self, context: ActionContext) -> bool:
        """Craft leather armor pieces."""
        armor_pieces = [
            "minecraft:leather_helmet",
            "minecraft:leather_chestplate",
            "minecraft:leather_leggings",
            "minecraft:leather_boots"
        ]
        for piece in armor_pieces:
            # Using craft function for simplicity
            from ..common.inventory import craft
            craft(context.client, piece, 1)
        return True

    def _hunt_food(self, context: ActionContext) -> bool:
        """Hunt passive mobs for food."""
        from ..actions.combat import CombatAction
        combat = CombatAction()
        result = combat.hunt_passive_mobs(context, target_count=10, timeout=300)
        return result.success