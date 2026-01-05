"""
Resource gathering utilities - Wood, stone, ores, and materials.
"""

import time
from typing import Any, Callable, Dict, Optional
from .inventory import count_item, craft, select_item
from .tasks import TaskResult
from .combat import hunt_mobs
from .navigation import find_nearby_block, goto

# Log block types (full IDs)
LOG_BLOCKS = [
    "minecraft:oak_log",
    "minecraft:birch_log",
    "minecraft:spruce_log",
    "minecraft:dark_oak_log",
    "minecraft:acacia_log",
    "minecraft:jungle_log",
    "minecraft:mangrove_log",
    "minecraft:cherry_log",
    "minecraft:pale_oak_log", # 1.21.4
]

# Stone block types
STONE_BLOCKS = [
    "minecraft:stone",
    "minecraft:cobblestone",
    "minecraft:deepslate",
    "minecraft:cobbled_deepslate",
]

# Ore types by tier
ORES = {
    "coal": ["minecraft:coal_ore", "minecraft:deepslate_coal_ore"],
    "iron": ["minecraft:iron_ore", "minecraft:deepslate_iron_ore"],
    "gold": ["minecraft:gold_ore", "minecraft:deepslate_gold_ore", "minecraft:nether_gold_ore"],
    "diamond": ["minecraft:diamond_ore", "minecraft:deepslate_diamond_ore"],
    "copper": ["minecraft:copper_ore", "minecraft:deepslate_copper_ore"],
    "lapis": ["minecraft:lapis_ore", "minecraft:deepslate_lapis_ore"],
    "redstone": ["minecraft:redstone_ore", "minecraft:deepslate_redstone_ore"],
    "emerald": ["minecraft:emerald_ore", "minecraft:deepslate_emerald_ore"],
}


def gather_wood(client, count: int = 16, timeout: int = 180) -> bool:
    """Gather wood logs until count reached.
    
    Smart behavior: Checks for existing planks first. If we have enough planks
    to satisfy the wood requirement (4 planks = 1 log equivalent), skip gathering.
    """
    # Check existing logs first
    total_logs = sum(count_item(client, block) for block in LOG_BLOCKS)
    if total_logs >= count:
        print(f"DEBUG: Already have {total_logs} logs, skipping gather")
        return True
    
    # Check existing planks - if we have enough planks, we don't need logs
    # 1 log = 4 planks, so planks/4 = equivalent logs
    PLANK_TYPES = [
        "minecraft:oak_planks", "minecraft:birch_planks", "minecraft:spruce_planks",
        "minecraft:dark_oak_planks", "minecraft:acacia_planks", "minecraft:jungle_planks",
        "minecraft:mangrove_planks", "minecraft:cherry_planks"
    ]
    total_planks = sum(count_item(client, p) for p in PLANK_TYPES)
    equivalent_logs = total_planks // 4  # 4 planks = 1 log
    
    if total_logs + equivalent_logs >= count:
        print(f"DEBUG: Have {total_logs} logs + {total_planks} planks ({equivalent_logs} log equiv) = enough! Skipping gather")
        return True
    
    # Calculate how many more logs we actually need
    needed = count - total_logs - equivalent_logs
    print(f"DEBUG: Need {needed} more logs (have {total_logs} logs, {total_planks} planks)")
    
    try:
        client.transport.dispatch("mine", {"blocks": LOG_BLOCKS, "quantity": needed + 4})
        start = time.time()
        idle_checks = 0
        last_count = total_logs + equivalent_logs
        stalled_checks = 0
        exploring = False
        
        while time.time() - start < timeout:
            # INTEGRATE DEFENSE
            from .combat import defend_or_flee
            if defend_or_flee(client):
                 print("DEBUG: Wood gathering interrupted by defense logic. Resuming mining...")
                 # Re-issue mine command just in case
                 client.transport.dispatch("mine", {"blocks": LOG_BLOCKS, "quantity": needed + 4})
                 time.sleep(2)
                 idle_checks = 0
                 stalled_checks = 0
                 exploring = False
                 continue

            # Fail Fast: Check if Baritone gave up (is_pathing = False)
            state = client.transport.dispatch("get_state", {})
            is_pathing = state.get("is_pathing", True)
            
            # Inventory Progress Tracking
            curr_logs = sum(count_item(client, block) for block in LOG_BLOCKS)
            curr_planks = sum(count_item(client, p) for p in PLANK_TYPES)
            total = curr_logs + (curr_planks // 4)
            print(f"DEBUG: gather_wood total={total}/{count} (Pathing: {is_pathing})")
            
            if total >= count:
                client.transport.dispatch("cancel", {})
                return True

            if not is_pathing:
                idle_checks += 1
                if idle_checks >= 3:
                    if not exploring:
                        if idle_checks == 3:
                            print("DEBUG: Baritone idle. Retrying mine command locally...")
                            client.transport.dispatch("mine", {"blocks": LOG_BLOCKS, "quantity": needed + 4})
                        elif idle_checks >= 6: # ~18s total idle
                            print("DEBUG: No trees nearby (idle)? Attempting explicit search...")
                            # Explicitly find blocks since 'mine' command might be failing
                            found_log = find_nearby_block(client, LOG_BLOCKS, radius=128)
                            if found_log:
                                lx, ly, lz = found_log
                                print(f"DEBUG: Found log at ({lx}, {ly}, {lz}). Pathing to it...")
                                client.transport.dispatch("goto", {"x": lx, "y": ly, "z": lz})
                                exploring = True # Treat as exploring/traveling
                                idle_checks = 0
                                time.sleep(2)
                            else:
                                print("DEBUG: No logs found in radius 128. Random exploration...")
                                client.transport.dispatch("chat", {"message": "#explore"})
                                exploring = True
                                idle_checks = 0
                                time.sleep(2)
                        continue
                    else:
                        # We were exploring/pathing, but now we stopped.
                        # Assume we arrived or stuck.
                        print("DEBUG: Exploration/Goto finished (stopped pathing). Resuming mining...")
                        client.transport.dispatch("mine", {"blocks": LOG_BLOCKS, "quantity": needed + 4})
                        exploring = False
                        idle_checks = 0
                        stalled_checks = 0
                        continue
                        
                        # Old logic below for reference, replaced by above:
                        # if idle_checks < 20: ... pass ... else: fail
            else:
                if exploring:
                    # If we started pathing again, we might have found a tree or just moving.
                    # Let's give it time.
                    pass
                idle_checks = 0
            
            if stalled_checks >= 10 and is_pathing: # Only check stall if we THINK we are moving
                print(f"DEBUG: Wood gathering stalled (stalled_checks={stalled_checks}). Retrying local mine command...")
                client.transport.dispatch("cancel", {})
                time.sleep(0.5)
                client.transport.dispatch("mine", {"blocks": LOG_BLOCKS, "quantity": needed + 4})
                stalled_checks = 0
                # Do NOT reset idle_checks here
                
                # Only explore if we've stalled multiple times effectively
                # actually let's implement a separate counter for "major stalls"
                # but for now, just retrying the mine command usually fixes pathing issues
                continue

            if total == last_count:
                stalled_checks += 1
            else:
                stalled_checks = 0
                last_count = total
                if exploring:
                    print("DEBUG: Found wood during exploration! Cancelling explore and mining...")
                    client.transport.dispatch("cancel", {})
                    client.transport.dispatch("mine", {"blocks": LOG_BLOCKS, "quantity": needed + 4})
                    exploring = False
            
            # Timeout/Still Stuck handling
            if stalled_checks >= 15 and not exploring:
                 print("DEBUG: No wood progress for 45s. NOW attempting exploration...")
                 client.transport.dispatch("chat", {"message": "#explore"})
                 exploring = True
                 stalled_checks = 0
            elif stalled_checks >= 30 and exploring:
                 print("DEBUG: Still no wood progress during exploration. Giving up.")
                 return False
                
            time.sleep(3)
        print("DEBUG: gather_wood timeout")
        client.transport.dispatch("cancel", {})
        return False
    except Exception as exc:
        print(f"Wood gathering error: {exc}")
        return False


def gather_stone(client, count: int = 16, timeout: int = 180) -> bool:
    """Gather cobblestone until count reached."""
    try:
        # Check for pickaxe - cannot mine stone with hand
        pickaxes = ["minecraft:wooden_pickaxe", "minecraft:stone_pickaxe", "minecraft:iron_pickaxe", "minecraft:diamond_pickaxe"]
        if sum(count_item(client, p) for p in pickaxes) == 0:
             print("DEBUG: No pickaxe for gathering stone! Ensuring wooden pickaxe...")
             if not ensure_supplies(client, {"minecraft:wooden_pickaxe": 1}).success:
                 print("DEBUG: Failed to acquire pickaxe. Aborting gather_stone.")
                 return False

        # Initial attempt: standard mine
        client.transport.dispatch("mine", {"blocks": STONE_BLOCKS, "quantity": count + 10})
        
        start = time.time()
        last_count = 0
        stalled_checks = 0
        idle_checks = 0
        
        while time.time() - start < timeout:
            # INTEGRATE DEFENSE
            from .combat import defend_or_flee
            if defend_or_flee(client):
                 # Combat happened. Resume mining.
                 client.transport.dispatch("mine", {"blocks": STONE_BLOCKS, "quantity": count + 10})
                 time.sleep(2)
                 idle_checks = 0
                 continue

            # Fail Fast: Check if Baritone gave up (is_pathing = False)
            state = client.transport.dispatch("get_state", {})
            is_pathing = state.get("is_pathing", True)
            if not is_pathing:
                idle_checks += 1
                if idle_checks >= 3:
                    print("DEBUG: Baritone stopped pathing during stone gathering (Fail Fast)")
                    return False
            else:
                idle_checks = 0

            total = count_item(client, "minecraft:cobblestone") + count_item(client, "minecraft:cobbled_deepslate")
            print(f"DEBUG: gather_stone loop: total={total}/{count}")
            
            if total >= count:
                print(f"DEBUG: gather_stone success! total={total}")
                client.transport.dispatch("cancel", {})
                return True
                
            # Stall detection: If we aren't getting items
            if total == last_count:
                stalled_checks += 1
            else:
                stalled_checks = 0
                last_count = total
                
            # If stalled for 15s (5 checks * 3s), try to unstick by digging down
            if stalled_checks >= 5:
                print("DEBUG: Stone gathering stalled. Attempting to dig down to find stone...")
                pos = client.transport.dispatch("get_player_pos", {})
                px, py, pz = int(pos[0]), int(pos[1]), int(pos[2])
                # Dig a small shaft down to find stone
                client.transport.dispatch("mine", {"x": px, "y": py-3, "z": pz}) 
                time.sleep(2)
                # Resume wide scan
                client.transport.dispatch("mine", {"blocks": STONE_BLOCKS, "quantity": count + 10})
                stalled_checks = 0 # Reset
                
            time.sleep(3)
            
        print("DEBUG: gather_stone timeout")
        client.transport.dispatch("cancel", {})
        return False
    except Exception as exc:
        print(f"Stone gathering error: {exc}")
        return False


def gather_ores(client, ore_type: str, count: int, timeout: int = 600) -> bool:
    """Gather specific ore type."""
    if ore_type not in ORES:
        print(f"Unknown ore type: {ore_type}")
        return False
    drop_items = {
        "coal": "minecraft:coal",
        "iron": "minecraft:raw_iron",
        "gold": "minecraft:raw_gold",
        "diamond": "minecraft:diamond",
        "copper": "minecraft:raw_copper",
        "lapis": "minecraft:lapis_lazuli",
        "redstone": "minecraft:redstone",
        "emerald": "minecraft:emerald",
    }
    drop_item = drop_items.get(ore_type, f"minecraft:raw_{ore_type}")
    try:
        # Initial check for pickaxe - preventing infinite loop if tool broken
        pickaxes = ["minecraft:stone_pickaxe", "minecraft:iron_pickaxe", "minecraft:diamond_pickaxe", "minecraft:wooden_pickaxe"]
        if sum(count_item(client, p) for p in pickaxes) == 0:
             print("DEBUG: No pickaxe at start of gather_ores! Crafting replacement...")
             ensure_supplies(client, {"minecraft:stone_pickaxe": 1})

        client.transport.dispatch("mine", {"blocks": ORES[ore_type], "quantity": count + 2})
        start = time.time()
        idle_checks = 0
        last_tool_check = time.time()
        
        while time.time() - start < timeout:
            # INTEGRATE DEFENSE
            from .combat import defend_or_flee
            if defend_or_flee(client):
                 client.transport.dispatch("mine", {"blocks": ORES[ore_type], "quantity": count + 2})
                 time.sleep(2)
                 idle_checks = 0
                 continue

            # Check pickaxe every 30 seconds
            if time.time() - last_tool_check > 30:
                pickaxe_count = count_item(client, "minecraft:stone_pickaxe") + \
                               count_item(client, "minecraft:iron_pickaxe") + \
                               count_item(client, "minecraft:diamond_pickaxe")
                if pickaxe_count == 0:
                    print("DEBUG: No pickaxe! Crafting replacement...")
                    client.transport.dispatch("cancel", {})
                    ensure_supplies(client, {"minecraft:stone_pickaxe": 1})
                    client.transport.dispatch("mine", {"blocks": ORES[ore_type], "quantity": count + 2})
                last_tool_check = time.time()

            # Fail Fast: Check if Baritone gave up (is_pathing = False)
            state = client.transport.dispatch("get_state", {})
            is_pathing = state.get("is_pathing", True) # Default True to be safe
            
            if not is_pathing:
                idle_checks += 1
                if idle_checks >= 3: # ~9-10 seconds of idle
                     print("DEBUG: Baritone stopped pathing (Fail Fast)")
                     return False
            else:
                idle_checks = 0

            total = count_item(client, drop_item)
            if total >= count:
                client.transport.dispatch("cancel", {})
                return True
            time.sleep(3) # Reduced sleep to scan more often
        client.transport.dispatch("cancel", {})
        return False
    except Exception as exc:
        print(f"Ore gathering error: {exc}")
        return False


def go_to_y_level(client, y: int, timeout: int = 300) -> bool:
    """Navigate to specific Y level using incremental goto targets (30 blocks at a time)."""
    try:
        STEP_SIZE = 30
        SEGMENT_TIMEOUT = 60  # Max time per segment
        
        # Enable necessary settings once
        settings = [
            "#set allowBreak true",
            "#set allowPlace true",
            "#set allowParkour true",
            "#set allowDownward true",
            "#set allowWater false",
        ]
        for s in settings:
            client.transport.dispatch("chat", {"message": s})
        
        overall_start = time.time()
        
        while time.time() - overall_start < timeout:
            # Get current position
            state = client.transport.dispatch("get_state", {})
            pos = state.get("block_position", state.get("position", {}))
            px, current_y, pz = int(pos.get("x", 0)), int(pos.get("y", 64)), int(pos.get("z", 0))
            
            # Check if we've reached the final target
            if current_y <= y + 3:
                print(f"DEBUG: Reached target Y={y}!")
                client.transport.dispatch("chat", {"message": "#cancel"})
                return True
            
            # Calculate next incremental target
            next_target_y = max(y, current_y - STEP_SIZE)
            # Offset X by the same amount we go down (creates diagonal staircase)
            x_offset = current_y - next_target_y
            target_x = px + x_offset
            print(f"DEBUG: At Y={current_y}, going to ({target_x}, {next_target_y}) (final: {y})")
            
            # Issue goto for this segment - offset X creates stairs, not straight shaft
            target_cmd = f"#goto {target_x} {next_target_y} {pz}"
            client.transport.dispatch("chat", {"message": target_cmd})
            
            # Wait for this segment to complete
            segment_start = time.time()
            last_y = current_y
            stall_count = 0
            
            while time.time() - segment_start < SEGMENT_TIMEOUT:
                state = client.transport.dispatch("get_state", {})
                pos = state.get("block_position", state.get("position", {}))
                current_y = int(pos.get("y", last_y))
                is_pathing = state.get("is_pathing", False)
                
                # Made progress - segment complete or getting closer
                if current_y <= next_target_y + 2:
                    print(f"DEBUG: Segment complete at Y={current_y}")
                    break
                
                # Still making progress
                if current_y < last_y:
                    last_y = current_y
                    stall_count = 0
                elif not is_pathing:
                    stall_count += 1
                    if stall_count >= 5:
                        print(f"DEBUG: Stalled at Y={current_y}, trying lateral movement...")
                        # Try moving 50 blocks in a direction to find a better spot
                        import random
                        offset = random.choice([(50, 0), (-50, 0), (0, 50), (0, -50)])
                        lateral_cmd = f"#goto {px + offset[0]} {current_y} {pz + offset[1]}"
                        client.transport.dispatch("chat", {"message": lateral_cmd})
                        time.sleep(15)  # Give it time to move
                        break
                
                time.sleep(2)
            
            # Brief pause before next segment
            time.sleep(1)
        
        client.transport.dispatch("chat", {"message": "#cancel"})
        return False
    except Exception as exc:
        print(f"Y level navigation error: {exc}")
        return False



def gather_gravel(client, count: int, timeout: int = 300) -> bool:
    """Gather gravel until specific amount of flint is obtained."""
    try:
        # Request mining a lot of gravel (10% drop rate for flint)
        client.transport.dispatch("mine", {"blocks": ["minecraft:gravel"], "quantity": count * 20})
        start = time.time()
        while time.time() - start < timeout:
            total = count_item(client, "minecraft:flint")
            if total >= count:
                client.transport.dispatch("cancel", {})
                return True
            time.sleep(3)
        client.transport.dispatch("cancel", {})
        return False
    except Exception as exc:
        print(f"Gravel gathering error: {exc}")
        return False


def gather_water(client, count: int = 1, timeout: int = 60) -> bool:
    """Find water and fill bucket."""
    try:
        # Check if we have a bucket
        if count_item(client, "minecraft:bucket") < 1:
             # Just fail, let the strategy handle crafting if it strictly needs water bucket
             # But usually ensuring "water_bucket" implies crafting "bucket" first which is handled by ensure_dependencies?
             # Actually ensure_supplies handles strict items. If we need water_bucket, we call this.
             pass

        # Find water
        # Search for water block
        water_pos = find_nearby_block(client, ["minecraft:water"], radius=32)
        if not water_pos:
            print("No water found nearby")
            return False
            
        # Go to water
        goto(client, water_pos[0], water_pos[1], water_pos[2], tolerance=2)
        
        # Select bucket
        if not select_item(client, "minecraft:bucket"):
            print("No empty bucket to fill")
            return False
            
        # Interact with water
        client.transport.dispatch("look_at", {"x": water_pos[0], "y": water_pos[1], "z": water_pos[2]})
        time.sleep(0.5)
        client.transport.dispatch("use_item", {}) # Right click
        time.sleep(0.5)
        
        return count_item(client, "minecraft:water_bucket") >= count
    except Exception as exc:
        print(f"Water gathering error: {exc}")
        return False


def _default_mine(client, block_id: str, quantity: int) -> bool:
    try:
        client.transport.dispatch("mine", {"blocks": [block_id], "quantity": max(1, quantity)})
        return True
    except Exception as exc:
        print(f"Mine command failed for {block_id}: {exc}")
        return False


def _craft_with_table(client, item_id: str, qty: int) -> bool:
    """Craft with crafting table - opens table first if needed."""
    from .base import open_crafting_table, place_crafting_table
    from .automation_utils import get_player_pos
    
    # Early exit if we already have the item
    if count_item(client, item_id) >= qty:
        return True
    
    # Try to open existing crafting table nearby
    if not open_crafting_table(client):
        # Try to go to saved crafting table waypoint
        print("  Trying saved crafting table waypoint...")
        client.transport.dispatch("chat", {"message": "#goto crafting_table"})
        time.sleep(5)  # Give time to path to waypoint
        
        # Try to open table at waypoint
        if not open_crafting_table(client):
            # No nearby table and waypoint failed, try to place one
            if count_item(client, "minecraft:crafting_table") == 0:
                # Ensure we have planks
                # Check for any planks first
                total_planks = sum(count_item(client, p) for p in [
                    "minecraft:oak_planks", "minecraft:birch_planks", "minecraft:spruce_planks",
                    "minecraft:dark_oak_planks", "minecraft:acacia_planks", "minecraft:jungle_planks",
                    "minecraft:mangrove_planks", "minecraft:cherry_planks"
                ])
                
                if total_planks < 4:
                    # Check for logs
                    total_logs = sum(count_item(client, b) for b in LOG_BLOCKS)
                    if total_logs > 0:
                        # Find which log we have
                        for log in LOG_BLOCKS:
                            if count_item(client, log) > 0:
                                print(f"  Crafting planks from {log} for crafting table...")
                                plank_type = log.replace("_log", "_planks").replace("_wood", "_planks")
                                craft(client, plank_type, 1) 
                                time.sleep(0.5)
                                break

                # Craft crafting table first (2x2 recipe)
                craft(client, "minecraft:crafting_table", 1)
                time.sleep(0.5)
        
        # Get player position for placement
        pos = get_player_pos(client)
        if not pos:
            print(f"  Cannot get position to place crafting table")
            return False
        
        px, py, pz = int(pos[0]), int(pos[1]), int(pos[2])
        
        # Try a few spots around the player
        found_spot = False
        # Try spots with Y variations too (player might be underground)
        offsets = [
             # Same Y level
             (2, 0, 0), (-2, 0, 0), (0, 0, 2), (0, 0, -2),
             (1, 0, 1), (-1, 0, -1), (1, 0, -1), (-1, 0, 1),
             # One block up (more likely to be air if underground)
             (1, 1, 0), (-1, 1, 0), (0, 1, 1), (0, 1, -1),
             (1, 1, 1), (-1, 1, -1),
             # One block down
             (1, -1, 0), (-1, -1, 0),
        ]
        
        found_spot = False
        table_pos = None
        max_attempts = 8  # Increased since we have more spots to try
        attempts = 0
        
        for dx, dy, dz in offsets:
            if attempts >= max_attempts:
                print(f"  Max placement attempts ({max_attempts}) reached")
                break
                
            tx, ty, tz = px + dx, py + dy, pz + dz
            
            # Check if spot is air or replaceable first
            try:
                block_result = client.transport.dispatch("get_block", {"x": tx, "y": ty, "z": tz})
                block_id = block_result.get("id", "")
                if block_id not in ["minecraft:air", "minecraft:cave_air", "minecraft:short_grass", "minecraft:tall_grass", ""]:
                    print(f"  Skip ({tx}, {ty}, {tz}) - occupied by {block_id}")
                    continue  # Skip this spot, it's not air
            except Exception as e:
                print(f"  Block check failed: {e}")
                # Continue anyway if check fails
            
            print(f"  Attempting to place crafting table at ({tx}, {ty}, {tz})...")
            attempts += 1
            if place_crafting_table(client, tx, ty, tz):
                found_spot = True
                table_pos = (tx, ty, tz)
                print(f"  Crafting table placed at ({tx}, {ty}, {tz})!")
                break  # Stop trying other spots once placed!
        
        if not found_spot:
            # Fallback: mine out a specific nearby block using attack_block
            print(f"  All spots occupied - mining a space with attack_block...")
            fallback_pos = (px + 1, py, pz)
            try:
                # Use attack_block to directly break the target block
                for _ in range(10):  # Multiple attempts to break the block
                    client.transport.dispatch("attack_block", {
                        "x": fallback_pos[0],
                        "y": fallback_pos[1],
                        "z": fallback_pos[2]
                    })
                    time.sleep(0.3)
                
                time.sleep(2)  # Wait for block to fully break and drop
                
                # Now try to place at the mined spot
                print(f"  Attempting placement at mined spot {fallback_pos}...")
                if place_crafting_table(client, fallback_pos[0], fallback_pos[1], fallback_pos[2]):
                    found_spot = True
                    table_pos = fallback_pos
                    print(f"  Crafting table placed at fallback {fallback_pos}!")
            except Exception as e:
                print(f"  Mining fallback failed: {e}")
        
        if not found_spot:
            # Last resort: go to surface where there's more space
            print(f"  Failed underground - trying to go to surface...")
            client.transport.dispatch("chat", {"message": "#surface"})
            time.sleep(30)  # Give time to reach surface
            # Try again on surface
            state = client.transport.dispatch("get_state", {})
            pos = state.get("block_position", state.get("position", {}))
            surface_x, surface_y, surface_z = int(pos.get("x", 0)), int(pos.get("y", 64)), int(pos.get("z", 0))
            print(f"  At surface Y={surface_y}, trying placement...")
            if place_crafting_table(client, surface_x + 1, surface_y, surface_z):
                found_spot = True
                table_pos = (surface_x + 1, surface_y, surface_z)
                print(f"  Crafting table placed at surface {table_pos}!")
        
        if not found_spot:
            print(f"  Failed to place crafting table for {item_id} (all spots occupied)")
            return False
        
        # Now try to open the table we just placed
        if table_pos:
            # Save as POI/waypoint for future use
            print(f"  Saving crafting table as waypoint at {table_pos}...")
            client.transport.dispatch("chat", {"message": f"#waypoint save crafting_table {table_pos[0]} {table_pos[1]} {table_pos[2]}"})
            
            # Also save as MAIN BASE
            client.transport.dispatch("chat", {"message": f"#waypoint save base {table_pos[0]} {table_pos[1]} {table_pos[2]}"})
            print(f"  *** BASE LOCATION SET to {table_pos} ***")
            
            # FIX: Blacklist to prevent breaking
            print("  Safeguard: Blacklisting crafting tables from mining...")
            client.transport.dispatch("chat", {"message": "#blacklist minecraft:crafting_table"})
            
            # Step back
            px, py, pz = table_pos
            print("  Stepping back from table...")
            client.transport.dispatch("goto", {"x": px+1, "y": py, "z": pz}) 
            time.sleep(1.0)
            
            if not open_crafting_table(client, table_pos[0], table_pos[1], table_pos[2]):
                print(f"  Warning: Placed table but failed to open at {table_pos}")
    
    # Now craft
    try:
        result = craft(client, item_id, qty)
        time.sleep(0.3)
        return result
    except Exception as e:
        print(f"  Crafting failed for {item_id}: {e}")
        return False


DEFAULT_REQUIREMENT_STRATEGIES: Dict[str, Callable[[Any, int], bool]] = {
    "minecraft:oak_log": lambda client, qty: gather_wood(client, count=max(qty, 16)),
    "minecraft:cobblestone": lambda client, qty: gather_stone(client, count=max(qty, 16)),
    "minecraft:iron_ingot": lambda client, qty: gather_ores(client, "iron", count=max(qty * 2, qty + 4)),
    "minecraft:diamond": lambda client, qty: gather_ores(client, "diamond", count=max(qty, 4)),
    "minecraft:gold_ingot": lambda client, qty: gather_ores(client, "gold", count=max(qty, 8)),
    "minecraft:obsidian": lambda client, qty: _default_mine(client, "minecraft:obsidian", qty),
    "minecraft:crafting_table": lambda client, qty: craft(client, "minecraft:crafting_table", qty) or True,
    "minecraft:furnace": lambda client, qty: _craft_with_table(client, "minecraft:furnace", qty),
    "minecraft:chest": lambda client, qty: _craft_with_table(client, "minecraft:chest", qty),
    # Early game wooden tools (2x2 crafting)
    "minecraft:stick": lambda client, qty: (client.transport.dispatch("close_screen", {}), craft(client, "minecraft:stick", max(qty, 4)), time.sleep(1)),
    "minecraft:oak_planks": lambda client, qty: (client.transport.dispatch("close_screen", {}), craft(client, "minecraft:oak_planks", qty), time.sleep(1)),
    # Wooden tools (3x3 crafting table required)
    "minecraft:wooden_pickaxe": lambda client, qty: _craft_with_table(client, "minecraft:wooden_pickaxe", qty),
    "minecraft:wooden_sword": lambda client, qty: _craft_with_table(client, "minecraft:wooden_sword", qty),
    "minecraft:wooden_axe": lambda client, qty: _craft_with_table(client, "minecraft:wooden_axe", qty),
    "minecraft:wooden_shovel": lambda client, qty: _craft_with_table(client, "minecraft:wooden_shovel", qty),
    # Stone tools
    "minecraft:stone_pickaxe": lambda client, qty: (client.transport.dispatch("close_screen", {}), _craft_with_table(client, "minecraft:stone_pickaxe", qty)),
    "minecraft:stone_sword": lambda client, qty: (client.transport.dispatch("close_screen", {}), _craft_with_table(client, "minecraft:stone_sword", qty)),
    "minecraft:stone_axe": lambda client, qty: (client.transport.dispatch("close_screen", {}), _craft_with_table(client, "minecraft:stone_axe", qty)),
    "minecraft:stone_shovel": lambda client, qty: (client.transport.dispatch("close_screen", {}), _craft_with_table(client, "minecraft:stone_shovel", qty)),
    "minecraft:iron_pickaxe": lambda client, qty: _craft_with_table(client, "minecraft:iron_pickaxe", qty),
    "minecraft:iron_sword": lambda client, qty: _craft_with_table(client, "minecraft:iron_sword", qty),
    "minecraft:diamond_pickaxe": lambda client, qty: _craft_with_table(client, "minecraft:diamond_pickaxe", qty),
    "minecraft:diamond_sword": lambda client, qty: _craft_with_table(client, "minecraft:diamond_sword", qty),
    "minecraft:bow": lambda client, qty: _craft_with_table(client, "minecraft:bow", qty),
    "minecraft:arrow": lambda client, qty: _craft_with_table(client, "minecraft:arrow", max(qty, 32)),
    "minecraft:string": lambda client, qty: hunt_mobs(client, ["spider", "cave_spider"], {"minecraft:string": qty}, search_radius=64, timeout=300).success,
    "minecraft:feather": lambda client, qty: hunt_mobs(client, ["chicken"], {"minecraft:feather": qty}, search_radius=50, timeout=300).success,
    "minecraft:flint": lambda client, qty: gather_gravel(client, count=qty),
    "minecraft:shield": lambda client, qty: _craft_with_table(client, "minecraft:shield", qty),
    "minecraft:bucket": lambda client, qty: _craft_with_table(client, "minecraft:bucket", qty),
    "minecraft:water_bucket": lambda client, qty: gather_water(client, count=qty),
}


def manage_inventory(client):
    """Check if inventory is full and drop junk items if needed."""
    from .inventory import is_full, drop_items
    if not is_full(client):
        return

    print("  Inventory full! Clearing junk...")
    # Items to drop (keep cobble/deepslate if < 64? simplied: just drop non-essential)
    # Always drop purely junk blocks
    junk = ["minecraft:dirt", "minecraft:gravel", "minecraft:diorite", "minecraft:andesite", "minecraft:granite", "minecraft:tuff"]
    drop_items(client, junk)
    
    # If still full, drop excess stone/deepslate but try to keep some?
    # For now, if really full, just drop them. We can always mine more.
    if is_full(client):
         print("  Still full, dropping stone/deepslate...")
         drop_items(client, ["minecraft:cobblestone", "minecraft:deepslate", "minecraft:cobbled_deepslate"])


def _smelt_with_furnace(client, item_id: str, qty: int) -> bool:
    """Smelt items using a furnace (places one if needed)."""
    from .base import place_furnace, open_furnace
    from .automation_utils import get_player_pos
    from .inventory import count_item
    
    # Map desired output to input
    input_item = "minecraft:raw_iron"
    if item_id == "minecraft:gold_ingot":
        input_item = "minecraft:raw_gold"
    
    # Ensure furnace exists in inventory (or nearby)
    if count_item(client, "minecraft:furnace") == 0 and not find_nearby_block(client, ["minecraft:furnace"], radius=10):
         # Craft one
         gather_stone(client, count=8)
         craft(client, "minecraft:furnace", 1)
    
    # If we are carrying a furnace, place it!
    if count_item(client, "minecraft:furnace") > 0:
        print("  Carrying furnace - attempting to place...")
        # Try to place
        pos = get_player_pos(client)
        px, py, pz = int(pos[0]), int(pos[1]), int(pos[2])
        
        offsets = [(1,0,0), (-1,0,0), (0,0,1), (0,0,-1), (1,1,0), (-1,1,0), (0,1,1), (0,1,-1)]
        placed = False
        for dx,dy,dz in offsets:
            if place_furnace(client, px+dx, py, pz+dz):
                placed = True
                fpos = (px+dx, py, pz+dz)
                print(f"  Saved furnace POI at {fpos}")
                client.transport.dispatch("chat", {"message": f"#waypoint save furnace {fpos[0]} {fpos[1]} {fpos[2]}"})
                # Update world_map.md
                try:
                    with open("c:/gh/mcbaratone/world_map.md", "a") as f:
                        f.write(f"\n- **Furnace**: {fpos}")
                except Exception as e:
                    print(f"  Failed to update world_map.md: {e}")
                
                client.transport.dispatch("chat", {"message": "#blacklist minecraft:furnace"})
                time.sleep(1)
                break
        if not placed:
            print("  Failed to place furnace (will retry later)")
            
    # Try to locate existing furnace (if we didn't just place one, or if placement fail but maybe we have one nearby?)
    elif open_furnace(client):
        # Successfully found and opened one. Save its location as POI.
        fpos = find_nearby_block(client, ["minecraft:furnace"], radius=20)
        if fpos:
            print(f"  Saving existing furnace POI at {fpos}")
            client.transport.dispatch("chat", {"message": f"#waypoint save furnace {fpos[0]} {fpos[1]} {fpos[2]}"})
            try:
                with open("c:/gh/mcbaratone/world_map.md", "a") as f:
                    f.write(f"\n- **Furnace (Existing)**: {fpos}")
            except Exception: pass
    else:
        # Try stored waypoint
        print("  Going to saved furnace...")
        client.transport.dispatch("chat", {"message": "#goto furnace"})
        time.sleep(5)
        
        if not open_furnace(client):
            # Place new one
            pos = get_player_pos(client)
            px, py, pz = int(pos[0]), int(pos[1]), int(pos[2])
            
            # Find a spot
            offsets = [(1,0,0), (-1,0,0), (0,0,1), (0,0,-1), (1,1,0), (-1,1,0), (0,1,1), (0,1,-1)]
            placed = False
            for dx,dy,dz in offsets:
                # Check for space (simplified, place_furnace handles safety)
                if place_furnace(client, px+dx, py, pz+dz):
                    placed = True
                    fpos = (px+dx, py, pz+dz)
                    print(f"  Saved furnace POI at {fpos}")
                    client.transport.dispatch("chat", {"message": f"#waypoint save furnace {fpos[0]} {fpos[1]} {fpos[2]}"})
                    try:
                        with open("c:/gh/mcbaratone/world_map.md", "a") as f:
                            f.write(f"\n- **Furnace**: {fpos}")
                    except Exception: pass
                    
                    client.transport.dispatch("chat", {"message": "#blacklist minecraft:furnace"})
                    time.sleep(1)
                    break
            
            if not placed:
                print("  Failed to place furnace!")
                # Don't return False yet, maybe we can gather resources anyway? 
                # But we can't smelt without it.
                # proceed to gathering, maybe we place later.
    
    # Check what we already have
    current = count_item(client, item_id)
    needed = qty - current
    if needed <= 0: return True
    
    # Gather input if missing
    if count_item(client, input_item) < needed:
         ore = "iron" if "iron" in input_item else "gold"
         gather_ores(client, ore, count=needed)
    
    # Gather coal if missing
    if count_item(client, "minecraft:coal") < needed // 8 + 1:
         gather_ores(client, "coal", count=max(4, needed // 4))

    # Perform smelting interaction
    if open_furnace(client):
        print("  Furnace opened. Adding items...")
        # Add Coal (Shift click)
        # We assume quick move works and puts it in fuel
        # iterate inventory and shift-click coal
        inv = client.transport.dispatch("get_inventory", {})
        for item in inv.get("inventory", []):
            if item.get("id") == "minecraft:coal":
                client.transport.dispatch("inventory_click", {"slot": item["slot"], "type": "QUICK_MOVE", "button": 0})
                time.sleep(0.1)
                
        # Add Input (Shift click)
        for item in inv.get("inventory", []):
            if item.get("id") == input_item:
                client.transport.dispatch("inventory_click", {"slot": item["slot"], "type": "QUICK_MOVE", "button": 0})
                time.sleep(0.1)
                
        # Wait loop logic would go here. For now just wait a bit and collect whatever is done
        # Users script will retry if count not met
        print("  Smelting...")
        time.sleep(5)
        
        # Collect result (Slot 2 is output usually in furnace container)
        # Container slots: 0=input, 1=fuel, 2=output.
        # But slots might be offset depending on container index.
        # Standard furnace: 0,1,2.
        client.transport.dispatch("inventory_click", {"slot": 2, "type": "QUICK_MOVE", "button": 0})
        
        client.transport.dispatch("close_screen", {})
        
    return count_item(client, item_id) >= qty


# Update strategies to use new smelting function
DEFAULT_REQUIREMENT_STRATEGIES["minecraft:iron_ingot"] = lambda client, qty: _smelt_with_furnace(client, "minecraft:iron_ingot", qty)
DEFAULT_REQUIREMENT_STRATEGIES["minecraft:gold_ingot"] = lambda client, qty: _smelt_with_furnace(client, "minecraft:gold_ingot", qty)


def _missing_requirements(client, requirements: Dict[str, int]) -> Dict[str, int]:
    missing: Dict[str, int] = {}
    for item_id, required in requirements.items():
        current = count_item(client, item_id)
        if current < required:
            missing[item_id] = required - current
    return missing


def ensure_supplies(
    client,
    requirements: Dict[str, int],
    strategies: Optional[Dict[str, Callable[[Any, int], bool]]] = None,
    poll_interval: float = 5.0,
    timeout: int = 600,
) -> TaskResult:
    """
    Ensure the inventory matches the provided requirement list.

    Args:
        client: Baritone client
        requirements: Mapping of item_id -> minimum amount
        strategies: Optional overrides for how to acquire each item
        poll_interval: Seconds to wait between refreshes
        timeout: Maximum duration before giving up
    """
    strategies_map = dict(DEFAULT_REQUIREMENT_STRATEGIES)
    if strategies:
        strategies_map.update(strategies)

    start = time.time()
    attempts = 0
    operations = []

    # Try to loot nearby chests first (recover from death or use starter chest)
    from .base import loot_nearby_chests
    loot_nearby_chests(client)


    missing = _missing_requirements(client, requirements)
    if not missing:
        return TaskResult.ok("All requirements already satisfied", operations=operations)

    while missing and time.time() - start < timeout:
        # Check inventory space
        manage_inventory(client)
        
        print(f"Ensuring supplies: {missing} (t={time.time()-start:.0f}s/{timeout}s)")
        for item_id, shortfall in list(missing.items()):
            handler = strategies_map.get(item_id)
            if handler is None:
                print(f"  No handler for {item_id}, skipping")
                continue
            attempts += 1
            print(f"  Gathering {item_id} x{shortfall}...")
            handler(client, shortfall)
            operations.append({"item": item_id, "shortfall": shortfall})
        time.sleep(poll_interval)
        missing = _missing_requirements(client, requirements)

    if missing:
        return TaskResult.fail(
            "Missing required supplies",
            missing=missing,
            operations=operations,
            attempts=attempts,
            timeout=timeout,
        )

    return TaskResult.ok(
        "Requirements satisfied",
        operations=operations,
        attempts=attempts,
        duration=time.time() - start,
    )
