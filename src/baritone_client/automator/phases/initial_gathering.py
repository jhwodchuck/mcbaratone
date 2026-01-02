"""
Initial Gathering Phase - Wood, stone, food, basic tools.
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import gather_wood, gather_stone, craft, find_item_slot, count_item, equip_best_weapon
from ...common.base import build_emergency_shelter, sleep_through_night
from ...common.combat import hunt_passive_mobs
from ...common.tasks import TaskResult, SequentialTask, ActionTask


def gather_leather(client) -> bool:
    """Gather leather by hunting cows/sheep."""
    print("Action: Gathering leather (Hunting cows/sheep)...")
    from ...common.combat import hunt_mobs
    from ...common.base import build_emergency_shelter, sleep_through_night
    import time
    
    while True:
        result = hunt_mobs(
            client,
            mob_types=["cow", "sheep"],
            required_loot={"minecraft:leather": 16},  # For 4 armor pieces, need about 16 leather
            search_radius=50,
            timeout=300,
            heal_threshold=5.0,
        )
        
        if result.success:
            return True
            
        if "Night detected" in result.reason:
            print("Action: Night detected during hunt! Surviving night...")
            if not sleep_through_night(client):
                print("No bed or sleep failed. Building emergency shelter...")
                build_emergency_shelter(client)
                time.sleep(10) # Wait for shelter build
                
            # Wait for morning loop or let re-entry handle it?
            # sleep_through_night waits if successful.
            # build_emergency_shelter waits?
            # We loop back to hunt_mobs which will check Time immediately and fail again if still Night?
            # We should wait until Morning.
            # Simple wait loop
            print("Waiting for morning...")
            for _ in range(60): # Wait up to 600s
                state = client.transport.dispatch("get_state", {})
                if state.get("world_time", 0) % 24000 < 1000:
                    print("Morning has broken!")
                    break
                time.sleep(10)
            continue
            
        return False


def craft_leather_armor(client) -> bool:
    """Craft leather armor pieces."""
    armor_pieces = [
        "minecraft:leather_helmet",
        "minecraft:leather_chestplate",
        "minecraft:leather_leggings",
        "minecraft:leather_boots"
    ]
    for piece in armor_pieces:
        if not craft(client, piece, 1):
            return False
    return True


class InitialGatheringHandler(PhaseHandler):
    """Handler for initial resource gathering phase using common library functions."""

    def get_name(self) -> str:
        return "Initial Gathering"

    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        """
        Gather initial resources following progression:
        1. Gather 16+ wood logs
        2. Craft wooden tools
        3. Mine 16+ cobblestone
        4. Craft stone tools
        5. Craft leather armor
        6. Hunt for 10+ food items
        """
        self.state = state
        
        # Ensure settings are applied (especially autoTool)
        settings = {
            "allowSprint": "true",
            "allowParkour": "true",
            "allowBreak": "true",
            "allowPlace": "true",
            "allowTool": "true",
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
        # If night falls and we have no bed, build emergency shelter
        if not sleep_through_night(client):
             # Sleep failed (no bed or not night). If night, build shelter.
             print("DEBUG: Sleep failed, checking time...")
             state_data = client.transport.dispatch("get_state", {})
             time_val = state_data.get("world_time", 0) % 24000
             print(f"DEBUG: world_time={time_val}")
             if time_val >= 13000:
                 print("Nightfall detected! Building Emergency Shelter...")
                 build_emergency_shelter(client)
                 # Wait for morning
                 import time
                 time.sleep(30) # Wait a bit
                 return TaskResult.fail("Built emergency shelter due to night")

        print("DEBUG: Creating task list...")

        # Define subtasks
        tasks = [
            ActionTask("Gather wood", gather_wood, count=16),
            ActionTask("Craft wooden tools", self._craft_wooden_tools),
            ActionTask("Mine stone", gather_stone, count=16),
            ActionTask("Craft stone tools", self._craft_stone_tools),
            ActionTask("Gather leather", gather_leather),
            ActionTask("Craft leather armor", craft_leather_armor),
            ActionTask("Hunt food", hunt_passive_mobs, target_count=10, timeout=300),
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
        """Finds or places a crafting table and opens it."""
        import time
        from ...common.inventory import count_item, craft
        
        print("  Setting up crafting table...")
        
        # 1. Try to find existing nearby
        find_result = client.transport.dispatch('find_blocks', {'blocks': ['minecraft:crafting_table'], 'radius': 10})
        if find_result.get('found'):
            blocks = find_result.get('blocks', [])
            if blocks:
                block = blocks[0]
                print(f"  Found existing crafting table at ({block.get('x')}, {block.get('y')}, {block.get('z')})")
                client.transport.dispatch('interact_block', {'x': block.get('x'), 'y': block.get('y'), 'z': block.get('z')})
                time.sleep(0.5)
                return True

        # 2. Check/Craft crafting table item
        if count_item(client, "minecraft:crafting_table") == 0:
             print("  Crafting crafting table item...")
             planks = sum(count_item(client, f"minecraft:{wood}_planks") for wood in ["oak", "spruce", "birch", "dark_oak", "acacia", "jungle", "mangrove", "cherry"])
             if planks < 4:
                 print("  Not enough planks for table, trying to craft planks...")
                 # Generic fallback
                 craft(client, "minecraft:spruce_planks", 1) 
                 craft(client, "minecraft:oak_planks", 1) 
             
             if not craft(client, "minecraft:crafting_table", 1):
                 print("  Failed to craft crafting table item")
             time.sleep(0.3)
             
        # 3. Place table
        inv = client.transport.dispatch('get_inventory', {})
        table_slot = None
        for item in inv.get('inventory', []):
            if item.get('id') == 'minecraft:crafting_table' and item.get('count', 0) > 0:
                table_slot = item.get('slot')
                break
        
        if table_slot is None:
            print("  No crafting table in inventory (unexpected)")
            return False

        # Select/Move to hotbar
        final_slot = 0
        if table_slot >= 9:
            print(f"DEBUG: Manually moving table from {table_slot} to 36 (Hotbar 0)")
            # Pickup from source (Protocol ID matches Internal ID for Main Inv 9-35)
            client.transport.dispatch('inventory_click', {'slot': table_slot, 'type': 'PICKUP', 'button': 0})
            time.sleep(0.3)
            # Pickup from target (Hotbar 0 -> Protocol 36)
            client.transport.dispatch('inventory_click', {'slot': 36, 'type': 'PICKUP', 'button': 0})
            time.sleep(0.3)
            # Place original item back in source
            client.transport.dispatch('inventory_click', {'slot': table_slot, 'type': 'PICKUP', 'button': 0})
            time.sleep(0.3)
            
            client.transport.dispatch('select_slot', {'slot': 0})
            final_slot = 0
        else:
            client.transport.dispatch('select_slot', {'slot': table_slot})
            final_slot = table_slot
        
        time.sleep(0.3)
        
        # Place
        state = client.transport.dispatch('get_state', {})
        pos = state.get('block_position', {})
        x = int(pos.get('x', 0))
        y = int(pos.get('y', 0))
        z = int(pos.get('z', 0))
        
        positions = [
            (x+1, y, z), (x-1, y, z), (x, y, z+1), (x, y, z-1),
            (x+2, y, z), (x-2, y, z), (x, y, z+2), (x, y, z-2),
             (x+1, y+1, z), (x-1, y+1, z)
        ]
        
        placed_pos = None
        for px, py, pz in positions:
            time.sleep(0.5)
            client.transport.dispatch("chat", {"message": f"Trying to place at {px},{py},{pz}"})
            
            inv_before = count_item(client, "minecraft:crafting_table")
            current_slot = find_item_slot(client, "minecraft:crafting_table")
            print(f"DEBUG: Tables before: {inv_before}, Slot: {current_slot}")
            
            place_result = client.transport.dispatch('place_block', {'x': px, 'y': py, 'z': pz})
            
            inv_after = count_item(client, "minecraft:crafting_table")
            print(f"DEBUG: Tables after: {inv_after}")
            
            if place_result.get('placed', False):
                placed_pos = (px, py, pz)
                break
            
            if 'error' in place_result:
                print(f"DEBUG: Place error at {px, py, pz}: {place_result.get('error')}")

            # Optimistic check
            check = client.transport.dispatch('get_block', {'x': px, 'y': py, 'z': pz})
            bid = check.get('id', '')
            print(f"DEBUG: Block at {px, py, pz} is '{bid}'")
            
            if 'crafting_table' in bid:
                 print(f"  Placement reported fail but found table at {px, py, pz}")
                 placed_pos = (px, py, pz)
                 break
                 
        if not placed_pos and inv_after < inv_before:
             print("DEBUG: Item consumed but placement not detected?")
        
        if not placed_pos:
            print("  Failed to place. Attempting to mine space...")
            client.transport.dispatch('mine', {'blocks': ['minecraft:stone', 'minecraft:dirt', 'minecraft:cobblestone', 'minecraft:deepslate', 'minecraft:diorite', 'minecraft:andesite', 'minecraft:granite', 'minecraft:tuff', 'minecraft:gravel'], 'quantity': 1})
            time.sleep(5.0)
            client.transport.dispatch('cancel', {})
            time.sleep(0.5)
            
            # Re-select table
            client.transport.dispatch('select_slot', {'slot': final_slot})
            time.sleep(0.3)
            
            # Retry placement
            state = client.transport.dispatch('get_state', {})
            pos = state.get('block_position', {})
            x, y, z = int(pos.get('x', 0)), int(pos.get('y', 0)), int(pos.get('z', 0))
            
            positions = [
                (x+1, y, z), (x-1, y, z), (x, y, z+1), (x, y, z-1),
                (x+2, y, z), (x-2, y, z), (x, y, z+2), (x, y, z-2),
                 (x+1, y+1, z), (x-1, y+1, z)
            ]
            
            for px, py, pz in positions:
                client.transport.dispatch("chat", {"message": f"Retry place at {px},{py},{pz}"})
                place_result = client.transport.dispatch('place_block', {'x': px, 'y': py, 'z': pz})
                if place_result.get('placed', False):
                    placed_pos = (px, py, pz)
                    break 
                
                check = client.transport.dispatch('get_block', {'x': px, 'y': py, 'z': pz})
                bid = check.get('id', '')
                print(f"DEBUG: Retry Block at {px, py, pz} is '{bid}'")
                
                if 'crafting_table' in bid:
                     print(f"  Placement reported fail but found table at {px, py, pz}")
                     placed_pos = (px, py, pz)
                     break
        
        if not placed_pos:
            print("  Failed to place crafting table")
            return False
            
        print(f"  Placed crafting table at {placed_pos}")
        if hasattr(self, "state"):
             self.state.add_location("crafting_table", placed_pos[0], placed_pos[1], placed_pos[2], tags=["setup"])
        time.sleep(0.3)
        
        # Open
        px, py, pz = placed_pos
        client.transport.dispatch('interact_block', {'x': px, 'y': py, 'z': pz})
        time.sleep(0.5)
        return True

    def _craft_wooden_tools(self, client) -> bool:
        """Craft wooden tools."""
        import time
        from ...common.inventory import count_item, craft
        
        if count_item(client, "minecraft:wooden_pickaxe") > 0:
            print("  Already have wooden pickaxe!")
            return True
            
        print("  Crafting planks...")
        craft(client, "minecraft:spruce_planks", 16)
        craft(client, "minecraft:oak_planks", 16)
        time.sleep(0.3)
        
        planks = sum(count_item(client, f"minecraft:{wood}_planks") for wood in ["oak", "spruce", "birch", "dark_oak", "acacia", "jungle", "mangrove", "cherry"])
        if planks < 8:
            print("  Not enough planks")
            return False

        print("  Crafting sticks...")
        craft(client, "minecraft:stick", 8)
        time.sleep(0.3)
        
        # We need state to record location, but we need to pass it from caller or use simpler signature
        # _ensure_crafting_table is helper. 
        # But ActionTask/SequentialTask doesn't easily inject state into _craft_wooden_tools unless we bind it.
        # InitialGatheringHandler method has access to self. 
        # We can store state on self during execute?
        if not self._ensure_crafting_table(client):
            return False
            
        print("  Crafting wooden pickaxe...")
        result = craft(client, "minecraft:wooden_pickaxe", 1)
        client.transport.dispatch("close_screen", {})
        return result

    def _craft_stone_tools(self, client) -> bool:
        """Craft stone tools."""
        from ...common.inventory import count_item, craft
        
        # Check existing
        need_pick = count_item(client, "minecraft:stone_pickaxe") == 0
        need_sword = count_item(client, "minecraft:stone_sword") == 0
        need_axe = count_item(client, "minecraft:stone_axe") == 0
        
        if not (need_pick or need_sword or need_axe):
             print("  Already have stone tools!")
             return True

        if not self._ensure_crafting_table(client):
            return False
            
        success = True
        if need_pick:
             print("  Crafting stone pickaxe...")
             if not craft(client, "minecraft:stone_pickaxe", 1): success = False
        if need_sword:
             print("  Crafting stone sword...")
             if not craft(client, "minecraft:stone_sword", 1): success = False
        if need_axe:
             print("  Crafting stone axe...")
             if not craft(client, "minecraft:stone_axe", 1): success = False
             
        client.transport.dispatch("close_screen", {})
        equip_best_weapon(client)
        return success
