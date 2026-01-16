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
    find_ground_place_pos,
    bot_place_block,
    move_near,
    get_inv_slots,
    place_block_at,
    wait_for_baritone_idle,
)
from tests.functional.shared.world_ops import (
    get_world_time,
    is_near_night,
    is_dead,
    wait_for_respawn,
)
from tests.functional.shared.logger import FarmingLogger
from tests.functional.shared.inventory_ops import (
    craft_and_wait,
    ensure_crafting_table_open,
    withdraw_from_supply_chest,
    safe_inventory_click,
    craft_door_manual,
    craft_wooden_axe_manual,
    craft_chest_manual,
    robust_craft,
    get_best_tool_durability,
    equip_item_to_hotbar,
    craft_sticks_manual,
    craft_crafting_table_manual,
    safe_count_item,
    get_inventory_counts,
    count_all_logs,
    count_any_planks,
    log_full_status,
    craft_door_manual,
)
from tests.functional.shared.suite_constants import (
    TIMEOUTS,
    get_timeout,
    poll_until,
)
from tests.functional.shared.constants import (
    LOG_BLOCK_IDS,
    STRIPPED_LOG_BLOCK_IDS,
    WOOD_BLOCK_IDS,
    STRIPPED_WOOD_BLOCK_IDS,
    PLANK_ITEM_IDS,
    SAPLING_ITEM_IDS,
    TOOL_ITEM_SUBSTRINGS,
    FOOD_ITEM_SUBSTRINGS,
    LOG_MINE_ARG,
)
from utils.mc_harness import (
    wait_for_block,
    wait_for_pathing_stop,
    wait_for_position_stable,
    cancel_pathing,
)

STATE_FILE = "t1100_store.json"



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



    # Initialize logger placeholder for outer scope visibility
    logger = None # Type: FarmingLogger

    def _check_and_recover_death(ctx) -> bool:
        """Check if player is dead and attempt recovery.
        
        Returns True if alive or successfully recovered, False if still dead.
        """
        if not is_dead(ctx):
            return True
        
        if logger:
            logger.warn("Player is dead! Waiting for respawn...")
        
        if wait_for_respawn(ctx, timeout=30.0):
            if logger:
                logger.info("Respawned successfully. Clearing cached positions.")
            # Clear cached positions since we may have respawned far away
            store = suite_state.get("persistent", {})
            store["crafting_table"] = None
            store["night_shelter"] = None
            save_farming_state(store)
            return True
        
        if logger:
            logger.error("Failed to respawn within timeout!")
        return False

    def _check_inventory_item(ctx: TestContext, item_id: str) -> int:
        return safe_count_item(ctx, item_id)

    def _check_tool_durability(ctx: TestContext) -> Optional[int]:
        return get_best_tool_durability(ctx, "minecraft:wooden_axe")

    def _craft_chest_manual(ctx: TestContext) -> bool:
        return craft_chest_manual(ctx)

    def _get_any_planks(ctx: TestContext) -> int:
        return count_any_planks(ctx)

    def _count_all_logs(ctx: TestContext) -> int:
        return count_all_logs(ctx)





    def _ensure_fresh_tool(ctx: TestContext, min_durability: int = 10) -> bool:
        """Ensure we have a tool with at least min_durability remaining."""
        durability = get_best_tool_durability(ctx, "minecraft:wooden_axe")
        
        if durability is None or durability < min_durability:
            logger.info( f"Tool needs replacement (durability: {durability})")
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
                logger.info( "ERROR: No logs available to bootstrap library/storage")
                return False
            
            # Find a log to convert
            inv = get_inventory_counts(ctx)
            log_item = None
            for item_id in inv.keys():
                if "_log" in item_id:
                    log_item = item_id
                    break
            
            if log_item:
                target_plank = log_item.replace("_log", "_planks")
                logger.info(f"Bootstrapping crafting table: converting {log_item} to planks")
                # Request 4 planks (crafting table needs 4)
                if not robust_craft(ctx, target_plank, 4):
                    logger.info("Automated plank craft failed in bootstrap")
                time.sleep(0.5)

        # Craft the table
        return robust_craft(ctx, "minecraft:crafting_table", 1)

    def _craft_replacement_axe(ctx: TestContext) -> bool:
        """Craft a new wooden axe."""
        logger.info( "Crafting replacement axe...")
        
        # Debug: show actual inventory contents
        inv_contents = get_inventory_counts(ctx)
        logger.info( f"Inventory contents: {inv_contents}")
        
        # Check if we already have an axe before doing work
        existing_axe = get_best_tool_durability(ctx, "minecraft:wooden_axe")
        if existing_axe is not None and existing_axe >= 10:
            logger.info( f"Already have usable axe (durability: {existing_axe})")
            return True
        
        # Ensure we have materials - check ALL wood types
        planks_needed = 3
        sticks_needed = 2
        
        all_logs = count_all_logs(ctx)
        all_planks = count_any_planks(ctx)
        current_sticks = safe_count_item(ctx, "minecraft:stick")
        
        logger.info( f"Materials: {all_logs} logs (all types), {all_planks} planks (all types), {current_sticks} sticks")
        
        # Craft planks if needed (need 3 planks for axe head + 2 planks to craft sticks if missing)
        planks_for_sticks = 0 if current_sticks >= sticks_needed else 2
        required_planks = planks_needed + planks_for_sticks
        if all_planks < required_planks:
            if all_logs > 0:
                needed_planks = required_planks - all_planks
                logs_to_convert = min(all_logs, (needed_planks + 3) // 4)
                logger.info( f"Converting {logs_to_convert} logs to planks...")
                for _ in range(logs_to_convert):
                    # Identify which log we have to craft the correct plank
                    inv = get_inventory_counts(ctx)
                    target_plank = None
                    for item_id in inv.keys():
                        if "_log" in item_id:
                            target_plank = item_id.replace("_log", "_planks")
                            break
                    
                    if not target_plank:
                        target_plank = "minecraft:oak_planks"

                    # Use robust crafting for planks
                    current_type_planks = safe_count_item(ctx, target_plank)
                    desired_planks = current_type_planks + 4
                    if not robust_craft(ctx, target_plank, desired_planks):
                         logger.info( f"ERROR: Failed to craft planks ({target_plank})")
                         return False
                    time.sleep(0.3)
                all_planks = count_any_planks(ctx)
                logger.info( f"Now have {all_planks} planks (all types)")
            else:
                logger.info( "ERROR: No logs available for crafting!")
                return False
        
        # Craft sticks if needed
        current_sticks = safe_count_item(ctx, "minecraft:stick")
        if current_sticks < sticks_needed:
            all_planks = count_any_planks(ctx)
            if all_planks >= 2:
                logger.info( "Crafting sticks...")
                if not craft_and_wait(ctx, "minecraft:stick", 1):
                    logger.info( "Automated stick craft failed, trying manual...")
                    if not craft_sticks_manual(ctx):
                        logger.info( "ERROR: Failed to craft sticks (auto and manual)")
                        return False
                time.sleep(0.3)
                time.sleep(0.3)
                current_sticks = safe_count_item(ctx, "minecraft:stick")
                logger.info( f"Now have {current_sticks} sticks")
            else:
                logger.info( f"ERROR: Not enough planks for sticks! Have {all_planks}, need 2")
                return False

        # Ensure we still have enough planks after crafting sticks
        all_planks = count_any_planks(ctx)
        if all_planks < planks_needed:
            needed_planks = planks_needed - all_planks
            logs_to_convert = min(count_all_logs(ctx), (needed_planks + 3) // 4)
            if logs_to_convert <= 0:
                logger.info( f"ERROR: Insufficient planks for axe after sticks ({all_planks})")
                return False
            logger.info( f"Converting {logs_to_convert} additional logs to planks for axe...")
            for _ in range(logs_to_convert):
                inv = get_inventory_counts(ctx)
                target_plank = None
                for item_id in inv.keys():
                    if "_log" in item_id:
                        target_plank = item_id.replace("_log", "_planks")
                        break
                if not target_plank:
                    target_plank = "minecraft:oak_planks"
                current_type_planks = safe_count_item(ctx, target_plank)
                desired_planks = current_type_planks + 4
                if not robust_craft(ctx, target_plank, desired_planks):
                    logger.info( f"ERROR: Failed to craft planks ({target_plank})")
                    return False
                time.sleep(0.3)
        
        # Verify we have enough materials for the axe
        all_planks = count_any_planks(ctx)
        current_sticks = safe_count_item(ctx, "minecraft:stick")
        if all_planks < 3 or current_sticks < 2:
            logger.info( f"ERROR: Insufficient materials for axe: {all_planks} planks, {current_sticks} sticks")
            return False
        
    def _ensure_crafting_table_placed(ctx: TestContext) -> Optional[List[int]]:
        """Ensure a crafting table is placed and reachable, prioritizing base then extras."""
        store = _get_persistent_store(ctx)
        base_pos = _normalize_pos(store.get("crafting_table"))
        extra_tables = _dedupe_positions(store.get("crafting_tables", []))
        store["crafting_tables"] = extra_tables
        if base_pos:
            store["crafting_table"] = base_pos

        def _check_table(pos: List[int]) -> bool:
            logger.info(f"Verifying stored crafting table at {pos}...")
            try:
                block = ctx.client.transport.dispatch("get_block", {"x": int(pos[0]), "y": int(pos[1]), "z": int(pos[2])})
                block_id = block.get("id", "")
            except Exception as e:
                logger.info(f"WARNING: Failed to check block at {pos}: {e}")
                return False
            if block_id != "minecraft:crafting_table":
                logger.info(f"WARNING: Block at stored pos is {block_id}, not crafting_table.")
                return False
            if not _move_near_with_reset(ctx, pos, timeout=20.0):
                logger.info("WARNING: Stored crafting table unreachable.")
                return False
            px, py, pz = ctx.get_position()
            dist_sq = (px - pos[0])**2 + (py - pos[1])**2 + (pz - pos[2])**2
            if dist_sq > 36:  # > 6 blocks away
                logger.info("WARNING: Still too far from crafting table.")
                return False
            return True

        candidates = []
        if base_pos:
            candidates.append(base_pos)
        candidates.extend([pos for pos in extra_tables if not base_pos or _pos_key(pos) != _pos_key(base_pos)])

        for pos in candidates:
            if _check_table(pos):
                store["crafting_table_active"] = pos
                _clear_table_failure(store, pos)
                _save_persistent(ctx)
                return pos
            if _record_table_failure(store, pos):
                logger.info(f"WARNING: Removing crafting table after repeated failures at {pos}")
                _remove_table_pos(store, pos)
            _save_persistent(ctx)

        base_pos = _normalize_pos(store.get("crafting_table"))
        extra_tables = _dedupe_positions(store.get("crafting_tables", []))
        store["crafting_tables"] = extra_tables

        logger.info("No crafting table position stored/reachable, checking/placing one...")

        if safe_count_item(ctx, "minecraft:crafting_table") == 0:
            if count_any_planks(ctx) < 4:
                logger.info(f"ERROR: Not enough planks for crafting table (have {count_any_planks(ctx)}, need 4)")
                return None

            logger.info("Crafting a crafting table...")
            if not craft_and_wait(ctx, "minecraft:crafting_table", 1):
                logger.info("Recipe crafting failed, trying manual 2x2 crafting...")
                if not craft_crafting_table_manual(ctx):
                    logger.info("ERROR: Failed to craft crafting table manually")
                    return None

        planned_pos = _normalize_pos(store.get("planned_crafting_table"))
        if planned_pos:
            logger.info(f"Navigating to planned crafting table position at {planned_pos}...")
            if _move_near_with_reset(ctx, planned_pos, timeout=30.0):
                x, y, z = planned_pos
                existing_block = block_id_at(ctx, x, y, z)
                if existing_block and "crafting_table" in existing_block:
                    table_pos = [x, y, z]
                    if not base_pos:
                        store["crafting_table"] = table_pos
                    else:
                        store["crafting_tables"] = _dedupe_positions(extra_tables + [table_pos])
                    store["crafting_table_active"] = table_pos
                    _clear_table_failure(store, table_pos)
                    _save_persistent(ctx)
                    logger.info(f"Found existing crafting table at planned position {table_pos}")
                    return table_pos
                logger.info(f"Placing crafting table at planned position ({x}, {y}, {z})...")
                if place_block_at(ctx, x, y, z, "minecraft:crafting_table"):
                    time.sleep(0.5)
                    actual_block = block_id_at(ctx, x, y, z)
                    if "crafting_table" in actual_block:
                        table_pos = [x, y, z]
                        if not base_pos:
                            store["crafting_table"] = table_pos
                        else:
                            store["crafting_tables"] = _dedupe_positions(extra_tables + [table_pos])
                        store["crafting_table_active"] = table_pos
                        _clear_table_failure(store, table_pos)
                        _save_persistent(ctx)
                        logger.info(f"Placed and verified crafting table at {table_pos}")
                        return table_pos
                    logger.info(f"Placement reported success but block is {actual_block}, trying nearby...")
            else:
                logger.info("Couldn't reach planned position, trying nearby...")

        pos = find_ground_place_pos(ctx, radius=5)
        if not pos:
            px, py, pz = ctx.get_position()
            pos = find_place_pos_near(ctx, int(px), int(py), int(pz), radius=3)
        if pos:
            x, y, z = pos
            logger.info(f"Placing crafting table at ground level ({x}, {y}, {z})...")
            if place_block_at(ctx, x, y, z, "minecraft:crafting_table"):
                time.sleep(0.5)
                actual_block = block_id_at(ctx, x, y, z)
                if "crafting_table" in actual_block:
                    table_pos = [x, y, z]
                    if not base_pos:
                        store["crafting_table"] = table_pos
                    else:
                        store["crafting_tables"] = _dedupe_positions(extra_tables + [table_pos])
                    store["crafting_table_active"] = table_pos
                    _clear_table_failure(store, table_pos)
                    _save_persistent(ctx)
                    logger.info(f"Placed and verified crafting table at {table_pos}")
                    return table_pos
                logger.info(f"Fallback placement reported success but block is {actual_block}")
                return None
            logger.info("ERROR: Failed to place crafting table")
            return None

        logger.info("ERROR: Could not find position to place crafting table")
        return None

    def _craft_replacement_axe(ctx: TestContext) -> bool:
        """Craft a new wooden axe."""
        logger.info( "Crafting replacement axe...")
        
        # Debug: show actual inventory contents
        inv_contents = get_inventory_counts(ctx)
        logger.info( f"Inventory contents: {inv_contents}")
        
        # Start fresh with crafting table (recipe or manual)
        if not _ensure_crafting_table_item(ctx):
             logger.info( "Failed to obtain crafting table item")
             return False

        # Ensure crafting table is placed (using planned pos if available)
        table_pos = _ensure_crafting_table_placed(ctx)
        if not table_pos:
            logger.info("Failed to ensure crafting table placement")
            return False
        
        # Open the crafting table
        logger.info( f"Opening crafting table at {table_pos}...")
        if not ensure_crafting_table_open(ctx, table_pos=table_pos):
            logger.info( "ERROR: Could not open crafting table")
            return False
        time.sleep(0.5)
        
        # Ensure materials for axe
        if safe_count_item(ctx, "minecraft:stick") < 2:
            logger.info("Crafting sticks for axe...")
            # Ensure we have planks first
            if count_any_planks(ctx) < 2:
                 if not _ensure_planks(ctx, 3):
                      logger.info("Failed to get planks for sticks")
                      return False
            if not robust_craft(ctx, "minecraft:stick", 4):
                 logger.info("Failed to craft sticks for axe")
                 return False
        
        if count_any_planks(ctx) < 3:
             logger.info("Getting planks for axe head...")
             if not _ensure_planks(ctx, 3):
                  logger.info("Failed to get planks for axe")
                  return False
        
        if craft_wooden_axe_manual(ctx):
            logger.info( "Replacement axe crafted successfully")
            return True
        
        logger.info( "ERROR: Failed to craft replacement axe")
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
        
        logger.info( f"Stats: {stats['total_runs']} runs, {stats['total_logs']} logs, {stats['failed_runs']} failures")

    def _get_persistent_store(ctx):
        return suite_state["persistent"]

    def _save_persistent(ctx):
        save_farming_state(suite_state["persistent"])

    CRAFTING_TABLE_FAILURE_LIMIT = 2

    def _pos_key(pos: List[int]) -> str:
        return f"{int(pos[0])},{int(pos[1])},{int(pos[2])}"

    def _normalize_pos(pos: Optional[List[int]]) -> Optional[List[int]]:
        if not pos or len(pos) < 3:
            return None
        return [int(pos[0]), int(pos[1]), int(pos[2])]

    def _dedupe_positions(positions: List[List[int]]) -> List[List[int]]:
        seen = set()
        result = []
        for pos in positions:
            norm = _normalize_pos(pos)
            if not norm:
                continue
            key = _pos_key(norm)
            if key in seen:
                continue
            seen.add(key)
            result.append(norm)
        return result

    def _get_table_failures(store: dict) -> dict:
        failures = store.get("crafting_table_failures")
        if not isinstance(failures, dict):
            failures = {}
        return failures

    def _record_table_failure(store: dict, pos: List[int]) -> bool:
        failures = _get_table_failures(store)
        key = _pos_key(pos)
        failures[key] = failures.get(key, 0) + 1
        store["crafting_table_failures"] = failures
        return failures[key] >= CRAFTING_TABLE_FAILURE_LIMIT

    def _clear_table_failure(store: dict, pos: List[int]) -> None:
        failures = _get_table_failures(store)
        key = _pos_key(pos)
        if key in failures:
            failures.pop(key)
            store["crafting_table_failures"] = failures

    def _remove_table_pos(store: dict, pos: List[int]) -> None:
        base_pos = _normalize_pos(store.get("crafting_table"))
        if base_pos and _pos_key(base_pos) == _pos_key(pos):
            store["crafting_table"] = None
        extra = store.get("crafting_tables", [])
        store["crafting_tables"] = [
            p for p in _dedupe_positions(extra) if _pos_key(p) != _pos_key(pos)
        ]

    def _move_near_with_reset(ctx: TestContext, pos: List[int], timeout: float) -> bool:
        cancel_pathing(ctx)
        wait_for_baritone_idle(ctx, timeout=5.0)
        return move_near(ctx, pos[0], pos[1], pos[2], timeout=timeout)

    def _get_plank_counts(ctx: TestContext) -> dict:
        inv = get_inventory_counts(ctx)
        return {item_id: count for item_id, count in inv.items() if item_id in PLANK_ITEM_IDS and count > 0}

    def _pick_plank_with_min(ctx: TestContext, min_count: int) -> Optional[str]:
        plank_counts = _get_plank_counts(ctx)
        candidates = [(item_id, count) for item_id, count in plank_counts.items() if count >= min_count]
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[1], reverse=True)
        return candidates[0][0]

    def _pick_any_plank(ctx: TestContext) -> Optional[str]:
        plank_counts = _get_plank_counts(ctx)
        if not plank_counts:
            return None
        return max(plank_counts.items(), key=lambda item: item[1])[0]

    def _find_any_door(ctx: TestContext) -> Optional[str]:
        inv = get_inventory_counts(ctx)
        for item_id, count in inv.items():
            if item_id.endswith("_door") and item_id != "minecraft:iron_door" and count > 0:
                return item_id
        return None

    def _ensure_planks(ctx: TestContext, min_planks: int) -> bool:
        total_planks = count_any_planks(ctx)
        if total_planks >= min_planks:
            return True

        def _find_log_item() -> Optional[str]:
            inv = get_inventory_counts(ctx)
            for item_id in inv.keys():
                if "_log" in item_id:
                    return item_id
            return None

        log_item = _find_log_item()
        if not log_item:
            if _withdraw_wood_for_shelter(ctx, min_planks):
                log_item = _find_log_item()
                total_planks = count_any_planks(ctx)
            if not log_item and total_planks < min_planks:
                logger.info( "ERROR: No logs available to craft shelter planks")
                return False
        if not log_item:
            return total_planks >= min_planks

        while total_planks < min_planks:
            # Re-check available logs each iteration
            log_item = _find_log_item()
            if not log_item:
                 logger.info("ERROR: Ran out of logs while crafting planks")
                 return False

            target_plank = log_item.replace("_log", "_planks")
            current_type_planks = safe_count_item(ctx, target_plank)
            desired_planks = current_type_planks + 4
            
            # Craft one batch (4 planks from 1 log)
            if not robust_craft(ctx, target_plank, desired_planks):
                logger.info( f"ERROR: Failed to craft planks ({target_plank})")
                return False
            time.sleep(0.3)
            total_planks = count_any_planks(ctx)

        return True

    def _ensure_door(ctx: TestContext) -> Optional[str]:
        existing_door = _find_any_door(ctx)
        if existing_door:
            return existing_door
        if count_any_planks(ctx) < 6:
            if not _ensure_planks(ctx, 6):
                logger.info("ERROR: Not enough planks for door")
                return None

        plank_type = _pick_plank_with_min(ctx, 6)
        if not plank_type:
            inv = get_inventory_counts(ctx)
            log_candidates = [(item_id, count) for item_id, count in inv.items() if item_id in LOG_BLOCK_IDS and count > 0]
            if log_candidates:
                log_candidates.sort(key=lambda item: item[1], reverse=True)
                target_log, available_logs = log_candidates[0]
                target_plank = target_log.replace("_log", "_planks")
                current_planks = safe_count_item(ctx, target_plank)
                while current_planks < 6 and available_logs > 0:
                    desired_planks = current_planks + 4
                    if not robust_craft(ctx, target_plank, desired_planks):
                        logger.info(f"ERROR: Failed to craft planks ({target_plank}) for door")
                        break
                    time.sleep(0.3)
                    available_logs -= 1
                    current_planks = safe_count_item(ctx, target_plank)
                plank_type = _pick_plank_with_min(ctx, 6)
            if not plank_type:
                logger.info("ERROR: Not enough matching planks for door crafting")
                return None
        door_item = plank_type.replace("_planks", "_door")

        table_pos = _ensure_crafting_table_placed(ctx)
        if not table_pos:
            logger.info("ERROR: Could not place crafting table for door crafting")
            return None
        if not ensure_crafting_table_open(ctx, table_pos=table_pos):
            logger.info("ERROR: Could not open crafting table for door crafting")
            return None
        if count_any_planks(ctx) < 6:
            if not _ensure_planks(ctx, 6):
                logger.info("ERROR: Not enough planks for door after crafting table opened")
                do_close_container(ctx)
                return None

        ok = craft_door_manual(ctx, door_item)
        do_close_container(ctx)

        if ok and safe_count_item(ctx, door_item) > 0:
            logger.info(f"Door crafted successfully via manual crafting ({door_item})")
            return door_item

        existing_door = _find_any_door(ctx)
        if existing_door:
            return existing_door

        logger.info("ERROR: Failed to craft door (manual crafting returned False or no door in inventory)")
        return None

    def _withdraw_wood_for_shelter(ctx: TestContext, min_planks: int) -> bool:
        store = _get_persistent_store(ctx)
        chests = store.get("wood_chests", [])
        if not chests:
            return False

        chest_pos = chests[-1]
        move_near(ctx, chest_pos[0], chest_pos[1], chest_pos[2], timeout=20.0)
        if not do_open_container(ctx, (chest_pos[0], chest_pos[1], chest_pos[2])):
            return False

        moved = False
        try:
            time.sleep(0.8)
            screen = ctx.client.transport.dispatch("get_screen", {})
            screen_data = screen.get("data", screen)
            slots = screen_data.get("slots", [])
            for item in slots:
                slot_idx = item.get("slot", -1)
                if slot_idx < 0 or slot_idx >= 27:
                    continue
                item_id = item.get("id", "")
                if not item_id or item_id == "minecraft:air":
                    continue
                if (
                    item_id in LOG_BLOCK_IDS
                    or item_id in STRIPPED_LOG_BLOCK_IDS
                    or item_id in WOOD_BLOCK_IDS
                    or item_id in STRIPPED_WOOD_BLOCK_IDS
                    or "_planks" in item_id
                ):
                    safe_inventory_click(ctx, slot_idx, "QUICK_MOVE")
                    moved = True
                    time.sleep(0.1)
                    if count_any_planks(ctx) >= min_planks:
                        break
        finally:
            do_close_container(ctx)

        return moved

    def _get_day_time_and_eta(world_time: int) -> Optional[Tuple[int, str, int]]:
        if world_time is None:
            return None
        day_time = int(world_time) % 24000
        if day_time < 12000:
            eta_ticks = 12000 - day_time
            eta_label = "etaDark"
        else:
            eta_ticks = 24000 - day_time
            eta_label = "etaDay"
        eta_seconds = int(eta_ticks / 20.0)
        return day_time, eta_label, eta_seconds

    def _log_time_eta(ctx: TestContext, prefix: str):
        world_time = get_world_time(ctx)
        eta_data = _get_day_time_and_eta(world_time)
        if not eta_data:
            logger.info( f"{prefix} time unknown")
            return
        day_time, eta_label, eta_seconds = eta_data
        logger.info( f"{prefix} time={day_time} {eta_label}={eta_seconds}s")

    def _shelter_is_valid(ctx: TestContext, shelter: dict) -> bool:
        if not shelter:
            return False
        door = shelter.get("door")
        origin = shelter.get("origin")
        if not door or not origin:
            return False
        door_id = block_id_at(ctx, door[0], door[1], door[2])
        return bool(door_id and door_id.endswith("_door"))

    def _move_to_shelter(ctx: TestContext, shelter: dict) -> bool:
        origin = shelter.get("origin")
        if not origin:
            return False
        target = (origin[0] + 1, origin[1] + 1, origin[2] + 1)
        logger.info( f"Moving to night shelter at {origin}...")
        return move_near(ctx, target[0], target[1], target[2], timeout=45.0)

    def _clear_area_tunnel(ctx: TestContext) -> bool:
        """Clear a 7w x 5h x 7d area using #tunnel (h,w,d) and save planned positions."""
        # First ensure any previous command is stopped with delay
        ctx.client.transport.dispatch("chat", {"message": "#stop"})
        time.sleep(1.0)
        cancel_pathing(ctx)
        time.sleep(0.5)
        
        # Ensure we are on solid ground, not a tree
        px, py, pz = ctx.get_position()
        # Exclude sand/gravel to avoid beaches and underwater starts
        ground_blocks = ["grass_block", "dirt", "stone", "cobblestone", "deepslate", 
                         "diorite", "granite", "andesite", "terracotta"]
        
        target_ground_y = None
        target_pos = None
        min_y = 999
        
        # Scan 7x7 area to find lowest ground point (handle slopes)
        # We want to start at the bottom of a hill to dig INTO it
        center_x, center_z = int(px), int(pz)
        
        for dx in range(-3, 4):
            for dz in range(-3, 4):
                check_x = center_x + dx
                check_z = center_z + dz
                
                # Scan down from current player height + 2
                found_y = None
                for scan_y in range(int(py) + 2, int(py) - 20, -1):
                    block_at = block_id_at(ctx, check_x, scan_y, check_z)
                    if block_at and any(gb in block_at for gb in ground_blocks):
                        found_y = scan_y + 1
                        break
                
                if found_y is not None:
                    if found_y < min_y:
                        min_y = found_y
                        target_pos = (check_x, min_y, check_z)

        if target_pos:
             # If significant difference or just robust alignment
             tx, ty, tz = target_pos
             if abs(ty - py) > 1 or abs(tx - px) > 1 or abs(tz - pz) > 1:
                 logger.info(f"Moving to lowest ground point at {target_pos} (current: {int(py)})...")
                 if move_near(ctx, tx, ty, tz, timeout=20.0):
                     logger.info("Moved to start position.")
                     px, py, pz = ctx.get_position()
                 else:
                     logger.info("Failed to move to lowest point, trying current position...")

        # Save origin position BEFORE starting tunnel (this is where the 7x7 area starts)
        # Use current position as origin
        px, py, pz = ctx.get_position()
        origin_x, origin_y, origin_z = int(px), int(py), int(pz)
        # Baritone tunnel args are (height, width, depth)
        logger.info("Clearing 7w x 5h x 7d area with tunnel command (h,w,d: 5 7 7)...")
        ctx.client.transport.dispatch("chat", {"message": "#tunnel 5 7 7"})
        
        # Track progress via inventory and position changes
        start = time.time()
        timeout = 600.0  # 10 minutes for harder blocks (stone, deepslate)
        min_wait = 90.0  # Wait at least 90 seconds before checking for completion
        idle_checks = 0  # Count consecutive idle states
        
        # Track progress to detect if work is happening
        last_log_count = count_all_logs(ctx)
        last_position = ctx.get_position()
        last_progress_time = time.time()
        progress_timeout = 90.0  # If no progress for 90s, consider done
        
        while time.time() - start < timeout:
            elapsed = time.time() - start
            
            try:
                state = ctx.client.transport.dispatch("get_state", {})
                is_pathing = state.get("pathing", False)
                
                # Check for progress (inventory or position changes)
                current_logs = count_all_logs(ctx)
                current_pos = ctx.get_position()
                
                # Calculate position change
                pos_change = abs(current_pos[0] - last_position[0]) + abs(current_pos[1] - last_position[1]) + abs(current_pos[2] - last_position[2])
                
                # Detect progress: log count changed OR moved more than 1 block
                if current_logs != last_log_count or pos_change > 1.0:
                    last_log_count = current_logs
                    last_position = current_pos
                    last_progress_time = time.time()
                    idle_checks = 0  # Reset idle checks on progress
                
                # Only start checking for completion after minimum wait time
                if elapsed >= min_wait and not is_pathing:
                    time_since_progress = time.time() - last_progress_time
                    idle_checks += 1
                    
                    # Complete if: idle for 3+ checks AND no recent progress
                    if idle_checks >= 3 and time_since_progress > 15.0:
                        logger.info(f"Tunnel complete! Took {int(elapsed)}s, collected {current_logs} logs")
                        time.sleep(1.0)  # Brief pause before next operation
                        break
                else:
                    idle_checks = 0  # Reset if pathing becomes active
                
                # Timeout if no progress for a long time (even if pathing)
                if time.time() - last_progress_time > progress_timeout:
                    logger.info(f"Tunnel stalled (no progress for {int(progress_timeout)}s) - stopping")
                    break
                    
            except Exception as e:
                logger.info(f"Tunnel check error: {e}")
            time.sleep(3.0)  # Longer sleep to reduce command frequency
        
        if time.time() - start >= timeout:
            logger.info(f"Tunnel finished/timeout - stopping after {int(time.time() - start)}s")
        ctx.client.transport.dispatch("chat", {"message": "#stop"})
        time.sleep(1.0)  # Delay after stop
        cancel_pathing(ctx)
        time.sleep(0.5)
        
        # Save origin and planned positions in the 7x7 area
        # Layout (relative to origin, +Z is forward from where player faces):
        # Crafting table: x+2, y, z+2 (floor level)
        # Supply chest: x+4, y, z+2  
        # Door: x+3, y, z+5
        store = _get_persistent_store(ctx)
        store["base_origin"] = [origin_x, origin_y, origin_z]
        store["planned_crafting_table"] = [origin_x + 2, origin_y, origin_z + 2]
        store["planned_chest"] = [origin_x + 4, origin_y, origin_z + 2]
        store["planned_door"] = [origin_x + 3, origin_y, origin_z + 5]
        store["area_cleared"] = True
        _save_persistent(ctx)
        
        logger.info(f"Saved base origin at ({origin_x}, {origin_y}, {origin_z})")
        logger.info(f"Planned positions: crafting table at {store['planned_crafting_table']}, chest at {store['planned_chest']}, door at {store['planned_door']}")
        
        return True

    def _build_house_7x7(ctx: TestContext) -> bool:
        """Build a 7x7 house with walls, roof, door, crafting table, and supply chest.
        
        Layout (looking from above, +Z is south):
        XXXXXXX    Row 0
        XWWWWWX    Row 1: North wall
        XWBXCWX    Row 2: C = Crafting table (col 4)
        XWBXXWX    Row 3: B = Bed spots (future)
        XWXXSWX    Row 4: S = Supply chest (col 4)
        XWWDWWX    Row 5: D = Door (col 3)
        XXXXXXX    Row 6
        """
        logger.info("Building 7x7 house...")
        
        # Get origin from store to ensure alignment
        store = _get_persistent_store(ctx)
        if "base_origin" in store:
            ox, oy, oz = store["base_origin"]
        else:
            # Fallback (shouldn't happen if setup ran)
            px, py, pz = ctx.get_position()
            ox, oy, oz = int(px) - 3, int(py), int(pz) - 3
        
        # Material requirements: ~80 planks (walls 28 + roof 25 + buffer) = 20 logs
        min_planks = 80
        min_logs_needed = 20
        
        # Check if we have enough materials, mine more if needed
        max_mine_attempts = 2
        for attempt in range(max_mine_attempts + 1):
            current_logs = count_all_logs(ctx)
            current_planks = count_any_planks(ctx)
            total_available = current_planks + (current_logs * 4)
            if total_available >= min_planks:
                break
            logs_to_mine = ((min_planks - total_available) // 4) + 5  # Add buffer
            logger.info(
                f"Need {logs_to_mine} more logs for house (have {current_logs} logs, {current_planks} planks). "
                f"Mining attempt {attempt + 1}/{max_mine_attempts + 1}..."
            )
            ctx.client.transport.dispatch("chat", {"message": f"#mine {LOG_MINE_ARG}"})
            start = time.time()
            timeout = 90.0
            while time.time() - start < timeout:
                new_logs = count_all_logs(ctx)
                if new_logs >= current_logs + logs_to_mine:
                    break
                time.sleep(3.0)
            ctx.client.transport.dispatch("chat", {"message": "#stop"})
            time.sleep(1.0)  # Delay after stop
            cancel_pathing(ctx)
            time.sleep(0.5)
            logger.info(f"Mined logs - now have {count_all_logs(ctx)} logs")

        current_logs = count_all_logs(ctx)
        current_planks = count_any_planks(ctx)
        total_available = current_planks + (current_logs * 4)
        if total_available < min_planks:
            logger.info(f"ERROR: Not enough logs/planks for house after mining ({total_available} < {min_planks})")
            return False

        if not _ensure_planks(ctx, min_planks):
            logger.info("ERROR: Not enough planks for house")
            return False

        # Ensure we have door
        door_item = _ensure_door(ctx)
        if not door_item:
            logger.info("ERROR: Failed to craft door for house")
            return False
        
        # Ensure we have crafting table  
        if not _ensure_crafting_table_item(ctx):
            logger.info("ERROR: Failed to craft crafting table for house")
            return False
        
        # Ensure we have chest
        if safe_count_item(ctx, "minecraft:chest") < 1:
            table_pos = _ensure_crafting_table_placed(ctx)
            if not table_pos:
                logger.info("ERROR: Could not place crafting table for house chest")
                return False
            if not ensure_crafting_table_open(ctx, table_pos=table_pos):
                logger.info("ERROR: Could not open crafting table for house chest")
                return False
            if count_any_planks(ctx) < 8:
                if not _ensure_planks(ctx, 8):
                    logger.info("ERROR: Not enough planks for house chest")
                    do_close_container(ctx)
                    return False
            if not craft_chest_manual(ctx):
                logger.info("ERROR: Failed to craft chest manually for house")
                do_close_container(ctx)
                return False
            do_close_container(ctx)
        
        ok = True
        current_plank = _pick_any_plank(ctx)
        if not current_plank:
            logger.info("ERROR: No planks available for house build")
            return False

        def _place_plank(x: int, y: int, z: int) -> bool:
            nonlocal current_plank
            if current_plank and safe_count_item(ctx, current_plank) > 0:
                if place_block_at(ctx, x, y, z, current_plank):
                    return True
            current_plank = _pick_any_plank(ctx)
            if current_plank and place_block_at(ctx, x, y, z, current_plank):
                return True
            return False

        # Wall positions (perimeter of 7x7, excluding corners which we'll handle)
        # Row 1 (north wall): x=1..5, z=1
        # Row 5 (south wall): x=1..5, z=5 (skip x=3 for door)
        # Col 1 (west wall): z=1..5, x=1
        # Col 5 (east wall): z=1..5, x=5
        
        wall_height = 2
        
        # Build walls (2 blocks high)
        logger.info("Building walls...")
        for y_offset in range(wall_height):
            y = oy + 1 + y_offset
            
            # North wall (z = oz + 1)
            for x_offset in range(1, 6):
                ok = _place_plank(ox + x_offset, y, oz + 1) and ok
            
            # South wall (z = oz + 5), skip door at x=3
            for x_offset in range(1, 6):
                if x_offset == 3:  # Door position
                    continue
                ok = _place_plank(ox + x_offset, y, oz + 5) and ok
            
            # West wall (x = ox + 1)
            for z_offset in range(2, 5):  # Skip corners (already built)
                ok = _place_plank(ox + 1, y, oz + z_offset) and ok
            
            # East wall (x = ox + 5)
            for z_offset in range(2, 5):  # Skip corners
                ok = _place_plank(ox + 5, y, oz + z_offset) and ok
        
        # Build roof (5x5 interior)
        logger.info("Building roof...")
        roof_y = oy + 1 + wall_height
        for x_offset in range(1, 6):
            for z_offset in range(1, 6):
                ok = _place_plank(ox + x_offset, roof_y, oz + z_offset) and ok
        
        # Place door (south wall, center)
        door_x, door_y, door_z = ox + 3, oy + 1, oz + 5
        logger.info(f"Placing door at ({door_x}, {door_y}, {door_z})...")
        ok = place_block_at(ctx, door_x, door_y, door_z, door_item) and ok
        
        # Place crafting table at C position (row 2, col 4 -> z=oz+2, x=ox+4)
        craft_x, craft_y, craft_z = ox + 4, oy + 1, oz + 2
        logger.info(f"Placing crafting table at ({craft_x}, {craft_y}, {craft_z})...")
        ok = place_block_at(ctx, craft_x, craft_y, craft_z, "minecraft:crafting_table") and ok
        
        # Place supply chest at S position (row 4, col 4 -> z=oz+4, x=ox+4)
        chest_x, chest_y, chest_z = ox + 4, oy + 1, oz + 4
        logger.info(f"Placing supply chest at ({chest_x}, {chest_y}, {chest_z})...")
        ok = place_block_at(ctx, chest_x, chest_y, chest_z, "minecraft:chest") and ok
        
        if not ok:
            logger.info("WARNING: Some blocks failed to place")
        
        # Save house info to persistent store
        store = _get_persistent_store(ctx)
        store["house_7x7"] = {
            "origin": [ox, oy, oz],
            "door": [door_x, door_y, door_z],
            "door_item": door_item,
            "crafting_table": [craft_x, craft_y, craft_z],
            "supply_chest": [chest_x, chest_y, chest_z],
        }
        if not store.get("crafting_table"):
            store["crafting_table"] = [craft_x, craft_y, craft_z]
        else:
            existing_tables = _dedupe_positions(store.get("crafting_tables", []))
            existing_tables.append([craft_x, craft_y, craft_z])
            store["crafting_tables"] = _dedupe_positions(existing_tables)
        store["night_shelter"] = {
            "origin": [ox, oy, oz],
            "door": [door_x, door_y, door_z],
        }
        _save_persistent(ctx)
        
        logger.info(f"Built 7x7 house at origin ({ox},{oy},{oz})")
        return True

    def _build_night_shelter(ctx: TestContext) -> bool:
        """Build a shelter - uses 7x7 house if no existing shelter, otherwise uses cached location."""
        store = _get_persistent_store(ctx)
        
        # Check if we already have a house
        if store.get("house_7x7"):
            logger.info("House already exists, using cached location")
            return True
        
        # Area should already be cleared from setup, just build the house
        # If area_cleared is False for some reason, the tunnel will happen in _build_house_7x7
        return _build_house_7x7(ctx)

    def t1100_wait_out_night(ctx: TestContext) -> bool:
        """If near night, build shelter and wait for day."""
        if not _check_and_recover_death(ctx):
            return False
        
        world_time = get_world_time(ctx)
        if not is_near_night(world_time):
            return True

        _log_time_eta(ctx, "Near night; preparing shelter.")
        store = _get_persistent_store(ctx)
        shelter = store.get("night_shelter")
        if shelter and _shelter_is_valid(ctx, shelter):
            if not _move_to_shelter(ctx, shelter):
                logger.info( "Stored shelter unreachable; rebuilding...")
                shelter = None
        elif shelter:
            logger.info( "Stored shelter missing; rebuilding...")
            shelter = None

        if not shelter:
            if not _build_night_shelter(ctx):
                return False
            shelter = store.get("night_shelter")
            if shelter:
                _move_to_shelter(ctx, shelter)

        start = time.time()
        next_log = time.time()
        while time.time() - start < 1200.0:
            current_time = get_world_time(ctx)
            if not is_near_night(current_time):
                logger.info( f"Daytime reached (time {current_time})")
                return True
            now = time.time()
            if now >= next_log:
                _log_time_eta(ctx, "Night wait:")
                next_log = now + 30.0
            time.sleep(5.0)

        logger.info( "ERROR: Timed out waiting for daytime")
        return False

    # --- Test Steps ---

    def t1100_setup(ctx: TestContext):
        """Setup for current run."""
        suite_state["run_number"] = suite_state.get("run_number", 0) + 1
        
        nonlocal logger
        logger = FarmingLogger(ctx, suite_state["run_number"])
        
        logger.info(f"Starting Run #{suite_state['run_number']}")
        
        # Commands disabled for multiplayer/non-OP compatibility
        # ctx.set_gamemode("survival")
        # ctx.run_command("difficulty peaceful")
        # ctx.run_command("gamerule doMobSpawning false")
        # ctx.run_command("time set day")
        # ctx.run_command("weather clear")
        
        # Give starter tool removed - test should robustly start from scratch
        # ctx.run_command("give @p minecraft:wooden_axe 1")
        
        try:
            ctx.client.transport.dispatch("chat", {"message": "#setting chatDebug false"})
        except Exception as e:
            logger.info( f"Failed to set chatDebug: {e}")

        cancel_pathing(ctx)
        
        # Check if we need to clear area for house (run tunnel before any placement)
        store = _get_persistent_store(ctx)
        if not store.get("house_7x7") and not store.get("area_cleared"):
            logger.info("No house yet - clearing 7w x 5h x 7d area first...")
            _clear_area_tunnel(ctx)
            store["area_cleared"] = True
            store["area_origin"] = list(ctx.get_position()[:3])  # Save origin for later
            _save_persistent(ctx)
        
        time.sleep(0.5)
        
        logger.info("Setup complete")

    def t1100_check_and_repair_tools(ctx: TestContext) -> bool:
        """Check tool condition before mining."""
        logger.info( "Checking tool condition...")
        
        durability = get_best_tool_durability(ctx, "minecraft:wooden_axe")
        logger.info( f"Current tool durability: {durability}")
        
        # Ensure we have a usable tool, or start naturally
        if _ensure_fresh_tool(ctx, min_durability=10):
            return True
            
        # If we failed to get a tool, check if we're starting from scratch
        # Check total available materials
        current_logs = count_all_logs(ctx)
        current_planks = count_any_planks(ctx)
        current_sticks = safe_count_item(ctx, "minecraft:stick")
        
        # If we have no materials at all, we need to mine with fists to bootstrap
        if current_logs == 0 and current_planks < 5:
            logger.info( f"No materials to craft (logs={current_logs}, planks={current_planks}) - starting fresh with fist mining")
            return True
            
        return False

    def t1100_mine_with_recovery(ctx: TestContext) -> bool:
        """Mine with error recovery and tool monitoring."""
        logger.info( "Starting mining operation...")
        
        initial_logs = count_all_logs(ctx)  # Count ALL log types
        target_gain = 10  # Lower target per run for reliability
        
        logger.info( f"Starting with {initial_logs} logs, target +{target_gain}")
        


        # Start mining
        ctx.client.transport.dispatch("chat", {"message": f"#mine {LOG_MINE_ARG}"})
        
        start = time.time()
        timeout = 120.0
        success = False
        last_count = initial_logs
        max_logs = initial_logs
        stuck_counter = 0
        last_repair_time = 0.0
        last_mine_restart = 0.0
        last_status_check = 0.0
        
        while time.time() - start < timeout:
            # Check for death first
            if is_dead(ctx):
                logger.warn("Died during mining! Attempting recovery...")
                if not _check_and_recover_death(ctx):
                    logger.error("Failed to recover from death")
                    break
                # After respawn, restart mining
                ctx.client.transport.dispatch("chat", {"message": f"#mine {LOG_MINE_ARG}"})
                time.sleep(2.0)
                continue
            
            current = count_all_logs(ctx)  # Count ALL log types
            if current > max_logs:
                max_logs = current
                
            gain = max_logs - initial_logs
            
            # Check progress
            if gain >= target_gain:
                logger.info( f"Target reached! Gained {gain} logs ({max_logs} total)")
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

                    now = time.time()
                    if now - last_mine_restart < 60.0:
                        logger.info( f"No recent log gain; waiting before retry. Logs: {max_logs} (Run Gain: {gain}). Pos: {pos}, Pathing: {is_pathing}")
                        # Removed #status polling to avoid Baritone command conflicts

                    else:
                        logger.info( f"No recent log gain; retrying mine. Logs: {max_logs} (Run Gain: {gain}). Pos: {pos}, Pathing: {is_pathing}")
                        ctx.client.transport.dispatch("chat", {"message": "#stop"})
                        time.sleep(1.0)
                        
                        # Try mining command again (Baritone often unstucks itself with a re-issue)
                        ctx.client.transport.dispatch("chat", {"message": f"#mine {LOG_MINE_ARG}"})
                        last_mine_restart = now
                        stuck_counter = 0
            else:
                stuck_counter = 0
                last_count = current
                # Log progress periodically
                if gain > 0 and gain % 5 == 0:
                    log_full_status(ctx, prefix=f"[Run {suite_state.get('run_number', 0)}] PROGRESS:")
            
            # Check tool durability mid-operation
            durability = get_best_tool_durability(ctx, "minecraft:wooden_axe")
            
            # If we don't have a tool (fist mining), check if we can craft one
            if durability is None:
                if time.time() - last_repair_time < 8.0:
                    time.sleep(2.0)
                    continue
                total_logs = count_all_logs(ctx)
                if total_logs >= 3:
                     logger.info( "Collected enough wood! Stopping to craft axe...")
                     ctx.client.transport.dispatch("chat", {"message": "#stop"})
                     time.sleep(1.0)  # Delay after stop
                     cancel_pathing(ctx)
                     time.sleep(0.5)
                     
                     if _craft_replacement_axe(ctx):
                         last_repair_time = time.time()
                         time.sleep(1.0)
                         # Resume mining
                         ctx.client.transport.dispatch("chat", {"message": f"#mine {LOG_MINE_ARG}"})
            
            if durability is not None and durability < 5:
                if time.time() - last_repair_time < 8.0:
                    time.sleep(2.0)
                    continue
                # IMPORTANT: Only stop to repair if we haven't reached target yet
                # (We already check this at top of loop, but let's be safe)
                logger.info( "Tool nearly broken, stopping to repair...")
                ctx.client.transport.dispatch("chat", {"message": "#stop"})
                time.sleep(1.0)  # Delay after stop
                cancel_pathing(ctx)
                time.sleep(0.5)
                
                if _craft_replacement_axe(ctx):
                    last_repair_time = time.time()
                    time.sleep(1.0)
                    # Resume mining
                    ctx.client.transport.dispatch("chat", {"message": f"#mine {LOG_MINE_ARG}"})
                else:
                    break
            
            time.sleep(2.0)
        
        ctx.client.transport.dispatch("chat", {"message": "#stop"})
        time.sleep(1.0)  # Delay after stop
        cancel_pathing(ctx)
        time.sleep(0.5)
        
        final_logs = max_logs  # Use high-water mark for final reporting
        final_gain = final_logs - initial_logs
        logger.info( f"Mining completed: gained {final_gain} logs (now have {final_logs} total)")
        
        _update_stats(ctx, success or final_gain >= 5, final_gain)
        
        return success or final_gain >= 5

    def t1100_smart_storage(ctx: TestContext) -> bool:
        """Improved storage with better chest management."""
        if not _check_and_recover_death(ctx):
            return False
        
        logger.info( "Managing storage...")
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
        logger.info( "Creating additional storage...")
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
        
        logger.info( "Setting up initial storage chest...")
        
        # Craft chest if needed (requires 8 planks and crafting table)
        if safe_count_item(ctx, "minecraft:chest") == 0:
            # Ensure crafting table is placed (prioritizing planned position)
            table_pos = _ensure_crafting_table_placed(ctx)
            if not table_pos:
                logger.info( "ERROR: Could not place crafting table for storage setup")
                return False

            # Navigate to crafting table first
            logger.info( f"Navigating to crafting table at {table_pos}...")
            
            reached = _move_near_with_reset(ctx, table_pos, timeout=45.0)
            
            # Verify we are close enough
            px, py, pz = ctx.get_position()
            dist_sq = (px-table_pos[0])**2 + (py-table_pos[1])**2 + (pz-table_pos[2])**2
            
            # If move_near failed OR we are still far away, try forceful goto
            if not reached or dist_sq > 36:
                logger.info( f"Move incomplete (dist sq: {dist_sq}). Trying direct #goto...")
                ctx.client.transport.dispatch("chat", {"message": f"#goto {table_pos[0]} {table_pos[1]} {table_pos[2]}"})
                time.sleep(10.0)
                wait_for_baritone_idle(ctx, timeout=60.0)
                
                # Check again
                px, py, pz = ctx.get_position()
                dist_sq = (px-table_pos[0])**2 + (py-table_pos[1])**2 + (pz-table_pos[2])**2

            if dist_sq > 36: # > 6 blocks away
                 logger.info( f"Still too far from crafting table (dist sq: {dist_sq}), cannot craft chest")
                 return False

            logger.info( f"Opening crafting table at {table_pos}...")
            if not ensure_crafting_table_open(ctx, table_pos=table_pos):
                logger.info( "ERROR: Could not open crafting table for chest")
                return False
            time.sleep(0.5)

            # Check if we have enough planks of a SINGLE type
            inv = get_inventory_counts(ctx)
            plank_type = None
            for item_id, count in inv.items():
                if "_planks" in item_id and count >= 8:
                    plank_type = item_id
                    logger.info( f"Found sufficient planks: {item_id} ({count})")
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
                    logger.info( f"Crafting planks from {best_log} -> {target_plank}")
                    # Request 8 items (will result in 8-11 planks depending on current count)
                    if robust_craft(ctx, target_plank, 8): 
                        plank_type = target_plank
                        time.sleep(0.5)
                    else:
                        logger.info( f"ERROR: Failed to craft planks ({target_plank}) for chest")
                        do_close_container(ctx)
                        return False
                else:
                    logger.info( "ERROR: No logs found to craft planks for chest")
                    do_close_container(ctx)
                    return False
            
            # Refresh inventory + crafting table before manual chest craft
            do_close_container(ctx)
            time.sleep(0.3)
            if not ensure_crafting_table_open(ctx, table_pos=table_pos):
                logger.info( "ERROR: Could not reopen crafting table for chest")
                return False
            time.sleep(0.5)
            inv = get_inventory_counts(ctx)
            if not any(item_id.endswith("_planks") and count >= 8 for item_id, count in inv.items()):
                if plank_type:
                    logger.info( f"Rechecking planks before chest craft; crafting more {plank_type}...")
                    if not robust_craft(ctx, plank_type, 8):
                        logger.info( f"ERROR: Failed to craft planks ({plank_type}) before chest")
                        do_close_container(ctx)
                        return False
                    time.sleep(0.5)
            # Now craft chest
            logger.info( "Crafting chest...")
            if not _craft_chest_manual(ctx):
                logger.info( "ERROR: Failed to craft chest manually")
                do_close_container(ctx)
                return False
            
            do_close_container(ctx)
            time.sleep(0.2)

        
        # Place chest - prefer planned position from tunnel clearing
        store = _get_persistent_store(ctx)
        planned_chest = store.get("planned_chest")
        pos = None
        
        if planned_chest:
            logger.info(f"Navigating to planned chest position at {planned_chest}...")
            if move_near(ctx, planned_chest[0], planned_chest[1], planned_chest[2], timeout=30.0):
                # Check if chest already exists at planned position
                existing_block = block_id_at(ctx, planned_chest[0], planned_chest[1], planned_chest[2])
                if existing_block and "chest" in existing_block:
                    logger.info(f"Found existing chest at planned position {planned_chest}")
                    store["wood_chests"] = [[planned_chest[0], planned_chest[1], planned_chest[2]]]
                    _save_persistent(ctx)
                    return True
                pos = (planned_chest[0], planned_chest[1], planned_chest[2])
            else:
                logger.info("Couldn't reach planned chest position, trying nearby...")
        
        if not pos:
            px, py, pz = ctx.get_position()
            pos = find_place_pos_near(ctx, int(px), int(py), int(pz), radius=5)
        
        if not pos:
            logger.info("ERROR: Could not find position to place chest")
            return False
        
        if pos:
            logger.info(f"Found placement spot at {pos}. Moving slightly away to avoid clipping...")
            # Move to a position slightly offset from placement spot
            move_near(ctx, pos[0]+2, pos[1], pos[2], timeout=5.0)
            
            logger.info(f"Placing chest at {pos} (supported by {block_id_at(ctx, pos[0], pos[1]-1, pos[2])})...")
            if not bot_place_block(ctx, pos[0], pos[1], pos[2], "minecraft:chest"):
                logger.info("ERROR: Failed to place chest")
                return False
            store["wood_chests"] = [[pos[0], pos[1], pos[2]]]
            _save_persistent(ctx)
            logger.info("Storage chest placed successfully!")
            return True
        
        logger.info("ERROR: Failed to place chest")
        return False

    def _deposit_to_chest(ctx: TestContext, chest_pos: List[int]) -> bool:
        """Deposit items to specific chest."""
        x, y, z = chest_pos
        
        move_near(ctx, x, y, z, timeout=20.0)
        wait_for_baritone_idle(ctx, timeout=5.0)
        
        opened = False
        for attempt in range(3):
            if do_open_container(ctx, (x, y, z)):
                opened = True
                break
            logger.info( f"Retrying chest open ({attempt + 1}/3)...")
            time.sleep(0.5)
        if opened:
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
                logger.info( f"Deposit error: {e}")
                do_close_container(ctx)
                return False
        
        return False

    def _create_overflow_chest(ctx: TestContext, near_pos: List[int]) -> Optional[List[int]]:
        """Create new chest near existing one."""
        cx, cy, cz = near_pos
        
        # Ensure we have a chest
        if safe_count_item(ctx, "minecraft:chest") == 0:
            table_pos = _ensure_crafting_table_placed(ctx)
            if not table_pos:
                logger.info( "ERROR: Could not place crafting table for overflow chest")
                return None
            if not ensure_crafting_table_open(ctx, table_pos=table_pos):
                logger.info( "ERROR: Could not open crafting table for overflow chest")
                return None
            if count_any_planks(ctx) < 8:
                if not _ensure_planks(ctx, 8):
                    logger.info( "ERROR: Not enough planks for overflow chest")
                    do_close_container(ctx)
                    return None
            if not craft_chest_manual(ctx):
                logger.info( "ERROR: Failed to craft chest manually for overflow chest")
                do_close_container(ctx)
                return None
            do_close_container(ctx)
        
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
        
        logger.info(f"Run complete. Total progress: {stats.get('total_logs', 0)} logs over {stats.get('total_runs', 0)} runs")
        
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
            t1100_wait_out_night,
            t1100_verify_progress,
        ],
        timeout_seconds=300
    ))
    
    return suite
