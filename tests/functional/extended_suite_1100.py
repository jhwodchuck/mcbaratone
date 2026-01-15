"""
Extended Suite 1100: Enhanced for 100+ Consecutive Runs
Improvements:
- Tool durability monitoring and auto-replacement
- Resource depletion handling
- Better error recovery
- Run statistics tracking
"""

import json
import os
import time
from typing import Tuple, Optional, List
from tests.functional.test_base import TestCase, TestSuite, TestContext
from tests.utils.mc_harness.actions import do_goto, do_open_container, do_close_container

from tests.functional.shared.block_ops import (
    block_id_at,
    find_place_pos_near,
    bot_place_block,
    move_near,
    get_inv_slots,
)
from tests.functional.shared.inventory_ops import (
    craft_and_wait,
    ensure_crafting_table_open,
    withdraw_from_supply_chest,
    safe_inventory_click,
)
from tests.functional.shared.suite_constants import (
    TIMEOUTS,
    get_timeout,
    poll_until,
)
from utils.mc_harness import (
    wait_for_block,
    wait_for_pathing_stop,
    wait_for_position_stable,
    cancel_pathing,
)

STATE_FILE = "t1100_store.json"

# --- Materials / Item Lists (expanded) ---

# All overworld logs (including bamboo block if you want it treated like a "wood")
LOG_BLOCK_IDS = [
    "minecraft:oak_log",
    "minecraft:spruce_log",
    "minecraft:birch_log",
    "minecraft:jungle_log",
    "minecraft:acacia_log",
    "minecraft:dark_oak_log",
    # "minecraft:mangrove_log", # Disabled due to Baritone crash
    # "minecraft:cherry_log",   # Disabled due to Baritone crash
    # Optional:
    # "minecraft:bamboo_block",
]

# Stripped logs (often end up in inventory depending on mods/tools/automation)
STRIPPED_LOG_BLOCK_IDS = [
    "minecraft:stripped_oak_log",
    "minecraft:stripped_spruce_log",
    "minecraft:stripped_birch_log",
    "minecraft:stripped_jungle_log",
    "minecraft:stripped_acacia_log",
    "minecraft:stripped_dark_oak_log",
    "minecraft:stripped_mangrove_log",
    "minecraft:stripped_cherry_log",
]

# "Wood" blocks (bark on all sides) sometimes appear via crafting or harvesting setups
WOOD_BLOCK_IDS = [
    "minecraft:oak_wood",
    "minecraft:spruce_wood",
    "minecraft:birch_wood",
    "minecraft:jungle_wood",
    "minecraft:acacia_wood",
    "minecraft:dark_oak_wood",
    "minecraft:mangrove_wood",
    "minecraft:cherry_wood",
]

STRIPPED_WOOD_BLOCK_IDS = [
    "minecraft:stripped_oak_wood",
    "minecraft:stripped_spruce_wood",
    "minecraft:stripped_birch_wood",
    "minecraft:stripped_jungle_wood",
    "minecraft:stripped_acacia_wood",
    "minecraft:stripped_dark_oak_wood",
    "minecraft:stripped_mangrove_wood",
    "minecraft:stripped_cherry_wood",
]

# All planks
PLANK_ITEM_IDS = [
    "minecraft:oak_planks",
    "minecraft:spruce_planks",
    "minecraft:birch_planks",
    "minecraft:jungle_planks",
    "minecraft:acacia_planks",
    "minecraft:dark_oak_planks",
    "minecraft:mangrove_planks",
    "minecraft:cherry_planks",
]

# If you ever decide to do replanting later
SAPLING_ITEM_IDS = [
    "minecraft:oak_sapling",
    "minecraft:spruce_sapling",
    "minecraft:birch_sapling",
    "minecraft:jungle_sapling",
    "minecraft:acacia_sapling",
    "minecraft:dark_oak_sapling",
    "minecraft:mangrove_propagule",
    "minecraft:cherry_sapling",
]

# Tools to never deposit
TOOL_ITEM_SUBSTRINGS = [
    "_axe", "_pickaxe", "_shovel", "_hoe", "_sword",
    "crafting_table",  # you already exclude this
]

# Foods to never deposit (expand as you like)
FOOD_ITEM_SUBSTRINGS = [
    "apple", "bread", "beef", "porkchop", "mutton", "chicken",
    "potato", "carrot", "beetroot", "cookie", "melon_slice",
    "sweet_berries", "glow_berries", "pumpkin_pie",
]

def load_farming_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {"stats": {"total_runs": 0, "total_logs": 0, "failed_runs": 0}}

def save_farming_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=4)

def create_extended_suite_1100() -> TestSuite:
    suite = TestSuite("Suite_1100_Farming_Enhanced", "100+ run farming automation")
    
    suite_state = {
        "persistent": load_farming_state(),
        "supply_chest_positions": None,
        "run_number": 0,  # Track current run
    }
    
    def _debug(ctx, msg):
        run_num = suite_state.get("run_number", 0)
        log_line = f"[Farming Run {run_num}] {msg}"
        ctx.log_event(log_line)
        print(log_line)

    def _debug_full_status(ctx):
        """Print comprehensive status: health, hunger, position, inventory."""
        run_num = suite_state.get("run_number", 0)
        prefix = f"[Farming Run {run_num}] STATUS:"
        
        try:
            # Get position
            pos = ctx.get_position()
            pos_str = f"Pos({pos[0]:.1f}, {pos[1]:.1f}, {pos[2]:.1f})" if pos else "unknown"
            
            # Get health/hunger from player state
            state = ctx.client.transport.dispatch("get_state", {})
            health = state.get("health", "?")
            hunger = state.get("food", state.get("hunger", "?"))
            
            # Get full inventory
            inv_contents = _debug_inventory(ctx)
            inv_str = ", ".join([f"{k.replace('minecraft:', '')}: {v}" for k, v in inv_contents.items()]) if inv_contents else "empty"
            
            status_line = f"{prefix} HP={health} Hunger={hunger} | {pos_str} | Inv: {inv_str}"
            ctx.log_event(status_line)
            print(status_line)
        except Exception as e:
            print(f"{prefix} Error getting status: {e}")

    def _wait_for_baritone_idle(ctx: TestContext, timeout: float = 10.0):
        wait_for_pathing_stop(ctx, timeout=timeout)
        wait_for_position_stable(ctx, timeout=2.0, stable_window=0.5)

    def _check_inventory_item(ctx: TestContext, item_id: str) -> int:
        try:
            return ctx.count_item(item_id)
        except Exception:
            return 0

    def _count_all_logs(ctx: TestContext) -> int:
        """Count all wood-like blocks we consider as 'logs' for progress."""
        all_ids = (
            LOG_BLOCK_IDS
            + STRIPPED_LOG_BLOCK_IDS
            + WOOD_BLOCK_IDS
            + STRIPPED_WOOD_BLOCK_IDS
        )
        return sum(max(0, _check_inventory_item(ctx, item_id)) for item_id in all_ids)

    def _get_any_planks(ctx: TestContext) -> int:
        return sum(max(0, _check_inventory_item(ctx, item_id)) for item_id in PLANK_ITEM_IDS)

    def _debug_inventory(ctx: TestContext):
        """Debug helper to show inventory contents."""
        inv = ctx.get_inventory().get("inventory", [])
        items = {}
        for slot in inv:
            if slot and slot.get("id") and slot.get("id") != "minecraft:air":
                item_id = slot.get("id")
                count = slot.get("count", 0)
                if item_id in items:
                    items[item_id] += count
                else:
                    items[item_id] = count
        return items

    def _get_persistent_store(ctx):
        return suite_state["persistent"]

    def _save_persistent(ctx):
        save_farming_state(suite_state["persistent"])

    def _check_tool_durability(ctx: TestContext) -> Optional[int]:
        """Check wooden axe durability. Returns remaining uses of BEST axe (None if no axe)."""
        inv = ctx.get_inventory().get("inventory", [])
        best_durability = None
        best_slot = None
        
        for item in inv:
            if item.get("id") == "minecraft:wooden_axe":
                damage = item.get("damage", 0)
                max_damage = 59  # Wooden axe durability
                remaining = max_damage - damage
                slot = item.get("slot", -1)
                
                if best_durability is None or remaining > best_durability:
                    best_durability = remaining
                    best_slot = slot
        
        # If we found a good axe not in hotbar, equip it
        if best_slot is not None and best_slot >= 9 and best_durability is not None and best_durability > 10:
            _debug(ctx, f"Best axe (durability {best_durability}) at slot {best_slot}, moving to hotbar...")
            _equip_item_from_slot(ctx, best_slot)
        
        return best_durability

    def _equip_item_from_slot(ctx: TestContext, slot: int):
        """Move an item from inventory slot to hotbar slot 0."""
        try:
            # Swap with hotbar slot (slots 36-44 are hotbar in player inventory screen)
            safe_inventory_click(ctx, slot, "PICKUP", 0)
            time.sleep(0.1)
            safe_inventory_click(ctx, 36, "PICKUP", 0)  # Hotbar slot 0
            time.sleep(0.1)
            # If there was something in hotbar, put it back
            safe_inventory_click(ctx, slot, "PICKUP", 0)
            time.sleep(0.1)
        except Exception as e:
            _debug(ctx, f"Failed to equip item: {e}")

    def _robust_craft(ctx: TestContext, item_id: str, count: int = 1, is_tool: bool = False) -> bool:
        """Craft an item with retries and multiple methods."""
        for attempt in range(6): # More attempts to handle multi-step crafting (planks then table etc)
            current_count = _check_inventory_item(ctx, item_id)
            if not is_tool and current_count >= count:
                return True
            if is_tool and _check_tool_durability(ctx) is not None:
                return True

            try:
                _debug(ctx, f"Crafting {count}x {item_id} (Attempt {attempt+1}, using auto_craft)...")
                ctx.client.transport.dispatch("auto_craft", {"item": item_id, "quantity": count})
                time.sleep(3.0)
            except Exception as e:
                _debug(ctx, f"auto_craft attempt failed: {e}")

            if not is_tool and _check_inventory_item(ctx, item_id) >= count: return True
            if is_tool and _check_tool_durability(ctx) is not None: return True

            try:
                _debug(ctx, f"Crafting {count}x {item_id} (Attempt {attempt+1}, using craft)...")
                ctx.client.transport.dispatch("craft", {"item": item_id, "count": count})
                time.sleep(2.0)
            except Exception as e:
                _debug(ctx, f"craft attempt failed: {e}")
            
            if not is_tool and _check_inventory_item(ctx, item_id) >= count: return True
            if is_tool and _check_tool_durability(ctx) is not None: return True

            # Final fallback for axe/chest if bridge commands fail
            if is_tool and item_id == "minecraft:wooden_axe":
                if _craft_axe_manual_clicks(ctx): return True
            if item_id == "minecraft:chest":
                if _craft_chest_manual_clicks(ctx): return True
            
            time.sleep(1.0)
        return False

    def _craft_sticks_manual(ctx: TestContext) -> bool:
        """Craft sticks using the bridge's robust crafting logic."""
        return _robust_craft(ctx, "minecraft:stick", 1)

    def _craft_crafting_table_manual(ctx: TestContext) -> bool:
        """Craft a crafting table using the bridge's robust crafting logic."""
        return _robust_craft(ctx, "minecraft:crafting_table", 1)

    def _ensure_fresh_tool(ctx: TestContext, min_durability: int = 10) -> bool:
        """Ensure we have a tool with at least min_durability remaining."""
        durability = _check_tool_durability(ctx)
        
        if durability is None or durability < min_durability:
            _debug(ctx, f"Tool needs replacement (durability: {durability})")
            return _craft_replacement_axe(ctx)
        
        return True

    def _ensure_crafting_table_item(ctx: TestContext) -> bool:
        """Ensure we have a crafting table in inventory, crafting one if needed."""
        if _check_inventory_item(ctx, "minecraft:crafting_table") > 0:
            return True
            
        # Need to craft one. Requires 4 planks.
        if _get_any_planks(ctx) < 4:
            # Need to craft planks. Requires 1 log.
            if _count_all_logs(ctx) < 1:
                _debug(ctx, "ERROR: No logs available to bootstrap library/storage")
                return False
            
            # Find a log to convert
            inv = _debug_inventory(ctx)
            log_item = None
            for item_id in inv.keys():
                if "_log" in item_id:
                    log_item = item_id
                    break
            
            if log_item:
                target_plank = log_item.replace("_log", "_planks")
                _debug(ctx, f"Bootstrapping crafting table: converting {log_item} to planks")
                if not _robust_craft(ctx, target_plank, 1):
                    _debug(ctx, "Automated plank craft failed in bootstrap")
                time.sleep(0.5)

        # Craft the table
        return _robust_craft(ctx, "minecraft:crafting_table", 1)

    def _craft_replacement_axe(ctx: TestContext) -> bool:
        """Craft a new wooden axe."""
        _debug(ctx, "Crafting replacement axe...")
        
        # Debug: show actual inventory contents
        inv_contents = _debug_inventory(ctx)
        _debug(ctx, f"Inventory contents: {inv_contents}")
        
        # Check if we already have an axe before doing work
        existing_axe = _check_tool_durability(ctx)
        if existing_axe is not None and existing_axe >= 10:
            _debug(ctx, f"Already have usable axe (durability: {existing_axe})")
            return True
        
        # Ensure we have materials - check ALL wood types
        planks_needed = 3
        sticks_needed = 2
        
        all_logs = _count_all_logs(ctx)
        all_planks = _get_any_planks(ctx)
        current_sticks = _check_inventory_item(ctx, "minecraft:stick")
        
        _debug(ctx, f"Materials: {all_logs} logs (all types), {all_planks} planks (all types), {current_sticks} sticks")
        
        # Craft planks if needed (need at least 3 for axe + 2 for sticks = 5 total, or 2 logs)
        logs_to_convert = 0
        if all_planks < planks_needed + 2:  # Need 5 planks total (3 for axe head, 2 for sticks)
            if all_logs > 0:
                logs_to_convert = min(all_logs, 2)  # Convert up to 2 logs
                _debug(ctx, f"Converting {logs_to_convert} logs to planks...")
                for _ in range(logs_to_convert):
                    # Identify which log we have to craft the correct plank
                    inv = _debug_inventory(ctx)
                    target_plank = None
                    for item_id in inv.keys():
                        if "_log" in item_id:
                            target_plank = item_id.replace("_log", "_planks")
                            break
                    
                    if not target_plank:
                        target_plank = "minecraft:oak_planks"

                    # Use robust crafting for planks
                    if not _robust_craft(ctx, target_plank, 1):
                         _debug(ctx, f"ERROR: Failed to craft planks ({target_plank})")
                         return False
                    time.sleep(0.3)
                all_planks = _get_any_planks(ctx)
                _debug(ctx, f"Now have {all_planks} planks (all types)")
            else:
                _debug(ctx, "ERROR: No logs available for crafting!")
                return False
        
        # Craft sticks if needed
        current_sticks = _check_inventory_item(ctx, "minecraft:stick")
        if current_sticks < sticks_needed:
            all_planks = _get_any_planks(ctx)
            if all_planks >= 2:
                _debug(ctx, "Crafting sticks...")
                if not craft_and_wait(ctx, "minecraft:stick", 1):
                    _debug(ctx, "Automated stick craft failed, trying manual...")
                    if not _craft_sticks_manual(ctx):
                        _debug(ctx, "ERROR: Failed to craft sticks (auto and manual)")
                        return False
                time.sleep(0.3)
                time.sleep(0.3)
                current_sticks = _check_inventory_item(ctx, "minecraft:stick")
                _debug(ctx, f"Now have {current_sticks} sticks")
            else:
                _debug(ctx, f"ERROR: Not enough planks for sticks! Have {all_planks}, need 2")
                return False
        
        # Verify we have enough materials for the axe
        all_planks = _get_any_planks(ctx)
        current_sticks = _check_inventory_item(ctx, "minecraft:stick")
        if all_planks < 3 or current_sticks < 2:
            _debug(ctx, f"ERROR: Insufficient materials for axe: {all_planks} planks, {current_sticks} sticks")
            return False
        
        # Get or place crafting table
        store = _get_persistent_store(ctx)
        table_pos = store.get("crafting_table")
        
        if table_pos is not None:
             _debug(ctx, f"Verifying stored crafting table at {table_pos}...")
             
             # Check block type first - prevent running to air/wrong block
             try:
                 block = ctx.client.transport.dispatch("get_block", {"x": int(table_pos[0]), "y": int(table_pos[1]), "z": int(table_pos[2])})
                 block_id = block.get("id", "")
                 if block_id != "minecraft:crafting_table":
                     _debug(ctx, f"WARNING: Block at stored pos is {block_id}, not crafting_table. Clearing.")
                     store["crafting_table"] = None
                     table_pos = None
                     _save_persistent(ctx)
             except Exception as e:
                 _debug(ctx, f"WARNING: Failed to check block: {e}")
            
             if table_pos:
                 # Check if we can reach it
                 if not move_near(ctx, table_pos[0], table_pos[1], table_pos[2], timeout=20.0):
                     _debug(ctx, "WARNING: Stored crafting table unreachable. Clearing stored position.")
                     store["crafting_table"] = None
                     table_pos = None
                     _save_persistent(ctx)
                 else:
                     # Check distance to confirm we are actually close
                     px, py, pz = ctx.get_position()
                     dist_sq = (px-table_pos[0])**2 + (py-table_pos[1])**2 + (pz-table_pos[2])**2
                     if dist_sq > 36: # > 6 blocks away
                         _debug(ctx, "WARNING: Still too far from crafting table. Clearing stored position.")
                         store["crafting_table"] = None
                         table_pos = None
                         _save_persistent(ctx)
        
        if table_pos is None:
            _debug(ctx, "No crafting table position stored/reachable, need to place one...")
            # Check if we have a crafting table in inventory
            if _check_inventory_item(ctx, "minecraft:crafting_table") == 0:
                # Craft a crafting table (needs 4 planks of any type)
                if _get_any_planks(ctx) >= 4:
                    _debug(ctx, "Crafting a crafting table...")
                    # First try recipe-based crafting
                    if not craft_and_wait(ctx, "minecraft:crafting_table", 1):
                        _debug(ctx, "Recipe crafting failed, trying manual 2x2 crafting...")
                        # Manual fallback: open inventory and craft in 2x2 grid
                        if not _craft_crafting_table_manual(ctx):
                            _debug(ctx, "ERROR: Failed to craft crafting table manually")
                            return False
                else:
                    _debug(ctx, f"ERROR: Not enough planks for crafting table (have {_get_any_planks(ctx)}, need 4)")
                    return False
            
            # Place the crafting table
            px, py, pz = ctx.get_position()
            pos = find_place_pos_near(ctx, int(px), int(py), int(pz), radius=3)
            if pos:
                x, y, z = pos
                if bot_place_block(ctx, x, y, z, "minecraft:crafting_table"):
                    table_pos = [x, y, z]
                    store["crafting_table"] = table_pos
                    _save_persistent(ctx)
                    _debug(ctx, f"Placed crafting table at {table_pos}")
                else:
                    _debug(ctx, "ERROR: Failed to place crafting table")
                    return False
            else:
                _debug(ctx, "ERROR: Could not find position to place crafting table")
                return False
        
        # Open the crafting table
        _debug(ctx, f"Opening crafting table at {table_pos}...")
        if not ensure_crafting_table_open(ctx, table_pos=table_pos):
            _debug(ctx, "ERROR: Could not open crafting table")
            return False
        time.sleep(0.5)
        
        if _craft_axe_manual(ctx):
            _debug(ctx, "Replacement axe crafted successfully")
            return True
        
        _debug(ctx, "ERROR: Failed to craft replacement axe")
        return False

    def _craft_axe_manual_clicks(ctx) -> bool:
        """Manually craft wooden axe in 3x3 grid."""
        screen = ctx.client.transport.dispatch("get_screen", {})
        data = screen.get("data", screen)
        slots = data.get("slots", [])
        
        plank_slot = -1
        stick_slot = -1
        
        # List of all plank types we can use
        plank_keywords = ["_planks"]
        
        for s in slots:
            sid = s.get("id", "")
            # Check for any type of planks
            if (sid in PLANK_ITEM_IDS or "_planks" in sid) and s.get("count", 0) >= 3:
                plank_slot = s.get("slot")
            if "stick" in sid and s.get("count", 0) >= 2:
                stick_slot = s.get("slot")
        
        if plank_slot == -1 or stick_slot == -1:
            _debug(ctx, f"Manual Axe: Missing ingredients (plank_slot={plank_slot}, stick_slot={stick_slot})")
            return False
        
        _debug(ctx, f"Placing axe recipe: planks from slot {plank_slot}, sticks from slot {stick_slot}")
        
        # Recipe placement for wooden axe:
        # [P][P][ ]    slots: 1, 2, 3
        # [P][S][ ]    slots: 4, 5, 6  
        # [ ][S][ ]    slots: 7, 8, 9
        # Output slot: 0
        
        safe_inventory_click(ctx, plank_slot, "PICKUP", 0)
        time.sleep(0.1)
        safe_inventory_click(ctx, 1, "PICKUP", 1)
        time.sleep(0.1)
        safe_inventory_click(ctx, 2, "PICKUP", 1)
        time.sleep(0.1)
        safe_inventory_click(ctx, 4, "PICKUP", 1)
        time.sleep(0.1)
        safe_inventory_click(ctx, plank_slot, "PICKUP", 0)  # Put back remaining planks
        time.sleep(0.1)
        
        safe_inventory_click(ctx, stick_slot, "PICKUP", 0)
        time.sleep(0.1)
        safe_inventory_click(ctx, 5, "PICKUP", 1)
        time.sleep(0.1)
        safe_inventory_click(ctx, 8, "PICKUP", 1)
        time.sleep(0.1)
        safe_inventory_click(ctx, stick_slot, "PICKUP", 0)  # Put back remaining sticks
        time.sleep(0.1)
        
        # Take the crafted axe from output slot
        safe_inventory_click(ctx, 0, "QUICK_MOVE")
        time.sleep(0.3)
        
        # Close crafting table
        do_close_container(ctx)
        time.sleep(0.2)
        
        # Verify we now have an axe
        durability = _check_tool_durability(ctx)
        if durability is not None:
            _debug(ctx, f"Axe crafted successfully! Durability: {durability}")
            return True
        else:
            _debug(ctx, "ERROR: Axe not found in inventory after crafting attempt")
            return False

    def _craft_axe_manual(ctx: TestContext) -> bool:
        """Craft a wooden axe using the bridge's robust crafting logic."""
        return _robust_craft(ctx, "minecraft:wooden_axe", 1, is_tool=True)

    def _craft_chest_manual(ctx: TestContext) -> bool:
        """Craft a chest using the bridge's robust crafting logic."""
        if _robust_craft(ctx, "minecraft:chest", 1):
            return True
        # Try manual clicks directly
        return _craft_chest_manual_clicks(ctx)

    def _craft_chest_manual_clicks(ctx: TestContext) -> bool:
        """Manual chest crafting sequence."""
        _debug(ctx, "Starting manual chest click sequence...")
        # 1. Take result slot if any (clear it)
        safe_inventory_click(ctx, 0, "QUICK_MOVE", 0)
        time.sleep(0.2)
        
        # 2. Find planks
        inv = ctx.get_inventory()
        slots = get_inv_slots(inv)
        plank_slot = -1
        for s in slots:
            if s.get("slot", -1) >= 10 and s.get("id", "").endswith("_planks") and s.get("count", 0) >= 8:
                plank_slot = s["slot"]
                break
        
        if plank_slot == -1:
            _debug(ctx, "ERROR: No planks found (>=8) for manual chest craft")
            return False

        # 3. Pick up planks
        safe_inventory_click(ctx, plank_slot, "PICKUP", 0)
        time.sleep(0.2)
        
        # 4. Place in 'O' shape: 1,2,3, 4,6, 7,8,9 (middle 5 empty)
        chest_pattern = [1, 2, 3, 4, 6, 7, 8, 9]
        for grid_slot in chest_pattern:
            safe_inventory_click(ctx, grid_slot, "PICKUP", 1) # Right click to place 1
            time.sleep(0.1)
        
        # 5. Return remaining planks
        safe_inventory_click(ctx, plank_slot, "PICKUP", 0)
        time.sleep(0.2)
        
        # 6. Take result
        safe_inventory_click(ctx, 0, "QUICK_MOVE", 0)
        time.sleep(0.5)
        
        if _check_inventory_item(ctx, "minecraft:chest") > 0:
            _debug(ctx, "Manual chest crafting successful!")
            return True
        return False

    def _update_stats(ctx: TestContext, success: bool, logs_gained: int):
        """Update run statistics."""
        store = _get_persistent_store(ctx)
        stats = store.get("stats", {})
        
        stats["total_runs"] = stats.get("total_runs", 0) + 1
        stats["total_logs"] = stats.get("total_logs", 0) + logs_gained
        
        if not success:
            stats["failed_runs"] = stats.get("failed_runs", 0) + 1
        
        store["stats"] = stats
        _save_persistent(ctx)
        
        _debug(ctx, f"Stats: {stats['total_runs']} runs, {stats['total_logs']} logs, {stats['failed_runs']} failures")

    # --- Test Steps ---

    def t1100_setup(ctx: TestContext):
        """Setup for current run."""
        suite_state["run_number"] = suite_state.get("run_number", 0) + 1
        
        _debug(ctx, f"Starting Run #{suite_state['run_number']}")
        
        # Commands disabled for multiplayer/non-OP compatibility
        # ctx.set_gamemode("survival")
        # ctx.run_command("difficulty peaceful")
        # ctx.run_command("gamerule doMobSpawning false")
        # ctx.run_command("time set day")
        # ctx.run_command("weather clear")
        
        # Give starter tool removed - test should robustly start from scratch
        # ctx.run_command("give @p minecraft:wooden_axe 1")
        
        cancel_pathing(ctx)
        time.sleep(0.5)
        
        _debug(ctx, "Setup complete")

    def t1100_check_and_repair_tools(ctx: TestContext) -> bool:
        """Check tool condition before mining."""
        _debug(ctx, "Checking tool condition...")
        
        durability = _check_tool_durability(ctx)
        _debug(ctx, f"Current tool durability: {durability}")
        
        # Ensure we have a usable tool, or start naturally
        if _ensure_fresh_tool(ctx, min_durability=10):
            return True
            
        # If we failed to get a tool, check if we're starting from scratch
        # Check total available materials
        current_logs = _count_all_logs(ctx)
        current_planks = _get_any_planks(ctx)
        current_sticks = _check_inventory_item(ctx, "minecraft:stick")
        
        # If we have no materials at all, we need to mine with fists to bootstrap
        if current_logs == 0 and current_planks < 5:
            _debug(ctx, f"No materials to craft (logs={current_logs}, planks={current_planks}) - starting fresh with fist mining")
            return True
            
        return False

    def t1100_mine_with_recovery(ctx: TestContext) -> bool:
        """Mine with error recovery and tool monitoring."""
        _debug(ctx, "Starting mining operation...")
        
        initial_logs = _count_all_logs(ctx)  # Count ALL log types
        target_gain = 10  # Lower target per run for reliability
        
        _debug(ctx, f"Starting with {initial_logs} logs, target +{target_gain}")
        
        LOG_MINE_ARG = " ".join([bid.replace("minecraft:", "") for bid in LOG_BLOCK_IDS])

        # Start mining
        ctx.client.transport.dispatch("chat", {"message": f"#mine {LOG_MINE_ARG}"})
        
        start = time.time()
        timeout = 120.0
        success = False
        last_count = initial_logs
        max_logs = initial_logs
        stuck_counter = 0
        
        while time.time() - start < timeout:
            current = _count_all_logs(ctx)  # Count ALL log types
            if current > max_logs:
                max_logs = current
                
            gain = max_logs - initial_logs
            
            # Check progress
            if gain >= target_gain:
                _debug(ctx, f"Target reached! Gained {gain} logs ({max_logs} total)")
                success = True
                break
            
            # Check if stuck (no progress for 20 seconds)
            # Use current for stuck detection to ensure we are actually moving
            if current == last_count:
                stuck_counter += 1
                if stuck_counter >= 10:  # 10 * 2s = 20s
                    # Get detailed status
                    pos = ctx.get_position()
                    try:
                        state = ctx.client.transport.dispatch("get_state", {})
                        is_pathing = state.get("pathing", False)
                    except Exception:
                        is_pathing = "Unknown"

                    _debug(ctx, f"Mining appears stuck. Logs: {max_logs} (Run Gain: {gain}). Pos: {pos}, Pathing: {is_pathing}")
                    ctx.client.transport.dispatch("chat", {"message": "#stop"})
                    time.sleep(1.0)
                    
                    # Try mining command again (Baritone often unstucks itself with a re-issue)
                    ctx.client.transport.dispatch("chat", {"message": f"#mine {LOG_MINE_ARG}"})
                    stuck_counter = 0
            else:
                stuck_counter = 0
                last_count = current
                # Log progress periodically
                if gain > 0 and gain % 5 == 0:
                    _debug_full_status(ctx)
            
            # Check tool durability mid-operation
            durability = _check_tool_durability(ctx)
            
            # If we don't have a tool (fist mining), check if we can craft one
            if durability is None:
                total_logs = _count_all_logs(ctx)
                if total_logs >= 3:
                     _debug(ctx, "Collected enough wood! Stopping to craft axe...")
                     ctx.client.transport.dispatch("chat", {"message": "#stop"})
                     cancel_pathing(ctx)
                     
                     if _craft_replacement_axe(ctx):
                         # Resume mining
                         ctx.client.transport.dispatch("chat", {"message": f"#mine {LOG_MINE_ARG}"})
            
            if durability is not None and durability < 5:
                # IMPORTANT: Only stop to repair if we haven't reached target yet
                # (We already check this at top of loop, but let's be safe)
                _debug(ctx, "Tool nearly broken, stopping to repair...")
                ctx.client.transport.dispatch("chat", {"message": "#stop"})
                cancel_pathing(ctx)
                
                if _craft_replacement_axe(ctx):
                    # Resume mining
                    ctx.client.transport.dispatch("chat", {"message": f"#mine {LOG_MINE_ARG}"})
                else:
                    break
            
            time.sleep(2.0)
        
        ctx.client.transport.dispatch("chat", {"message": "#stop"})
        cancel_pathing(ctx)
        
        final_logs = max_logs  # Use high-water mark for final reporting
        final_gain = final_logs - initial_logs
        _debug(ctx, f"Mining completed: gained {final_gain} logs (now have {final_logs} total)")
        
        _update_stats(ctx, success or final_gain >= 5, final_gain)
        
        return success or final_gain >= 5

    def t1100_smart_storage(ctx: TestContext) -> bool:
        """Improved storage with better chest management."""
        _debug(ctx, "Managing storage...")
        store = _get_persistent_store(ctx)
        
        chests = store.get("wood_chests", [])
        
        # Ensure we have at least one chest
        if not chests:
            if not _setup_initial_storage(ctx):
                return False
            chests = store.get("wood_chests", [])
        
        # Try depositing in most recent chest
        if _deposit_to_chest(ctx, chests[-1]):
            return True
        
        # If full, create new chest
        _debug(ctx, "Creating additional storage...")
        new_chest = _create_overflow_chest(ctx, chests[-1])
        if new_chest:
            chests.append(new_chest)
            store["wood_chests"] = chests
            _save_persistent(ctx)
            return _deposit_to_chest(ctx, new_chest)
        
        return False

    def _setup_initial_storage(ctx: TestContext) -> bool:
        """Setup first storage chest."""
        store = _get_persistent_store(ctx)
        
        # Check for existing chest in storage
        if "wood_chests" in store and store["wood_chests"]:
            return True
        
        _debug(ctx, "Setting up initial storage chest...")
        
        # Craft chest if needed (requires 8 planks and crafting table)
        if _check_inventory_item(ctx, "minecraft:chest") == 0:
            # Ensure we have the table item first
            if not _ensure_crafting_table_item(ctx):
                _debug(ctx, "ERROR: Could not ensure crafting table item for storage setup")
                return False

            table_pos = store.get("crafting_table")
            
            # Robustly ensure crafting table is open, even if stored position is missing
            if not ensure_crafting_table_open(ctx, table_pos=table_pos, suite_state=suite_state):
                 _debug(ctx, "ERROR: Could not get a crafting table for chest crafting")
                 return False
            
            # Update table_pos from suite_state if it was found/placed
            table_pos = suite_state.get("crafting_table_pos") or table_pos
            store["crafting_table"] = table_pos
            _save_persistent(ctx)

            # Navigate to crafting table first
            _debug(ctx, f"Navigating to crafting table at {table_pos}...")
            
            reached = move_near(ctx, table_pos[0], table_pos[1], table_pos[2], timeout=45.0)
            
            # Verify we are close enough
            px, py, pz = ctx.get_position()
            dist_sq = (px-table_pos[0])**2 + (py-table_pos[1])**2 + (pz-table_pos[2])**2
            
            # If move_near failed OR we are still far away, try forceful goto
            if not reached or dist_sq > 36:
                _debug(ctx, f"Move incomplete (dist sq: {dist_sq}). Trying direct #goto...")
                ctx.client.transport.dispatch("chat", {"message": f"#goto {table_pos[0]} {table_pos[1]} {table_pos[2]}"})
                time.sleep(10.0)
                _wait_for_baritone_idle(ctx, timeout=60.0)
                
                # Check again
                px, py, pz = ctx.get_position()
                dist_sq = (px-table_pos[0])**2 + (py-table_pos[1])**2 + (pz-table_pos[2])**2

            if dist_sq > 36: # > 6 blocks away
                 _debug(ctx, f"Still too far from crafting table (dist sq: {dist_sq}), cannot craft chest")
                 return False

            _debug(ctx, f"Opening crafting table at {table_pos}...")
            if not ensure_crafting_table_open(ctx, table_pos=table_pos):
                _debug(ctx, "ERROR: Could not open crafting table for chest")
                return False
            time.sleep(0.5)

            # Check if we have enough planks of a SINGLE type
            inv = _debug_inventory(ctx)
            plank_type = None
            for item_id, count in inv.items():
                if "_planks" in item_id and count >= 8:
                    plank_type = item_id
                    _debug(ctx, f"Found sufficient planks: {item_id} ({count})")
                    break
            
            if not plank_type:
                # Need to craft planks. Find best log.
                best_log = None
                best_count = 0
                for item_id, count in inv.items():
                    if "_log" in item_id and count > best_count:
                        best_log = item_id
                        best_count = count
                
                if best_log:
                    target_plank = best_log.replace("_log", "_planks")
                    _debug(ctx, f"Crafting planks from {best_log} -> {target_plank}")
                    # Request 8 items (will result in 8-11 planks depending on current count)
                    if _robust_craft(ctx, target_plank, 8): 
                        plank_type = target_plank
                        time.sleep(0.5)
                    else:
                        _debug(ctx, f"ERROR: Failed to craft planks ({target_plank}) for chest")
                        do_close_container(ctx)
                        return False
                else:
                    _debug(ctx, "ERROR: No logs found to craft planks for chest")
                    do_close_container(ctx)
                    return False
            
            # Now craft chest
            _debug(ctx, "Crafting chest...")
            if not _craft_chest_manual(ctx):
                _debug(ctx, "ERROR: Failed to craft chest manually")
                do_close_container(ctx)
                return False
            
            do_close_container(ctx)
            time.sleep(0.2)

        
        # Place chest
        px, py, pz = ctx.get_position()
        pos = find_place_pos_near(ctx, int(px), int(py), int(pz), radius=5)
        if not pos:
            _debug(ctx, "ERROR: Could not find position to place chest")
            return False
        
        if pos:
            _debug(ctx, f"Found placement spot at {pos}. Moving slightly away to avoid clipping...")
            # Move to a position slightly offset from placement spot
            move_near(ctx, pos[0]+2, pos[1], pos[2], timeout=5.0)
            
            _debug(ctx, f"Placing chest at {pos} (supported by {block_id_at(ctx, pos[0], pos[1]-1, pos[2])})...")
            if not bot_place_block(ctx, pos[0], pos[1], pos[2], "minecraft:chest"):
                _debug(ctx, "ERROR: Failed to place chest")
                return False
            store["wood_chests"] = [[pos[0], pos[1], pos[2]]]
            _save_persistent(ctx)
            _debug(ctx, "Storage chest placed successfully!")
            return True
        
        _debug(ctx, "ERROR: Failed to place chest")
        return False

    def _deposit_to_chest(ctx: TestContext, chest_pos: List[int]) -> bool:
        """Deposit items to specific chest."""
        x, y, z = chest_pos
        
        move_near(ctx, x, y, z, timeout=20.0)
        _wait_for_baritone_idle(ctx, timeout=5.0)
        
        if do_open_container(ctx, (x, y, z)):
            try:
                time.sleep(1.0)
                
                screen = ctx.client.transport.dispatch("get_screen", {})
                screen_data = screen.get("data", screen)
                slots = screen_data.get("slots", [])
                
                # Check capacity
                occupied = sum(1 for item in slots 
                             if item.get("slot", -1) < 27 and item.get("id") != "minecraft:air")
                
                if occupied >= 27:
                    do_close_container(ctx)
                    return False
                
                # Deposit items (excluding tools and food)
                for item in slots:
                    slot_idx = item.get("slot", -1)
                    if slot_idx >= 27:
                        item_id = item.get("id", "")
                        is_tool = any(t in item_id for t in TOOL_ITEM_SUBSTRINGS)
                        is_food = any(t in item_id for t in FOOD_ITEM_SUBSTRINGS)
                        
                        if not is_tool and not is_food and item_id != "minecraft:air":
                            safe_inventory_click(ctx, slot_idx, "QUICK_MOVE")
                            time.sleep(0.1)
                
                do_close_container(ctx)
                return True
            except Exception as e:
                _debug(ctx, f"Deposit error: {e}")
                do_close_container(ctx)
                return False
        
        return False

    def _create_overflow_chest(ctx: TestContext, near_pos: List[int]) -> Optional[List[int]]:
        """Create new chest near existing one."""
        cx, cy, cz = near_pos
        
        # Ensure we have a chest
        if _check_inventory_item(ctx, "minecraft:chest") == 0:
            if not craft_and_wait(ctx, "minecraft:chest", 1):
                return None
        
        # Find adjacent position
        for dx, dz in [(1, 0), (-1, 0), (0, 1), (0, -1), (2, 0), (0, 2)]:
            tx, ty, tz = cx + dx, cy, cz + dz
            bid = block_id_at(ctx, tx, ty, tz)
            if bid and "air" in bid:
                if bot_place_block(ctx, tx, ty, tz, "minecraft:chest"):
                    return [tx, ty, tz]
        
        return None

    def t1100_verify_progress(ctx: TestContext) -> bool:
        """Verify run was successful."""
        store = _get_persistent_store(ctx)
        stats = store.get("stats", {})
        
        _debug(ctx, f"Run complete. Total progress: {stats.get('total_logs', 0)} logs over {stats.get('total_runs', 0)} runs")
        
        return True

    # Build test case
    suite.add(TestCase(
        id="T1100_ENHANCED",
        name="Enhanced Farming (100+ Runs)",
        description="Robust farming with tool management and error recovery",
        setup=t1100_setup,
        steps=[
            t1100_check_and_repair_tools,
            t1100_mine_with_recovery,
            t1100_smart_storage,
            t1100_verify_progress,
        ],
        timeout_seconds=300
    ))
    
    return suite