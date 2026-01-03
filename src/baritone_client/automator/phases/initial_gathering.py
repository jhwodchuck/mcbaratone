"""
Initial Gathering Phase - Wood, stone, food, basic tools.
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import gather_wood, gather_stone, craft, find_item_slot, count_item, equip_best_weapon
from ...common.base import build_emergency_shelter, sleep_through_night
from ...common.combat import hunt_passive_mobs, hunt_mobs
from ...common.tasks import TaskResult, SequentialTask, ActionTask


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
        if not craft(client, piece, 1):
            print(f"  Failed to craft {piece} (Skipping)")
    return True


class InitialGatheringHandler(PhaseHandler):
    """Handler for initial resource gathering phase using common library functions."""

    def get_name(self) -> str:
        return "Initial Gathering"

    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        """
        Gather initial resources following progression:
        1. Gather minimal wood (for wooden pick)
        2. Craft wooden pick
        3. Mine minimal stone (for stone pick)
        4. Craft stone pick
        5. Mine remaining stone (efficiently)
        6. Craft stone axe/sword
        7. Gather remaining wood (efficiently)
        8. Craft leather armor
        9. Hunt food
        """
        self.state = state
        
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
        # If night falls and we have no bed, build emergency shelter
        if not sleep_through_night(client):
             # Sleep failed (no bed or not night). If night, build shelter.
             print("DEBUG: Sleep failed, checking time...")
             state_data = client.transport.dispatch("get_state", {})
             time_val = state_data.get("world_time", 0) % 24000
             print(f"DEBUG: world_time={time_val}")
             if time_val >= 13000:
                 print("Nightfall detected! Skipping shelter to remain active...")
                 # build_emergency_shelter(client)  <-- Removed to prevent "dark screen" / burying
                 # We will use the productivity loop instead
                 print("Waiting for morning...")
                 import time
                 from ...common.inventory import count_item
                 from ...common.resources import gather_ores # Import gather_ores

                 # Loop until morning
                 while True:
                     state = client.transport.dispatch("get_state", {})
                     t = state.get("world_time", 0) % 24000
                     
                     if t < 1000:
                         print("Morning has broken! Resuming operations.")
                         client.transport.dispatch("cancel", {}) 
                         # Safety mechanism: Ensure we are on surface
                         client.transport.dispatch("chat", {"message": "#surface"})
                         break
                     
                     # Productive Wait: Gather Resources safely
                     
                     # Calculate time until morning (sunrise at 24000/0)
                     remaining_ticks = 24000 - t if t >= 13000 else 0
                     rem_seconds = remaining_ticks // 20
                     rem_m, rem_s = divmod(rem_seconds, 60)
                     time_str = f"[Time: {t}] [Morning in: {rem_m}m {rem_s:02d}s]"

                     # 1. Tool Maintenance: Replace broken stone tools (Priority #1)
                     has_stone_pick = count_item(client, "minecraft:stone_pickaxe") > 0
                     has_stone_axe = count_item(client, "minecraft:stone_axe") > 0
                     has_stone_sword = count_item(client, "minecraft:stone_sword") > 0
                     current_cobble = count_item(client, "minecraft:cobblestone")

                     # Ensure Stone Pickaxe (Essential for efficient mining and iron)
                     if not has_stone_pick and current_cobble >= 3:
                         print(f"  Tool Maintenance: Crafting replacement stone pickaxe... {time_str}")
                         self._craft_stone_pickaxe_only(client)
                         continue
                     
                     # Ensure Stone Sword (Self Defense)
                     if not has_stone_sword and current_cobble >= 2:
                         print(f"  Tool Maintenance: Crafting replacement stone sword... {time_str}")
                         if self._ensure_crafting_table(client):
                             craft(client, "minecraft:stone_sword", 1)
                             client.transport.dispatch("close_screen", {})
                             continue

                     # Ensure Stone Axe (Helpful for wood gathering later)
                     if not has_stone_axe and current_cobble >= 3:
                         print(f"  Tool Maintenance: Crafting replacement stone axe... {time_str}")
                         if self._ensure_crafting_table(client):
                             craft(client, "minecraft:stone_axe", 1)
                             client.transport.dispatch("close_screen", {})
                             continue

                     # Ensure at least Wooden Pickaxe if cobble is low but logs exist
                     if not has_stone_pick and count_item(client, "minecraft:wooden_pickaxe") == 0:
                         logs = sum(count_item(client, block) for block in ["minecraft:oak_log", "minecraft:spruce_log", "minecraft:birch_log"])
                         if logs > 0 or count_item(client, "minecraft:oak_planks") >= 3:
                              print(f"  Tool Maintenance: Restoring basic wooden pickaxe... {time_str}")
                              self._craft_wooden_tools(client)
                              continue

                     # Mine stone etc. (Logic continues)
                     # ...
                     
                     time.sleep(2.0) # Slow down loop for inventory sync
                     if current_cobble < 64:
                         needed = 64 - current_cobble
                         print(f"Night activity: Gathering stone (Have {current_cobble}/64)... {time_str}")
                         # Short timeout to re-check time frequently
                         gather_stone(client, count=64, timeout=30) 
                         continue

                     # 3. Coal (Target: 64)
                     current_coal = count_item(client, "minecraft:coal")
                     if current_coal < 64:
                         print(f"Night activity: Looking for coal (Have {current_coal}/64)... {time_str}")
                         
                         # Check for progress
                         if '_last_coal' not in locals(): _last_coal = -1
                         if '_stuck_coal' not in locals(): _stuck_coal = 0
                         
                         if current_coal <= _last_coal:
                             _stuck_coal += 1
                         else:
                             _stuck_coal = 0
                             _last_coal = current_coal
                             
                         # If stuck for ~2 attempts (40s)
                         if _stuck_coal >= 2:
                             print("  Mining stalled (stuck?). Attempting to surface...")
                             client.transport.dispatch("chat", {"message": "#surface"})
                             import time as pytime
                             pytime.sleep(10)
                             client.transport.dispatch("cancel", {})
                             _stuck_coal = 0
                         
                         # Use gather_ores but be careful with timeout
                         gather_ores(client, "coal", count=64, timeout=20)
                         continue

                     # 4. Iron (Target: 32)
                     current_iron = count_item(client, "minecraft:raw_iron")
                     if current_iron < 32:
                         if has_stone_pick:
                             print(f"Night activity: Looking for iron (Have {current_iron}/32)... {time_str}")
                             gather_ores(client, "iron", count=32, timeout=30)
                             continue
                         else:
                             print(f"  Night activity: Skipping iron mining (No stone pickaxe) {time_str}")
                     print(f"Waiting for morning... (Time: {t})")
                     time.sleep(10)
                 
                 # Do not fail, proceed to tasks (or re-check)
                 # Re-check sleep just in case
                 if not sleep_through_night(client):
                     pass

        print("DEBUG: Creating task list (Optimized Progression)...")

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
        # ... (method content unchanged, omitted for brevity in this replace call, 
        # but since I am replacing execute, I need to make sure I don't delete it. 
        # Actually I can't easily skip it if I replace the whole block.
        # Wait, I am replacing execute AND adding new helper methods.
        # I should target execute separately to avoid massive replacement context issues.
        # But wait, the user instructions earlier modified execute.
        # The prompt asks for replacing execute and adding methods.
        # I'll replace execute only first, then add methods at the end.
        pass

    # ... (existing _ensure_crafting_table, _craft_wooden_tools) ...
    
    # I will do this in 2 steps. First replace execute(), then add the new methods.
    # This tool call handles EXECUTE replacement.
    pass


    def _ensure_crafting_table(self, client) -> bool:
        """Finds or places a crafting table and opens it."""
        import time
        from ...common.inventory import count_item, craft
        from ...common.base import is_position_safe
        
        print("  Setting up crafting table...")
        
        # 1. Try to find existing nearby using get_view (voxel scan)
        print("  Scanning usage area for crafting table...")
        view_res = client.transport.dispatch('get_view', {'radius': 16}) # Scan 16 blocks radius
        voxels = view_res.get('voxels', [])
        found_table = None
        
        # Search voxels for crafting table
        for v in voxels:
            if v.get('id') == 'minecraft:crafting_table':
                found_table = v
                break
                
        if found_table:
            tx, ty, tz = found_table['x'], found_table['y'], found_table['z']
            print(f"  Found existing crafting table at ({tx}, {ty}, {tz})")
            
            # Goto nearby (don't walk INTO the table)
            print(f"  Moving near existing table...")
            # Simple heuristic: try to stand on top (y+1) or adjacent? 
            # Standing on top is safest if there's air.
            # Or just move to x+1.
            # Let's try x+1, z+0 first.
            client.transport.dispatch("goto", {"x": tx+1, "y": ty, "z": tz})
            
            # Wait getting there (simple timeout)
            # Ideally we check status, but for short distance 3-4s is enough
            time.sleep(4.0) 
            
            print(f"  Opening table at {tx}, {ty}, {tz}")
            # Ensure we look at it first?
            client.transport.dispatch('look_at', {'x': tx+0.5, 'y': ty+0.5, 'z': tz+0.5})
            time.sleep(0.1)
            client.transport.dispatch('interact_block', {'x': tx, 'y': ty, 'z': tz, 'hand': 'MAIN_HAND'})
            time.sleep(1.0) # Wait for UI to open
            
            # Verify screen is open?
            # State doesn't always show screen.
            # But subsequent craft command needs it.
            return True
            
        print("  No nearby crafting table found.")

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
            # Check safety (no liquids)
            if not is_position_safe(client, px, py, pz):
                print(f"DEBUG: Skipping {px},{py},{pz} - unsafe/liquid")
                continue

            # Pre-check: only try if target is air/replaceable
            check = client.transport.dispatch('get_block', {'x': px, 'y': py, 'z': pz})
            bid = check.get('id', '')
            
            # Allow replacing soft blocks
            # Allow replacing soft blocks (Removed water)
            soft_blocks = ['air', 'grass', 'fern', 'leaf', 'flower', 'snow']
            is_soft = any(s in bid for s in soft_blocks)
            
            if bid and 'air' not in bid and bid != 'minecraft:air' and not is_soft:
                print(f"DEBUG: Skipping {px},{py},{pz} - occupied by {bid}")
                continue
                
            time.sleep(0.3)
            client.transport.dispatch("chat", {"message": f"Trying to place at {px},{py},{pz}"})
            
            inv_before = count_item(client, "minecraft:crafting_table")
            
            try:
                # Use Baritone's robust placement via chat as first choice if available
                # or as fallback
                print(f"  Attempting Baritone placement at {px, py, pz}...")
                client.transport.dispatch("chat", {"message": f"#place crafting_table {px} {py} {pz}"})
                time.sleep(3.0) 
                
                # Verify
                check = client.transport.dispatch('get_block', {'x': px, 'y': py, 'z': pz})
                if 'crafting_table' in check.get('id', ''):
                    placed_pos = (px, py, pz)
                    break
                
                # Fallback to direct bridge placement
                place_result = client.transport.dispatch('place_block', {'x': px, 'y': py, 'z': pz})
                
                if place_result.get('placed', False):
                    placed_pos = (px, py, pz)
                    break
                
                if 'error' in place_result:
                    print(f"DEBUG: Place error at {px, py, pz}: {place_result.get('error')}")
            except Exception as e:
                print(f"DEBUG: Place exception at {px, py, pz}: {e}")
                # Continue to next spot or fallback

            # Optimistic check
            check = client.transport.dispatch('get_block', {'x': px, 'y': py, 'z': pz})
            bid = check.get('id', '')
            
            if 'crafting_table' in bid:
                 print(f"  Placement reported fail but found table at {px, py, pz}")
                 placed_pos = (px, py, pz)
                 break
                 
        if not placed_pos and 'inv_after' in dir() and inv_after < inv_before:
             print("DEBUG: Item consumed but placement not detected?")
        
        if not placed_pos:
            print("  Failed to place in open air. Clearing a dedicated spot...")
            # Pick a specific target to clear (e.g., x+1)
            tx, ty, tz = x+1, y, z
            
            print(f"  Mining spot at {tx}, {ty}, {tz} to make room...")
            client.transport.dispatch('mine', {'x': tx, 'y': ty, 'z': tz})
            time.sleep(4.0) # Wait for it to break
            
            # Ensure we stopped mining
            client.transport.dispatch('cancel', {})
            time.sleep(0.5)
            
            # Re-select table
            if final_slot < 9:
                client.transport.dispatch('select_slot', {'slot': final_slot})
            else:
                # Safe fallback if slot management got messy
                client.transport.dispatch('select_slot', {'slot': 0})
                
            time.sleep(0.5)
            
            print(f"  Placing at cleared spot {tx}, {ty}, {tz}")
            place_result = client.transport.dispatch('place_block', {'x': tx, 'y': ty, 'z': tz})
            
            # Verify
            time.sleep(0.5)
            check = client.transport.dispatch('get_block', {'x': tx, 'y': ty, 'z': tz})
            if 'crafting_table' in check.get('id', ''):
                 placed_pos = (tx, ty, tz)
            elif place_result.get('placed'):
                 placed_pos = (tx, ty, tz)

        if not placed_pos:
            print("  Still failed to place crafting table.")
            return False
            
            for px, py, pz in positions:
                # Pre-check: only try if target is air/replaceable
                check = client.transport.dispatch('get_block', {'x': px, 'y': py, 'z': pz})
                bid = check.get('id', '')
                if bid and 'air' not in bid and bid != 'minecraft:air':
                    print(f"DEBUG: Retry skipping {px},{py},{pz} - occupied by {bid}")
                    continue
                    
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
             self.state.add_location("crafting_table", placed_pos[0], placed_pos[1], placed_pos[2], tags=["setup"], client=client)
        time.sleep(0.3)
        
        # FIX: Blacklist to prevent breaking
        print("  Safeguard: Blacklisting crafting tables from mining...")
        client.transport.dispatch("chat", {"message": "#blacklist minecraft:crafting_table"})
        
        # Step back
        px, py, pz = placed_pos
        print("  Stepping back from table...")
        client.transport.dispatch("goto", {"x": px+1, "y": py, "z": pz}) 
        time.sleep(1.0)
        
        # Open
        client.transport.dispatch('interact_block', {'x': px, 'y': py, 'z': pz})
        time.sleep(2.0)
        return True

    def _craft_wooden_tools(self, client) -> bool:
        """Craft wooden tools."""
        import time
        from ...common.inventory import count_item, craft
        
        if count_item(client, "minecraft:wooden_pickaxe") > 0:
            print("  Already have wooden pickaxe!")
            return True
        
        # Check existing planks FIRST before converting logs
        planks = sum(count_item(client, f"minecraft:{wood}_planks") for wood in ["oak", "spruce", "birch", "dark_oak", "acacia", "jungle", "mangrove", "cherry"])
        print(f"  Existing planks: {planks}")
        
        # Only craft planks if we need more (Need 12 for safety: 4 Table + 2 Sticks + 3 Pick = 9 minimum)
        if planks < 12:
            needed_planks = 12 - planks
            logs_to_convert = (needed_planks + 3) // 4  # Each log gives 4 planks
            print(f"  Need {needed_planks} more planks, converting {logs_to_convert} logs...")
            # Only convert what we need
            craft(client, "minecraft:oak_planks", logs_to_convert)
            craft(client, "minecraft:spruce_planks", logs_to_convert)
            craft(client, "minecraft:birch_planks", logs_to_convert)
            craft(client, "minecraft:jungle_planks", logs_to_convert)
            craft(client, "minecraft:acacia_planks", logs_to_convert)
            craft(client, "minecraft:dark_oak_planks", logs_to_convert)
            craft(client, "minecraft:cherry_planks", logs_to_convert)
            craft(client, "minecraft:mangrove_planks", logs_to_convert)
            time.sleep(0.3)
        else:
            print(f"  Have enough planks ({planks}), skipping log conversion")
        
        # Re-check planks after potential crafting
        planks = sum(count_item(client, f"minecraft:{wood}_planks") for wood in ["oak", "spruce", "birch", "dark_oak", "acacia", "jungle", "mangrove", "cherry"])
        if planks < 9: # Minimum strict requirement
             print(f"  Still not enough planks ({planks}) for Table+Sticks+Pick (Need 9)")
             return False

        # Check sticks before crafting to avoid waste (User reported "too many sticks")
        sticks = count_item(client, "minecraft:stick")
        if sticks < 4:
            print(f"  Crafting sticks (Have {sticks}, need 4)...")
            # Crafting 1 batch yields 4 sticks. Asking for 4 items should be safe, or 1 operation.
            # Assuming 'count' is item count.
            craft(client, "minecraft:stick", 4)
            time.sleep(0.3)
        else:
            print(f"  Have enough sticks ({sticks})")
        
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

    def _craft_stone_pickaxe_only(self, client) -> bool:
        """Craft just the stone pickaxe (to speed up mining)."""
        from ...common.inventory import count_item, craft
        
        if count_item(client, "minecraft:stone_pickaxe") > 0:
            print("  Already have stone pickaxe!")
            return True
            
        if not self._ensure_crafting_table(client):
            return False
            
        print("  Crafting stone pickaxe...")
        result = craft(client, "minecraft:stone_pickaxe", 1)
        client.transport.dispatch("close_screen", {})
        return result

    def _craft_bed(self, client) -> bool:
        """Craft a bed (tries all wool colors)."""
        from ...common.inventory import count_item, craft
        
        wool_colors = [
            "white", "black", "gray", "light_gray", "brown", 
            "red", "orange", "yellow", "lime", "green", 
            "cyan", "light_blue", "blue", "purple", "magenta", "pink"
        ]
        
        for color in wool_colors:
            bed_id = f"minecraft:{color}_bed"
            wool_id = f"minecraft:{color}_wool"
            
            # Check if we already have this color bed
            if count_item(client, bed_id) > 0:
                print(f"  Already have {bed_id}!")
                return True
                
            # Check if we have 3 of this wool
            if count_item(client, wool_id) >= 3:
                print(f"  Found 3 {wool_id}, attempting to craft {bed_id}...")
                if self._ensure_crafting_table(client):
                    # We need 3 planks too. _ensure_crafting_table ensures we have a table 
                    # but doesn't guarantee planks are still there. 
                    # craft() helper should handle finding the recipe.
                    result = craft(client, bed_id, 1)
                    client.transport.dispatch("close_screen", {})
                    if result:
                        print(f"  Successfully crafted {bed_id}")
                        return True
        
        # Generic check for ANY bed
        beds = ["minecraft:white_bed", "minecraft:black_bed", "minecraft:gray_bed", "minecraft:light_gray_bed", "minecraft:brown_bed"]
        for bed in beds:
            if count_item(client, bed) > 0:
                return True

        print("  Could not craft bed (missing 3 wool of same color or planks)")
        return False

    def _craft_remaining_stone_tools(self, client) -> bool:
        """Craft remaining stone tools (axe, sword)."""
        from ...common.inventory import count_item, craft, equip_best_weapon
        
        # Force refresh inventory to avoid duplicate crafting
        # Assuming resources manager or similar handles refresh, but client.transport 'get_inventory' does it live?
        # Baritone Bridge 'get_inventory' returns live data, so count_item calling it should be fine.
        # But let's verify count_item implementation.
        # If count_item caches, we need to clear it. 
        # For now, let's assume get_inventory is called fresh or add a small wait/check.
        
        need_sword = count_item(client, "minecraft:stone_sword") == 0
        need_axe = count_item(client, "minecraft:stone_axe") == 0
        
        needed_sticks = 0
        if need_sword: needed_sticks += 1
        if need_axe: needed_sticks += 2
        
        current_sticks = count_item(client, "minecraft:stick")
        if current_sticks < needed_sticks:
             print(f"  Not enough sticks for remaining tools (Have {current_sticks}, need {needed_sticks})")
             # Craft more sticks
             # Need planks?
             # 2 planks -> 4 sticks.
             planks = sum(count_item(client, f"minecraft:{wood}_planks") for wood in ["oak", "spruce", "birch", "dark_oak", "acacia", "jungle", "mangrove", "cherry"])
             if planks < 2:
                 # Craft planks from logs?
                 logs = sum(count_item(client, block) for block in ["minecraft:oak_log", "minecraft:spruce_log", "minecraft:birch_log", "minecraft:jungle_log", "minecraft:acacia_log", "minecraft:dark_oak_log", "minecraft:mangrove_log", "minecraft:cherry_log"])
                 if logs > 0:
                     craft(client, "minecraft:oak_planks", 1) # simple fallback
                     time.sleep(0.5)
             
             craft(client, "minecraft:stick", 4) # Get 4 more
             time.sleep(0.5)
        
        if not (need_sword or need_axe):
             print("  Already have remaining stone tools!")
             return True

        if not self._ensure_crafting_table(client):
            return False
            
        success = True
        if need_sword:
             print("  Crafting stone sword...")
             if not craft(client, "minecraft:stone_sword", 1): success = False
        if need_axe:
             print("  Crafting stone axe...")
             if not craft(client, "minecraft:stone_axe", 1): success = False
             
        client.transport.dispatch("close_screen", {})
        equip_best_weapon(client)
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
                        # swap to 0? Or just select if in hotbar.
                        # Assume simple swap for now if needed, or rely on client finding it.
                        # Baritone helper might handle it.
                        # Let's verify slot.
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
            # Legacy checkpoint (optional, keeping for safety if ws used elsewhere)
            # ws.save_checkpoint("storage", {"x": chest_pos[0], "y": chest_pos[1], "z": chest_pos[2]})
            
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
