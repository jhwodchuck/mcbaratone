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
                        print("DEBUG: No trees nearby? Starting brief exploration...")
                        client.transport.dispatch("chat", {"message": "#explore"})
                        exploring = True
                        idle_checks = 0
                        time.sleep(5)
                        continue
                    else:
                        print("DEBUG: Exploration failed to find logs. Failing fast.")
                        return False
            else:
                if exploring:
                    # If we started pathing again, we might have found a tree or just moving.
                    # Let's give it time.
                    pass
                idle_checks = 0
            
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
            
            if stalled_checks >= 10: # ~30 seconds no progress
                if not exploring:
                     print("DEBUG: No wood progress for 30s. Attempting exploration...")
                     client.transport.dispatch("chat", {"message": "#explore"})
                     exploring = True
                     stalled_checks = 0
                else:
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
        client.transport.dispatch("mine", {"blocks": ORES[ore_type], "quantity": count + 2})
        start = time.time()
        idle_checks = 0
        
        while time.time() - start < timeout:
            # INTEGRATE DEFENSE
            from .combat import defend_or_flee
            if defend_or_flee(client):
                 client.transport.dispatch("mine", {"blocks": ORES[ore_type], "quantity": count + 2})
                 time.sleep(2)
                 idle_checks = 0
                 continue

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
    """Navigate to specific Y level for mining."""
    try:
        # Ensure Baritone settings allow breaking and placing blocks
        client.transport.dispatch("chat", {"message": "#set allowBreak true"})
        client.transport.dispatch("chat", {"message": "#set allowPlace true"})

        # Revert to simple goal Y level (safer height)
        y = -54 
        client.transport.dispatch("chat", {"message": f"#goal {y}"})
        client.transport.dispatch("chat", {"message": "#set allowBreak true"})
        client.transport.dispatch("chat", {"message": "#path"})
        
        print(f"DEBUG: Goal set to Y={y}. Waiting for arrival...")
        
        start = time.time()
        while time.time() - start < timeout:
            state = client.transport.dispatch("get_state", {})
            position = state.get("block_position", state.get("position", {}))
            current_y = position.get("y", state.get("y", 0))
            is_pathing = state.get("is_pathing", False)
            
            if int(time.time()) % 5 == 0:
                 print(f"DEBUG: y={current_y}, pathing={is_pathing}")
                 if 'error' in state:
                     print(f"DEBUG State Error: {state['error']}")
            
            if abs(current_y - y) < 5:
                client.transport.dispatch("cancel", {})
                return True
            
            if not is_pathing and time.time() - start > 10:
                 print("DEBUG: Pathing stopped. Re-asserting goal...")
                 client.transport.dispatch("chat", {"message": f"#goal {y}"})
                 client.transport.dispatch("chat", {"message": "#path"})
                 
            time.sleep(1)
        client.transport.dispatch("cancel", {})
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
    
    # Try to open existing crafting table
    if not open_crafting_table(client):
        # No nearby table, try to place one
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
            # Fallback: try to mine out a spot
            print(f"  All spots occupied - mining a space...")
            fallback_pos = (px + 1, py, pz)
            try:
                # Use Baritone to break the block
                from .navigation import goto
                client.transport.dispatch("mine", {"blocks": ["minecraft:stone", "minecraft:diorite", "minecraft:granite", "minecraft:andesite", "minecraft:cobblestone", "minecraft:dirt"], "quantity": 1})
                time.sleep(3)  # Wait for mining
                
                # Now try to place at the mined spot
                print(f"  Attempting placement at mined spot {fallback_pos}...")
                if place_crafting_table(client, fallback_pos[0], fallback_pos[1], fallback_pos[2]):
                    found_spot = True
                    table_pos = fallback_pos
                    print(f"  Crafting table placed at fallback {fallback_pos}!")
            except Exception as e:
                print(f"  Mining fallback failed: {e}")
        
        if not found_spot:
            print(f"  Failed to place crafting table for {item_id} (all spots occupied)")
            return False
        
        # Now try to open the table we just placed
        if table_pos:
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
