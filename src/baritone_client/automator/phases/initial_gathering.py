"""
Initial Gathering Phase - Wood, stone, food, basic tools.
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import gather_wood, gather_stone, find_item_slot, count_item, equip_best_weapon
from ...common.base import build_emergency_shelter, sleep_through_night
from ...common.combat import hunt_passive_mobs, hunt_mobs
from ...common.tasks import TaskResult, SequentialTask, ActionTask

# Modular Action Imports
from ...actions import (
    SequenceAction,
    WoodCollectionPhase,
    ToolProgressionPhase,
    StoneCollectionPhase,
    BedPreparationPhase,
    StorageSetupPhase,
    SurvivalPhase,
)
from ...core.interfaces import ActionContext


def gather_wool(client) -> bool:
    """Gather 3 wool of the SAME color efficiently."""
    print("Action: Gathering wool (Hunting sheep for bed)...")
    from ...common.inventory import count_item
    from ...common.combat import hunt_mobs
    
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
        # Standardize color name for some items if needed
        # (gray vs grey? Minecraft uses 'gray')
        c = count_item(client, id)
        current_wool_counts[color] = c
        if c >= 3:
            print(f"  Already have 3 {id}, skipping hunt.")
            return True
        if c > max_count:
            max_count = c
            best_color = color

    print(f"  Current best wool: {best_color} ({max_count}/3)")
    
    # Hunt animals for wool. prioritize the best color.
    # We include some alternatives in case the best one is rare nearby.
    result = hunt_mobs(
        client,
        mob_types=["sheep"],
        required_loot={f"minecraft:{best_color}_wool": 3},
        search_radius=120,
        timeout=180,
    )
    
    # Re-check all colors in case we got a different set of 3
    for color in wool_colors:
        if count_item(client, f"minecraft:{color}_wool") >= 3:
            print(f"  Wool hunt successful! Found 3 {color}_wool.")
            return True

    print("  Wool hunt failed to get 3 matching wool (No sheep found or split colors?)")
    return False


def gather_leather(client) -> bool:
    """Gather leather by hunting cows/sheep."""
    print("Action: Gathering leather (Hunting cows/sheep)...")
    from ...common.combat import hunt_mobs
    from ...common.base import build_emergency_shelter, sleep_through_night
    from ...common.inventory import count_item
    import time
    
    # Check if we already have enough leather (4 is enough for boots, our minimum goal)
    existing_leather = count_item(client, "minecraft:leather")
    if existing_leather >= 4:
        print(f"  Already have {existing_leather} leather, skipping hunt")
        return True
    
    # Shorter timeout, only need 4 leather for basic armor
    max_attempts = 2
    for attempt in range(max_attempts):
        print(f"  Hunt attempt {attempt+1}/{max_attempts}...")
        result = hunt_mobs(
            client,
            mob_types=["cow", "sheep"],
            required_loot={"minecraft:leather": 4},  # Reduced from 16 to 4 - just need boots
            search_radius=50,
            timeout=120,  # Reduced from 300 to 120
            heal_threshold=5.0,
        )
        
        if result.success:
            print(f"  Leather hunt successful! Kills: {result.data.get('kills', 0)}")
            return True
            
        if "Night detected" in result.reason:
            print("Action: Night detected during hunt! Surviving night...")
            if not sleep_through_night(client):
                print("No bed or sleep failed. Building emergency shelter...")
                build_emergency_shelter(client)
                time.sleep(10)
                
            print("Waiting for morning...")
            for _ in range(30):  # Wait up to 300s (reduced from 600)
                state = client.transport.dispatch("get_state", {})
                if state.get("world_time", 0) % 24000 < 1000:
                    print("Morning has broken!")
                    break
                time.sleep(10)
            continue
        
        # Check if we got some leather even if not full count
        current_leather = count_item(client, "minecraft:leather")
        if current_leather >= 4:
            print(f"  Have {current_leather} leather, good enough!")
            return True
            
        print(f"  Hunt failed: {result.reason}")
        
    # Even if we failed, don't block the whole phase
    current_leather = count_item(client, "minecraft:leather")
    print(f"  Final leather count: {current_leather}")
    return True # Always return True to avoid progression loops


def craft_leather_armor(client) -> bool:
    """Craft leather armor pieces."""
    armor_pieces = [
        "minecraft:leather_helmet",
        "minecraft:leather_chestplate",
        "minecraft:leather_leggings",
        "minecraft:leather_boots"
    ]
    for piece in armor_pieces:
        # Keeping craft() usage for simple inventory crafting not requiring table
        # We could use self.crafting.craft if passed context, but this is a standalone function used in task list.
        # ActionTask calls it with client.
        # We'd need to refactor it to use context if we want Actions here.
        # For POC, mixing is fine.
        from ...common.inventory import craft
        if not craft(client, piece, 1):
            print(f"  Failed to craft {piece} (Skipping)")
    return True


class InitialGatheringHandler(PhaseHandler):
    """Handler for initial resource gathering phase using common library functions."""

    def get_name(self) -> str:
        return "Initial Gathering"

    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        """
        Gather initial resources using modular action components.
        """
        self.state = state
        
        # Initialize Actions
        self.crafting = CraftingAction()
        self.combat = CombatAction()
        self.inventory = InventoryAction()
        self.movement = MovementAction()
        
        # Initialize Context
        self.context = ActionContext(client=client, state=state)
        
        # Ensure settings are applied (especially autoTool)
        settings = {
            "allowSprint": "true",
            "allowParkour": "true",
            "allowBreak": "true",
            "allowPlace": "true",
            "autoTool": "true",
        }
        client.mission.macro("bootstrap", {"settings": settings})

        print("DEBUG: Checking phase_ready_result...")
        ready = resources.phase_ready_result(Phase.INITIAL_GATHERING, "Initial gathering already satisfied")
        if ready:
            print("DEBUG: Phase already ready, returning early")
            return ready

        print("DEBUG: Checking sleep_through_night...")
        # 0. Safety Check
        if not sleep_through_night(client):
             print("DEBUG: Sleep failed, checking time...")
             # (Keeping existing night logic for now, using self.combat action where possible)
             pass

        print("DEBUG: Creating task list (Optimized Progression using Actions)...")

        # Define subtasks with optimized order
        tasks = [
            # 1. Start small: Get just enough wood for a pickaxe (4 logs = 16 planks -> table(4) + sticks(4) + pick(3))
            ActionTask("Gather minimal wood", gather_wood, count=4),
            ActionTask("Craft wooden tools", self._craft_wooden_tools),
            
            # 2. Upgrade ASAP: Get just enough stone for stone pickaxe (3 cobble)
            ActionTask("Mine minimal stone", gather_stone, count=3),
            ActionTask("Craft stone pickaxe", self._craft_stone_pickaxe_only),
            
            # 3. Bulk Gather Stone (Fast with Stone Pick)
            ActionTask("Mine bulk stone", gather_stone, count=64),
            
            # 4. Get remaining tools (Axe for wood, Sword for food)
            ActionTask("Craft remaining stone tools", self._craft_remaining_stone_tools),
            
            # 5. Safety Priority: Bed (Gather wool early)
            ActionTask("Gather wool for bed", gather_wool),
            ActionTask("Craft bed", self._craft_bed),

            # 6. Bulk Gather Wood (Fast with Stone Axe)
            ActionTask("Gather bulk wood", gather_wood, count=16),
            
            # 7. Setup Storage
            ActionTask("Setup storage", self._setup_storage),
            ActionTask("Deposit excess", self._deposit_excess),
            
            # 8. Armor & Food
            ActionTask("Gather leather", gather_leather),
            ActionTask("Craft leather armor", craft_leather_armor),
            ActionTask("Hunt food", lambda c: self.combat.hunt_passive_mobs(self.context, target_count=10, timeout=300)),
        ]

        # Execute sequentially
        sequential_task = SequentialTask("Initial Gathering", tasks)
        result = sequential_task.run(client)

        if result.success:
            resources.refresh_inventory()
            summary = resources.get_summary()
            return TaskResult.ok("Initial gathering complete", inventory=summary["inventory"])
        else:
            missing = resources.check_phase_requirements(Phase.INITIAL_GATHERING)
            return TaskResult.fail(f"Initial gathering failed: {result.reason}", missing=missing)

    def _ensure_crafting_table(self, client) -> bool:
        """Finds or places a crafting table and opens it (Using CraftingAction)."""
        return self.crafting.ensure_crafting_table(self.context)

    def _craft_wooden_tools(self, client) -> bool:
        """Craft wooden tools using Actions."""
        import time
        
        if self.inventory.count_item(self.context, "minecraft:wooden_pickaxe") > 0:
            print("  Already have wooden pickaxe!")
            return True
        
        # Check existing planks
        plank_types = ["oak", "spruce", "birch", "dark_oak", "acacia", "jungle", "mangrove", "cherry"]
        planks = sum(self.inventory.count_item(self.context, f"minecraft:{wood}_planks") for wood in plank_types)
        print(f"  Existing planks: {planks}")
        
        if planks < 12:
            needed_planks = 12 - planks
            logs_to_convert = (needed_planks + 3) // 4
            print(f"  Need {needed_planks} more planks, converting {logs_to_convert} logs...")
            
            self.crafting.craft(self.context, "minecraft:oak_planks", logs_to_convert)
            self.crafting.craft(self.context, "minecraft:spruce_planks", logs_to_convert)
            time.sleep(0.3)
        
        planks = sum(self.inventory.count_item(self.context, f"minecraft:{wood}_planks") for wood in plank_types)
        if planks < 9:
             print(f"  Still not enough planks ({planks})")
             return False

        sticks = self.inventory.count_item(self.context, "minecraft:stick")
        if sticks < 4:
            print(f"  Crafting sticks...")
            self.crafting.craft(self.context, "minecraft:stick", 4)
            time.sleep(0.3)
        
        if not self.crafting.ensure_crafting_table(self.context):
            return False
            
        print("  Crafting wooden pickaxe...")
        result = self.crafting.craft(self.context, "minecraft:wooden_pickaxe", 1)
        client.transport.dispatch("close_screen", {}) # Ensure screen closed
        return result

    def _craft_stone_pickaxe_only(self, client) -> bool:
        """Craft just the stone pickaxe using Actions."""
        if self.inventory.count_item(self.context, "minecraft:stone_pickaxe") > 0:
            return True
            
        if not self.crafting.ensure_crafting_table(self.context):
            return False
            
        print("  Crafting stone pickaxe...")
        result = self.crafting.craft(self.context, "minecraft:stone_pickaxe", 1)
        client.transport.dispatch("close_screen", {})
        return result

    def _craft_bed(self, client) -> bool:
        """Craft a bed (tries all wool colors) using Actions."""
        # Generic check for ANY bed
        beds = ["minecraft:white_bed", "minecraft:black_bed", "minecraft:gray_bed", "minecraft:light_gray_bed", "minecraft:brown_bed", "minecraft:red_bed"]
        for bed in beds:
            if self.inventory.count_item(self.context, bed) > 0:
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
            
            if self.inventory.count_item(self.context, wool_id) >= 3:
                print(f"  Found 3 {wool_id}, attempting to craft {bed_id}...")
                if self.crafting.ensure_crafting_table(self.context):
                    result = self.crafting.craft(self.context, bed_id, 1)
                    if result:
                        client.transport.dispatch("close_screen", {})
                        print(f"  Successfully crafted {bed_id}")
                        return True
        return False

    def _craft_remaining_stone_tools(self, client) -> bool:
        """Craft remaining stone tools using Actions."""
        import time
        
        need_sword = self.inventory.count_item(self.context, "minecraft:stone_sword") == 0
        need_axe = self.inventory.count_item(self.context, "minecraft:stone_axe") == 0
        
        needed_sticks = 0
        if need_sword: needed_sticks += 1
        if need_axe: needed_sticks += 2
        
        current_sticks = self.inventory.count_item(self.context, "minecraft:stick")
        if current_sticks < needed_sticks:
             print(f"  Not enough sticks (Have {current_sticks})")
             # Check planks
             plank_types = ["oak", "spruce", "birch", "dark_oak", "acacia", "jungle", "mangrove", "cherry"]
             planks = sum(self.inventory.count_item(self.context, f"minecraft:{wood}_planks") for wood in plank_types)
             
             if planks < 2:
                 self.crafting.craft(self.context, "minecraft:oak_planks", 1)
                 time.sleep(0.5)
             
             self.crafting.craft(self.context, "minecraft:stick", 4)
             time.sleep(0.5)

        if not (need_sword or need_axe):
             print("  Already have remaining stone tools!")
             return True

        if not self.crafting.ensure_crafting_table(self.context):
            return False
            
        success = True
        if need_sword:
             print("  Crafting stone sword...")
             if not self.crafting.craft(self.context, "minecraft:stone_sword", 1): success = False
        if need_axe:
             print("  Crafting stone axe...")
             if not self.crafting.craft(self.context, "minecraft:stone_axe", 1): success = False
             
        client.transport.dispatch("close_screen", {})
        self.inventory.equip_best_weapon(self.context)
        return success

    def _setup_storage(self, client) -> bool:
        """Craft/Place a chest and remember it."""
        from ...common.state import WorldState
        from ...common.inventory import count_item, craft, find_item_slot
        import time
        
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
            
            craft(client, "minecraft:oak_planks", 2)
            time.sleep(1.0)
            # Force refresh to ensure client knows about planks
            client.transport.dispatch("get_inventory", {})
            time.sleep(0.5)
            
        # Craft Chest
        if count_item(client, "minecraft:chest") == 0:
            if not self._ensure_crafting_table(client):
                print("  Warning: Failed to ensure crafting table for storage (Skipping - Non-fatal)")
                return True
            
            print("  Crafting chest...")
            if not craft(client, "minecraft:chest", 1):
                print("  Warning: Failed to craft chest (Skipping storage - Non-fatal)")
                client.transport.dispatch("close_screen", {})
                return True
            client.transport.dispatch("close_screen", {})
            time.sleep(0.5)

        # Place Chest
        # Find spot near player
        state = client.transport.dispatch('get_state', {})
        pos = state.get('block_position', {})
        x, y, z = int(pos.get('x', 0)), int(pos.get('y', 0)), int(pos.get('z', 0))
        
        chest_pos = None
        
        # Try a few spots
        for dx, dz in [(1,0), (-1,0), (0,1), (0,-1), (2,0), (-2,0), (0,2), (0,-2)]:
            tx, ty, tz = x+dx, y, z
            check = client.transport.dispatch('get_block', {'x': tx, 'y': ty, 'z': tz})
            bid = check.get('id', '')
            if 'air' in bid or 'grass' in bid:
                # Good spot
                slot = find_item_slot(client, "minecraft:chest")
                if slot is not None:
                    if slot >= 9:
                        client.transport.dispatch('select_slot', {'slot': 0})
                        client.transport.dispatch('inventory_click', {'slot': slot, 'type': 'PICKUP', 'button': 0})
                        time.sleep(0.1)
                        client.transport.dispatch('inventory_click', {'slot': 36, 'type': 'PICKUP', 'button': 0})
                        time.sleep(0.1)
                        client.transport.dispatch('inventory_click', {'slot': slot, 'type': 'PICKUP', 'button': 0})
                        client.transport.dispatch('select_slot', {'slot': 0})
                    else:
                        client.transport.dispatch('select_slot', {'slot': slot})
                        
                    time.sleep(0.3)
                    try:
                        client.transport.dispatch('place_block', {'x': tx, 'y': ty, 'z': tz})
                    except Exception as e:
                        print(f"  Placement error at {tx, ty, tz}: {e}")
                        continue
                    time.sleep(0.5)
                    # Verify
                    check = client.transport.dispatch('get_block', {'x': tx, 'y': ty, 'z': tz})
                    if 'chest' in check.get('id', ''):
                        chest_pos = (tx, ty, tz)
                        break
        
        if chest_pos:
            print(f"  Storage initialized at {chest_pos}")
            if hasattr(self, "state"):
                self.state.add_location("chest", chest_pos[0], chest_pos[1], chest_pos[2], tags=["storage"], client=client)
            
            # CRITICAL SAFETY: Blacklist chest so Baritone NEVER breaks it
            print("  Safeguard: Blacklisting chests from mining...")
            client.transport.dispatch("chat", {"message": "#blacklist minecraft:chest"})
            time.sleep(0.5)
            
            # Step away to ensure we aren't standing inside/on it
            print("  Stepping back from chest...")
            px, py, pz = chest_pos
            # Try to go to x-1 or x+1
            client.transport.dispatch("goto", {"x": px+1, "y": py, "z": pz})
            time.sleep(1.0)
            
            return True
            
        print("  Warning: Failed to place storage chest (Skipping - Non-fatal)")
        return True

    def _deposit_excess(self, client) -> bool:
        """Dump non-essential items to storage."""
        from ...common.inventory import dump_to_chest
        
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
