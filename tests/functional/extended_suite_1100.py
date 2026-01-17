"""
Extended Suite 1100: Enhanced for 100+ Consecutive Runs
Improvements:
- Tool durability monitoring and auto-replacement
- Resource depletion handling
- Better error recovery
- Run statistics tracking
"""

import json
import math
import os
import sqlite3
import time
from typing import Tuple, Optional, List, Dict
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
    get_entities,
    select_hotbar_item,
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

    def _count_empty_inventory_slots(ctx: TestContext) -> int:
        try:
            inv = ctx.get_inventory()
        except Exception:
            return 0
        slots = get_inv_slots(inv)
        empty = 0
        for slot in slots:
            if not slot:
                empty += 1
                continue
            if slot.get("id") in (None, "", "minecraft:air") or slot.get("count", 0) <= 0:
                empty += 1
        return empty

    def _get_best_axe(ctx: TestContext) -> Tuple[Optional[str], Optional[int]]:
        best_item = None
        best_durability = None
        for item_id in ["minecraft:stone_axe", "minecraft:wooden_axe"]:
            durability = get_best_tool_durability(ctx, item_id)
            if durability is None:
                continue
            if best_durability is None or durability > best_durability:
                best_durability = durability
                best_item = item_id
        return best_item, best_durability

    def _equip_best_axe(ctx: TestContext) -> bool:
        best_item, durability = _get_best_axe(ctx)
        if not best_item:
            return False
        if select_hotbar_item(ctx, best_item):
            logger.info(f"Equipped {best_item} (durability {durability}) in hotbar")
            return True
        logger.info(f"WARNING: Failed to equip {best_item} in hotbar")
        return False

    def _check_tool_durability(ctx: TestContext) -> Optional[int]:
        _, durability = _get_best_axe(ctx)
        return durability

    def _craft_chest_manual(ctx: TestContext) -> bool:
        return craft_chest_manual(ctx)

    def _get_any_planks(ctx: TestContext) -> int:
        return count_any_planks(ctx)

    def _count_all_logs(ctx: TestContext) -> int:
        return count_all_logs(ctx)

    def _pickup_nearby_items(
        ctx: TestContext,
        radius: int = 12,
        max_targets: int = 8,
        timeout: float = 20.0,
    ) -> int:
        start = time.time()
        before_count = None
        sweep_rounds = 0

        def _entity_present(entity_id: int) -> bool:
            entities = get_entities(ctx, radius=radius)
            return any(ent.get("id") == entity_id for ent in entities)

        def _nudge_pickup(target_x: float, target_y: float, target_z: float) -> None:
            offsets = [(0.6, 0.0), (-0.6, 0.0), (0.0, 0.6), (0.0, -0.6)]
            for dx, dz in offsets:
                do_goto(
                    ctx,
                    {"x": target_x + dx, "y": target_y, "z": target_z + dz},
                    timeout=4.0,
                    arrival_radius=0.7,
                    require_arrival=False,
                )
                time.sleep(0.2)

        while time.time() - start < timeout:
            entities = get_entities(ctx, radius=radius)
            items = [ent for ent in entities if ent.get("type") == "minecraft:item"]
            if before_count is None:
                before_count = len(items)
            if not items:
                break
            items.sort(key=lambda ent: ent.get("distance", 0))
            moved_any = False
            for ent in items[:max_targets]:
                pos = ent.get("position") or {}
                if not pos:
                    continue
                target_x = pos.get("x", 0)
                target_y = pos.get("y", 64)
                target_z = pos.get("z", 0)
                ent_id = ent.get("id")
                arrived = do_goto(
                    ctx,
                    {"x": target_x, "y": target_y, "z": target_z},
                    timeout=8.0,
                    arrival_radius=0.7,
                    require_arrival=False,
                )
                moved_any = moved_any or arrived
                time.sleep(0.3)
                if ent_id is not None and _entity_present(ent_id):
                    _nudge_pickup(target_x, target_y, target_z)
            if not moved_any:
                break
            sweep_rounds += 1
            if sweep_rounds >= 3:
                break
            time.sleep(0.2)

        if before_count is None:
            return 0
        after_items = [
            ent for ent in get_entities(ctx, radius=radius)
            if ent.get("type") == "minecraft:item"
        ]
        after_count = len(after_items)
        if before_count > 0 or after_count > 0:
            logger.info(f"Item pickup sweep: {before_count} -> {after_count} item entities within {radius} blocks")
        return max(0, before_count - after_count)

    def _get_explore_state(ctx: TestContext) -> dict:
        store = _get_persistent_store(ctx)
        state = store.get("explore", {})
        if not state:
            state = {
                "origin": None,
                "ring": 0,
                "angle_idx": 0,
                "points_per_ring": 8,
                "base_radius": 20,
                "radius_step": 16,
                "history": [],
            }
            store["explore"] = state
        if not state.get("origin"):
            house = store.get("house_7x7", {})
            origin = house.get("origin")
            if origin:
                ox, oy, oz = origin
                state["origin"] = [ox + 3, oy + 1, oz + 3]
            else:
                px, py, pz = ctx.get_position()
                state["origin"] = [int(px), int(py), int(pz)]
        return state

    def _explore_for_more_trees(ctx: TestContext) -> bool:
        state = _get_explore_state(ctx)
        origin = state.get("origin") or [0, 64, 0]
        ring = int(state.get("ring", 0))
        angle_idx = int(state.get("angle_idx", 0))
        points_per_ring = max(4, int(state.get("points_per_ring", 8)))
        base_radius = max(8, int(state.get("base_radius", 20)))
        radius_step = max(8, int(state.get("radius_step", 16)))

        angle = (2 * math.pi * angle_idx) / points_per_ring
        radius = base_radius + (ring * radius_step)
        target_x = int(round(origin[0] + (radius * math.cos(angle))))
        target_z = int(round(origin[2] + (radius * math.sin(angle))))
        px, py, _ = ctx.get_position()
        target = {"x": target_x, "y": int(py), "z": target_z}

        state["angle_idx"] = angle_idx + 1
        if state["angle_idx"] >= points_per_ring:
            state["angle_idx"] = 0
            state["ring"] = ring + 1

        history = list(state.get("history", []))
        history.append({
            "x": target_x,
            "y": int(py),
            "z": target_z,
            "ring": ring,
            "angle_idx": angle_idx,
            "ts": int(time.time()),
        })
        if len(history) > 50:
            history = history[-50:]
        state["history"] = history

        _save_persistent(ctx)

        logger.info(f"Exploring ring {ring} ({angle_idx + 1}/{points_per_ring}) at {target_x}, {int(py)}, {target_z}")
        arrived = do_goto(ctx, target, timeout=35.0, arrival_radius=2.5, require_arrival=False)
        if not arrived:
            logger.info("Explore move incomplete; will continue next run")
        return True





    def _ensure_fresh_tool(ctx: TestContext, min_durability: int = 10) -> bool:
        """Ensure we have a tool with at least min_durability remaining."""
        _, durability = _get_best_axe(ctx)
        
        if durability is None or durability < min_durability:
            logger.info( f"Tool needs replacement (durability: {durability})")
            return _craft_replacement_axe(ctx)
        
        return True

    def _blacklist_shelter_blocks(ctx: TestContext) -> None:
        """Prevent Baritone from mining shelter blocks using blocksToAvoidBreaking setting."""
        door_types = [
            "oak", "spruce", "birch", "jungle", "acacia", "dark_oak",
            "mangrove", "cherry", "bamboo", "crimson", "warped", "iron",
        ]
        bed_colors = [
            "white", "orange", "magenta", "light_blue", "yellow", "lime",
            "pink", "gray", "light_gray", "cyan", "purple", "blue",
            "brown", "green", "red", "black",
        ]
        blocks = set(PLANK_ITEM_IDS)
        blocks.update({"minecraft:crafting_table", "minecraft:chest"})
        blocks.update({f"minecraft:{name}_door" for name in door_types})
        blocks.update({f"minecraft:{color}_bed" for color in bed_colors})

        # Build comma-separated list for Baritone setting
        block_list = ",".join(sorted(blocks))
        logger.info("Setting blocksToAvoidBreaking for shelter blocks...")
        try:
            ctx.client.transport.dispatch("chat", {"message": f"#set blocksToAvoidBreaking {block_list}"})
            time.sleep(0.1)
        except Exception as exc:
            logger.info(f"WARNING: Failed to set blocksToAvoidBreaking: {exc}")

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
        """Craft a replacement axe, preferring stone when available."""
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
                 if not _ensure_planks(ctx, 3, allow_mining=True):
                      logger.info("Failed to get planks for sticks")
                      return False
            if not robust_craft(ctx, "minecraft:stick", 4):
                 logger.info("Failed to craft sticks for axe")
                 return False

        cobble_count = safe_count_item(ctx, "minecraft:cobblestone")
        stick_count = safe_count_item(ctx, "minecraft:stick")
        if cobble_count >= 3 and stick_count >= 2:
            empty_slots = _count_empty_inventory_slots(ctx)
            max_sets = min(cobble_count // 3, stick_count // 2)
            existing_stone = safe_count_item(ctx, "minecraft:stone_axe")
            max_total = min(4, existing_stone + max_sets)
            to_craft = max_total - existing_stone
            if to_craft > 0:
                craft_count = min(to_craft, max_sets)
                if empty_slots > 0:
                    craft_count = min(craft_count, empty_slots)
                elif existing_stone > 0:
                    craft_count = 0
                else:
                    craft_count = 1
                if craft_count > 0:
                    logger.info(f"Crafting {craft_count} stone axe(s)...")
                    if not ensure_crafting_table_open(ctx, table_pos=table_pos):
                        logger.info("ERROR: Could not open crafting table for stone axe")
                        return False
                    if craft_and_wait(ctx, "minecraft:stone_axe", craft_count):
                        _equip_best_axe(ctx)
                        logger.info("Replacement stone axe crafted successfully")
                        return True
                    if not ensure_crafting_table_open(ctx, table_pos=table_pos):
                        logger.info("ERROR: Could not reopen crafting table for stone axe")
                        return False
                    if robust_craft(ctx, "minecraft:stone_axe", existing_stone + craft_count, is_tool=True):
                        _equip_best_axe(ctx)
                        logger.info("Replacement stone axe crafted successfully")
                        return True
                    logger.info("Stone axe crafting failed; falling back to wooden axe")

        if not _ensure_matching_planks_for_axe(ctx):
            logger.info("Failed to get matching planks for axe")
            return False

        if craft_wooden_axe_manual(ctx):
            _equip_best_axe(ctx)
            logger.info("Replacement axe crafted successfully")
            return True

        logger.info("ERROR: Failed to craft replacement axe")
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

    def _world_cache_path() -> str:
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        data_dir = os.path.join(root, "data")
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, "world_cache.sqlite")

    def _world_cache_conn() -> sqlite3.Connection:
        conn = sqlite3.connect(_world_cache_path())
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        return conn

    def _init_world_cache(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS blocks (
                dimension TEXT NOT NULL,
                x INTEGER NOT NULL,
                y INTEGER NOT NULL,
                z INTEGER NOT NULL,
                chunk_x INTEGER NOT NULL,
                chunk_z INTEGER NOT NULL,
                block_id TEXT NOT NULL,
                props TEXT,
                last_seen INTEGER NOT NULL,
                PRIMARY KEY (dimension, x, y, z)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chunk_scans (
                dimension TEXT NOT NULL,
                chunk_x INTEGER NOT NULL,
                chunk_z INTEGER NOT NULL,
                last_scan INTEGER NOT NULL,
                PRIMARY KEY (dimension, chunk_x, chunk_z)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_blocks_chunk ON blocks (dimension, chunk_x, chunk_z)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_blocks_id ON blocks (dimension, block_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_blocks_xz ON blocks (dimension, x, z)"
        )
        conn.commit()

    def _block_props_json(block: dict) -> str:
        data = block.get("data", block)
        props = data.get("properties") or block.get("properties")
        if not isinstance(props, dict):
            return ""
        try:
            return json.dumps(props, sort_keys=True)
        except Exception:
            return ""

    def _cache_block_rows(
        rows: List[Tuple[str, int, int, int, int, int, str, str, int]]
    ) -> None:
        if not rows:
            return
        conn = _world_cache_conn()
        try:
            _init_world_cache(conn)
            conn.executemany(
                """
                INSERT INTO blocks (
                    dimension, x, y, z, chunk_x, chunk_z, block_id, props, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dimension, x, y, z) DO UPDATE SET
                    chunk_x=excluded.chunk_x,
                    chunk_z=excluded.chunk_z,
                    block_id=excluded.block_id,
                    props=excluded.props,
                    last_seen=excluded.last_seen
                """,
                rows,
            )
            conn.commit()
        finally:
            conn.close()

    def _cache_block_deletes(rows: List[Tuple[str, int, int, int]]) -> None:
        if not rows:
            return
        conn = _world_cache_conn()
        try:
            _init_world_cache(conn)
            conn.executemany(
                "DELETE FROM blocks WHERE dimension=? AND x=? AND y=? AND z=?",
                rows,
            )
            conn.commit()
        finally:
            conn.close()

    def _record_chunk_scan(dimension: str, chunk_x: int, chunk_z: int) -> None:
        conn = _world_cache_conn()
        try:
            _init_world_cache(conn)
            conn.execute(
                """
                INSERT INTO chunk_scans (dimension, chunk_x, chunk_z, last_scan)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(dimension, chunk_x, chunk_z) DO UPDATE SET last_scan=excluded.last_scan
                """,
                (dimension, chunk_x, chunk_z, int(time.time())),
            )
            conn.commit()
        finally:
            conn.close()

    def _get_chest_index(store: dict) -> dict:
        index = store.get("chest_index")
        if not isinstance(index, dict):
            index = {}
        return index

    def _get_block_properties(ctx: TestContext, pos: List[int]) -> dict:
        block = ctx.get_block(int(pos[0]), int(pos[1]), int(pos[2]))
        data = block.get("data", block)
        props = data.get("properties")
        if isinstance(props, dict):
            return props
        props = block.get("properties")
        if isinstance(props, dict):
            return props
        return {}

    def _get_chest_facing(ctx: TestContext, pos: List[int]) -> Optional[str]:
        props = _get_block_properties(ctx, pos)
        facing = props.get("facing")
        if facing:
            return str(facing)
        return None

    def _find_adjacent_chest(ctx: TestContext, pos: List[int]) -> Optional[List[int]]:
        x, y, z = pos
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            tx, ty, tz = x + dx, y, z + dz
            bid = block_id_at(ctx, tx, ty, tz)
            if bid and "chest" in bid:
                return [tx, ty, tz]
        return None

    def _summarize_chest_contents(slots: List[dict], chest_slots: int) -> Tuple[dict, int]:
        contents: Dict[str, int] = {}
        occupied = 0
        for item in slots:
            slot_idx = item.get("slot", -1)
            if slot_idx < 0 or slot_idx >= chest_slots:
                continue
            item_id = item.get("id", "")
            if not item_id or item_id == "minecraft:air":
                continue
            count = int(item.get("count", 0))
            contents[item_id] = contents.get(item_id, 0) + count
            occupied += 1
        empty_slots = max(0, chest_slots - occupied)
        return contents, empty_slots

    def _chest_roles_for_pos(store: dict, pos: List[int]) -> List[str]:
        roles = []
        key = _pos_key(pos)
        for chest_pos in store.get("wood_chests", []):
            if _pos_key(chest_pos) == key:
                roles.append("wood")
                break
        for chest_pos in store.get("extra_log_chests", []):
            if _pos_key(chest_pos) == key:
                roles.append("extra_log")
                break
        for entry in store.get("planks_chests", []):
            chest_a = _normalize_pos(entry.get("chest_a"))
            chest_b = _normalize_pos(entry.get("chest_b"))
            if chest_a and _pos_key(chest_a) == key:
                roles.append("planks")
                break
            if chest_b and _pos_key(chest_b) == key:
                roles.append("planks")
                break
        house = store.get("house_7x7") or {}
        supply = _normalize_pos(house.get("supply_chest"))
        if supply and _pos_key(supply) == key:
            roles.append("supply")
        return roles

    def _update_chest_index_from_open(
        ctx: TestContext,
        chest_pos: List[int],
        slots: List[dict],
        chest_slots: int,
    ) -> None:
        store = _get_persistent_store(ctx)
        index = _get_chest_index(store)
        pos = _normalize_pos(chest_pos)
        if not pos:
            return
        pair = _find_adjacent_chest(ctx, pos)
        contents, empty_slots = _summarize_chest_contents(slots, chest_slots)
        chest_type = "double" if chest_slots >= 54 or pair else "single"
        entry = {
            "pos": pos,
            "facing": _get_chest_facing(ctx, pos),
            "slots": chest_slots,
            "empty_slots": empty_slots,
            "full": empty_slots == 0,
            "type": chest_type,
            "pair": pair,
            "contents": contents,
            "roles": _chest_roles_for_pos(store, pos),
            "last_scan": int(time.time()),
        }
        index[_pos_key(pos)] = entry
        if pair:
            pair_pos = _normalize_pos(pair)
            if pair_pos:
                pair_entry = dict(entry)
                pair_entry["pos"] = pair_pos
                pair_entry["facing"] = _get_chest_facing(ctx, pair_pos)
                pair_entry["pair"] = pos
                pair_entry["roles"] = _chest_roles_for_pos(store, pair_pos)
                index[_pos_key(pair_pos)] = pair_entry
        store["chest_index"] = index
        _save_persistent(ctx)

    def _is_chest_known_full(store: dict, pos: List[int]) -> bool:
        entry = _get_chest_index(store).get(_pos_key(pos))
        if not entry:
            return False
        return bool(entry.get("full"))

    def _count_item_in_chest_index(store: dict, pos: List[int], item_ids: List[str]) -> int:
        entry = _get_chest_index(store).get(_pos_key(pos))
        if not entry:
            return 0
        contents = entry.get("contents") or {}
        return sum(int(contents.get(item_id, 0)) for item_id in item_ids)

    def _merge_chests_with_index(store: dict, chests: List[List[int]], item_ids: List[str]) -> List[List[int]]:
        index = _get_chest_index(store)
        indexed = []
        for entry in index.values():
            contents = entry.get("contents") or {}
            count = sum(int(contents.get(item_id, 0)) for item_id in item_ids)
            if count > 0:
                indexed.append(entry.get("pos"))
        combined = _dedupe_positions(chests + indexed)
        combined.sort(
            key=lambda pos: _count_item_in_chest_index(store, pos, item_ids),
            reverse=True,
        )
        return combined

    def _gather_known_chests(store: dict) -> List[List[int]]:
        chests: List[List[int]] = []
        chests.extend(store.get("wood_chests", []))
        chests.extend(store.get("extra_log_chests", []))
        for entry in store.get("planks_chests", []):
            if not isinstance(entry, dict):
                continue
            chest_a = _normalize_pos(entry.get("chest_a"))
            chest_b = _normalize_pos(entry.get("chest_b"))
            if chest_a:
                chests.append(chest_a)
            if chest_b:
                chests.append(chest_b)
        house = store.get("house_7x7") or {}
        supply = _normalize_pos(house.get("supply_chest"))
        if supply:
            chests.append(supply)
        chests.extend(store.get("known_chests", []))
        index = _get_chest_index(store)
        for entry in index.values():
            pos = _normalize_pos(entry.get("pos"))
            if pos:
                chests.append(pos)
        return _dedupe_positions(chests)

    def _unique_chest_positions_from_index(store: dict) -> List[List[int]]:
        index = _get_chest_index(store)
        seen = set()
        result = []
        for entry in index.values():
            pos = _normalize_pos(entry.get("pos"))
            if not pos:
                continue
            pair = _normalize_pos(entry.get("pair"))
            key = _pos_key(pos)
            if pair:
                pair_key = _pos_key(pair)
                canonical = min(key, pair_key)
                if canonical in seen:
                    continue
                seen.add(canonical)
            else:
                if key in seen:
                    continue
                seen.add(key)
            result.append(pos)
        return result

    def _count_items_in_index(store: dict, item_ids: List[str]) -> int:
        total = 0
        for pos in _unique_chest_positions_from_index(store):
            total += _count_item_in_chest_index(store, pos, item_ids)
        return total

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

    def _is_solid_support_block(block_id: Optional[str]) -> bool:
        if not block_id:
            return False
        if "air" in block_id or "water" in block_id or "lava" in block_id:
            return False
        non_solid = [
            "grass",
            "flower",
            "fern",
            "sapling",
            "dead_bush",
            "torch",
            "fire",
            "leaf_litter",
            "snow",
            "leaves",
        ]
        if any(ns in block_id for ns in non_solid) and "grass_block" not in block_id:
            return False
        return True

    def _is_placeable_target(block_id: Optional[str]) -> bool:
        if not block_id:
            return False
        if "chest" in block_id:
            return False
        return "air" in block_id or "water" in block_id or "lava" in block_id

    WORLD_SCAN_Y_MIN = -64
    WORLD_SCAN_Y_MAX = 319
    WORLD_SCAN_TIME_BUDGET_S = 45.0
    EXCLUDED_BLOCK_IDS = {
        "minecraft:air",
        "minecraft:dirt",
        "minecraft:cobblestone",
        "minecraft:stone",
    }

    TOWER_OUTER_SIZE = 7
    TOWER_INNER_SIZE = 5
    TOWER_LEVEL_HEIGHT = 4
    TOWER_PLANKS_PER_LEVEL = 96
    TOWER_STAIRS_PER_LEVEL = 4
    TOWER_SLAB_BUFFER = 4
    TOWER_MAX_Y = 319

    TOOL_WORN_THRESHOLD = 10

    def _tool_max_damage(item_id: str) -> Optional[int]:
        if "wooden_" in item_id:
            return 59
        if "stone_" in item_id:
            return 131
        if "iron_" in item_id:
            return 250
        if "golden_" in item_id:
            return 32
        if "diamond_" in item_id:
            return 1561
        if "netherite_" in item_id:
            return 2031
        return None

    def _is_worn_tool(item_id: str, damage: Optional[int]) -> bool:
        if damage is None:
            return False
        max_damage = _tool_max_damage(item_id)
        if max_damage is None:
            return False
        remaining = max_damage - damage
        return remaining <= TOOL_WORN_THRESHOLD

    def _screen_slot_to_inventory_slot(slot_idx: int, chest_slots: int = 27) -> Optional[int]:
        # Chest screen layout: 0-(chest_slots-1) container, then 27 main inventory, then 9 hotbar
        main_start = chest_slots
        main_end = chest_slots + 26
        hotbar_start = chest_slots + 27
        hotbar_end = chest_slots + 35
        if main_start <= slot_idx <= main_end:
            return slot_idx - (chest_slots - 9)
        if hotbar_start <= slot_idx <= hotbar_end:
            return slot_idx - hotbar_start
        return None

    def _place_planks_at_positions(ctx: TestContext, positions: List[Tuple[int, int, int]]) -> bool:
        current_plank = _pick_any_plank(ctx)
        if not current_plank:
            return False
        ok = True
        for x, y, z in positions:
            if safe_count_item(ctx, current_plank) <= 0:
                current_plank = _pick_any_plank(ctx)
                if not current_plank:
                    return False
            ok = place_block_at(ctx, x, y, z, current_plank) and ok
        return ok

    def _ensure_chest_item(ctx: TestContext) -> bool:
        if safe_count_item(ctx, "minecraft:chest") > 0:
            return True
        table_pos = _ensure_crafting_table_placed(ctx)
        if not table_pos:
            logger.info("ERROR: Could not place crafting table for chest crafting")
            return False
        if not ensure_crafting_table_open(ctx, table_pos=table_pos):
            logger.info("ERROR: Could not open crafting table for chest crafting")
            return False
        if count_any_planks(ctx) < 8:
            if not _ensure_planks(ctx, 8):
                logger.info("ERROR: Not enough planks for chest crafting")
                do_close_container(ctx)
                return False
        if not craft_chest_manual(ctx):
            logger.info("ERROR: Failed to craft chest manually")
            do_close_container(ctx)
            return False
        do_close_container(ctx)
        return True

    def _ensure_house_matches_plan(ctx: TestContext) -> bool:
        store = _get_persistent_store(ctx)
        house = store.get("house_7x7")
        if not house or "origin" not in house:
            if not _build_house_7x7(ctx):
                return False
            house = store.get("house_7x7")
            if not house or "origin" not in house:
                return False

        ox, oy, oz = house["origin"]
        door_x, door_y, door_z = ox + 3, oy + 1, oz + 5
        craft_x, craft_y, craft_z = ox + 4, oy + 1, oz + 2
        chest_x, chest_y, chest_z = ox + 4, oy + 1, oz + 4

        wall_positions = []
        for x_offset in range(1, 6):
            wall_positions.append((ox + x_offset, oy + 1, oz + 1))
            wall_positions.append((ox + x_offset, oy + 2, oz + 1))
            if x_offset != 3:
                wall_positions.append((ox + x_offset, oy + 1, oz + 5))
                wall_positions.append((ox + x_offset, oy + 2, oz + 5))
        for z_offset in range(2, 5):
            wall_positions.append((ox + 1, oy + 1, oz + z_offset))
            wall_positions.append((ox + 1, oy + 2, oz + z_offset))
            wall_positions.append((ox + 5, oy + 1, oz + z_offset))
            wall_positions.append((ox + 5, oy + 2, oz + z_offset))

        roof_positions = []
        roof_y = oy + 3
        for x_offset in range(1, 6):
            for z_offset in range(1, 6):
                roof_positions.append((ox + x_offset, roof_y, oz + z_offset))

        def _find_missing_planks(positions: List[Tuple[int, int, int]]) -> List[Tuple[int, int, int]]:
            missing = []
            for x, y, z in positions:
                block_id = block_id_at(ctx, x, y, z)
                if not block_id or not block_id.endswith("_planks"):
                    missing.append((x, y, z))
            return missing

        missing_walls = _find_missing_planks(wall_positions)
        if missing_walls:
            support_positions = []
            for x, y, z in missing_walls:
                if y != oy + 1:
                    continue
                below_id = block_id_at(ctx, x, y - 1, z)
                if not below_id or "air" in below_id:
                    support_positions.append((x, y - 1, z))
            support_positions = _dedupe_positions([list(pos) for pos in support_positions])
            if support_positions:
                if not _ensure_planks(ctx, len(support_positions)):
                    logger.info("ERROR: Not enough planks to support shelter repair")
                    return False
                if not _place_planks_at_positions(ctx, [tuple(pos) for pos in support_positions]):
                    logger.info("ERROR: Failed to place support planks for shelter repair")
                    return False

            if not _ensure_planks(ctx, len(missing_walls)):
                logger.info("ERROR: Not enough planks to repair shelter walls")
                return False
            missing_walls.sort(key=lambda pos: pos[1])
            if not _place_planks_at_positions(ctx, missing_walls):
                logger.info("ERROR: Failed to place planks for shelter walls")
                return False

        missing_roof = _find_missing_planks(roof_positions)
        if missing_roof:
            if not _ensure_planks(ctx, len(missing_roof)):
                logger.info("ERROR: Not enough planks to repair shelter roof")
                return False
            if not _place_planks_at_positions(ctx, missing_roof):
                logger.info("ERROR: Failed to place planks for shelter roof")
                return False

        door_block = block_id_at(ctx, door_x, door_y, door_z)
        if not door_block or not door_block.endswith("_door"):
            door_item = house.get("door_item") or _ensure_door(ctx)
            if not door_item:
                logger.info("ERROR: Could not craft door for shelter repair")
                return False
            if not place_block_at(ctx, door_x, door_y, door_z, door_item):
                logger.info("ERROR: Failed to place door for shelter repair")
                return False
            house["door_item"] = door_item

        craft_block = block_id_at(ctx, craft_x, craft_y, craft_z)
        if not craft_block or "crafting_table" not in craft_block:
            if not _ensure_crafting_table_item(ctx):
                logger.info("ERROR: Failed to craft crafting table for shelter repair")
                return False
            if not place_block_at(ctx, craft_x, craft_y, craft_z, "minecraft:crafting_table"):
                logger.info("ERROR: Failed to place crafting table for shelter repair")
                return False

        chest_block = block_id_at(ctx, chest_x, chest_y, chest_z)
        if not chest_block or "chest" not in chest_block:
            if not _ensure_chest_item(ctx):
                logger.info("ERROR: Failed to craft chest for shelter repair")
                return False
            if not place_block_at(ctx, chest_x, chest_y, chest_z, "minecraft:chest"):
                logger.info("ERROR: Failed to place chest for shelter repair")
                return False

        store["house_7x7"] = house
        _save_persistent(ctx)
        return True

    def _get_house_border_positions(store: dict) -> List[List[int]]:
        house = store.get("house_7x7")
        if not house or "origin" not in house:
            return []
        ox, oy, oz = house["origin"]
        y = oy + 1
        positions = []
        for x in range(ox, ox + 7):
            positions.append([x, y, oz])
            positions.append([x, y, oz + 6])
        for z in range(oz + 1, oz + 6):
            positions.append([ox, y, z])
            positions.append([ox + 6, y, z])
        door_path = [ox + 3, y, oz + 6]
        positions = [pos for pos in positions if pos != door_path]
        return _dedupe_positions(positions)

    def _find_sapling_spots(
        ctx: TestContext,
        center: Tuple[int, int, int],
        radius: int,
        max_positions: int,
        min_clearance: int,
        min_light_clearance: Optional[int] = None,
        grid_spacing: Optional[int] = None,
        avoid_box: Optional[Tuple[int, int, int, int]] = None,
    ) -> List[Tuple[int, int, int]]:
        ground_blocks = [
            "grass_block",
            "dirt",
            "coarse_dirt",
            "podzol",
            "rooted_dirt",
        ]
        cx, cy, cz = center
        light_clearance = max(min_clearance, min_light_clearance if min_light_clearance is not None else 10)
        spots = []
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                x = cx + dx
                z = cz + dz
                if grid_spacing:
                    if ((x - cx) % grid_spacing) != 0 or ((z - cz) % grid_spacing) != 0:
                        continue
                if avoid_box:
                    min_x, max_x, min_z, max_z = avoid_box
                    if min_x <= x <= max_x and min_z <= z <= max_z:
                        continue
                found_y = None
                for scan_y in range(int(cy) + 2, int(cy) - 12, -1):
                    block_at = block_id_at(ctx, x, scan_y, z)
                    if block_at and any(gb in block_at for gb in ground_blocks):
                        found_y = scan_y + 1
                        break
                if found_y is None:
                    continue
                if not _has_vertical_clearance(ctx, x, found_y, z, light_clearance):
                    continue
                spots.append((x, found_y, z))
                if len(spots) >= max_positions:
                    return spots
        return spots

    def _has_vertical_clearance(ctx: TestContext, x: int, y: int, z: int, height: int) -> bool:
        for dy in range(height):
            block_at = block_id_at(ctx, x, y + dy, z)
            if not block_at or "air" not in block_at:
                return False
        return True

    def _find_ground_y(ctx: TestContext, x: int, z: int, y_ref: int) -> Optional[int]:
        ground_blocks = [
            "grass_block",
            "dirt",
            "coarse_dirt",
            "podzol",
            "rooted_dirt",
        ]
        for scan_y in range(int(y_ref) + 2, int(y_ref) - 12, -1):
            block_at = block_id_at(ctx, x, scan_y, z)
            if block_at and any(gb in block_at for gb in ground_blocks):
                return scan_y + 1
        return None

    def _find_sapling_clusters(
        ctx: TestContext,
        center: Tuple[int, int, int],
        radius: int,
        max_clusters: int,
        min_clearance: int,
        min_light_clearance: Optional[int] = None,
        avoid_box: Optional[Tuple[int, int, int, int]] = None,
    ) -> List[List[Tuple[int, int, int]]]:
        cx, cy, cz = center
        light_clearance = max(min_clearance, min_light_clearance if min_light_clearance is not None else 10)
        clusters = []
        occupied = set()
        for dx in range(-radius, radius):
            for dz in range(-radius, radius):
                base_x = cx + dx
                base_z = cz + dz
                positions = [
                    (base_x, base_z),
                    (base_x + 1, base_z),
                    (base_x, base_z + 1),
                    (base_x + 1, base_z + 1),
                ]
                if avoid_box:
                    min_x, max_x, min_z, max_z = avoid_box
                    if any(min_x <= x <= max_x and min_z <= z <= max_z for x, z in positions):
                        continue
                if any((x, z) in occupied for x, z in positions):
                    continue
                ys = []
                for x, z in positions:
                    y = _find_ground_y(ctx, x, z, cy)
                    if y is None:
                        ys = []
                        break
                    ys.append(y)
                if not ys or len(set(ys)) != 1:
                    continue
                y = ys[0]
                if not all(_has_vertical_clearance(ctx, x, y, z, light_clearance) for x, z in positions):
                    continue
                cluster = [(x, y, z) for x, z in positions]
                clusters.append(cluster)
                for x, z in positions:
                    occupied.add((x, z))
                if len(clusters) >= max_clusters:
                    return clusters
        return clusters

    def _replant_saplings(ctx: TestContext, max_to_plant: int = 8) -> int:
        inv = get_inventory_counts(ctx)
        saplings = {item_id: count for item_id, count in inv.items() if item_id in SAPLING_ITEM_IDS and count > 0}
        total_saplings = sum(saplings.values())
        if total_saplings <= 0:
            return 0

        store = _get_persistent_store(ctx)
        house = store.get("house_7x7", {})
        origin = house.get("origin")
        if origin:
            ox, oy, oz = origin
            center = (ox + 3, oy + 1, oz + 3)
            avoid_box = (ox - 20, ox + 26, oz - 20, oz + 26)
        else:
            px, py, pz = ctx.get_position()
            center = (int(px), int(py), int(pz))
            avoid_box = None

        planted = 0
        big_saplings = {
            "minecraft:spruce_sapling",
            "minecraft:jungle_sapling",
            "minecraft:dark_oak_sapling",
        }
        max_groups = max_to_plant // 4
        for sapling_type in list(big_saplings):
            available = saplings.get(sapling_type, 0)
            if available < 4 or max_groups <= 0:
                continue
            clusters = _find_sapling_clusters(
                ctx,
                center,
                radius=45,
                max_clusters=min(available // 4, max_groups),
                min_clearance=9,
                min_light_clearance=12,
                avoid_box=avoid_box,
            )
            for cluster in clusters:
                success = True
                for x, y, z in cluster:
                    if not place_block_at(ctx, x, y, z, sapling_type, allow_break=False):
                        success = False
                        break
                if not success:
                    continue
                planted += 4
                saplings[sapling_type] -= 4
                max_groups -= 1
                if max_groups <= 0:
                    break

        # Spacing requirements per sapling type
        sapling_spacing = {
            "minecraft:oak_sapling": 2,
            "minecraft:birch_sapling": 2,
            "minecraft:acacia_sapling": 3,
            "minecraft:spruce_sapling": 5,  # Can be giant
            "minecraft:dark_oak_sapling": 5,  # Always 2x2
            "minecraft:jungle_sapling": 7,  # Can be giant
            "minecraft:cherry_sapling": 2,
            "minecraft:mangrove_propagule": 3,
        }
        
        # Plant each sapling type with its required spacing
        for sapling_item, count in list(saplings.items()):
            if count <= 0 or planted >= max_to_plant:
                continue
            spacing = sapling_spacing.get(sapling_item, 2)
            to_plant_this_type = min(count, max_to_plant - planted)
            spots = _find_sapling_spots(
                ctx,
                center,
                radius=40,
                max_positions=to_plant_this_type,
                min_clearance=6,
                min_light_clearance=10,
                grid_spacing=spacing,
                avoid_box=avoid_box,
            )
            for pos in spots:
                if planted >= max_to_plant:
                    break
                x, y, z = pos
                if place_block_at(ctx, x, y, z, sapling_item, allow_break=False):
                    planted += 1
                    saplings[sapling_item] -= 1
        
        if planted > 0:
            logger.info(f"Replanted {planted} saplings")
        return planted

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

    def _mine_min_logs(ctx: TestContext, min_logs: int, timeout: float = 35.0) -> bool:
        if min_logs <= 0:
            return True
        if _is_near_night_early(get_world_time(ctx)):
            logger.info("Near night; skipping minimal log mining")
            return False
        start_logs = count_all_logs(ctx)
        if start_logs >= min_logs:
            return True
        logger.info(f"Mining {min_logs} log(s) to bootstrap crafting...")
        ctx.client.transport.dispatch("chat", {"message": f"#mine {LOG_MINE_ARG}"})
        start = time.time()
        while time.time() - start < timeout:
            if count_all_logs(ctx) >= start_logs + min_logs:
                break
            time.sleep(2.0)
        ctx.client.transport.dispatch("chat", {"message": "#stop"})
        time.sleep(1.0)
        cancel_pathing(ctx)
        time.sleep(0.5)
        _pickup_nearby_items(ctx)
        gained = count_all_logs(ctx) - start_logs
        if gained >= min_logs:
            logger.info(f"Collected {gained} log(s) for crafting")
            return True
        logger.info(f"Failed to collect {min_logs} log(s) for crafting")
        return False

    def _craft_furnace_manual(ctx: TestContext) -> bool:
        logger.info("Starting manual furnace crafting sequence...")
        safe_inventory_click(ctx, 0, "QUICK_MOVE", 0)
        time.sleep(0.2)

        screen = ctx.client.transport.dispatch("get_screen", {})
        data = screen.get("data", screen)
        screen_slots = data.get("slots", [])
        for grid_slot in range(1, 10):
            slot_info = next((s for s in screen_slots if s.get("slot") == grid_slot), None)
            if slot_info and slot_info.get("id") not in ("minecraft:air", None) and slot_info.get("count", 0) > 0:
                safe_inventory_click(ctx, grid_slot, "QUICK_MOVE", 0)
                time.sleep(0.05)
        cobble_slots = []
        total_cobble = 0
        for s in screen_slots:
            slot_id = s.get("slot", -1)
            item_id = s.get("id", "")
            count = s.get("count", 0)
            if slot_id >= 10 and item_id == "minecraft:cobblestone" and count > 0:
                cobble_slots.append((slot_id, count))
                total_cobble += count
                if total_cobble >= 8:
                    break

        if total_cobble < 8:
            logger.info(f"ERROR: Not enough cobblestone (have {total_cobble}, need 8)")
            return False

        furnace_pattern = [1, 2, 3, 4, 6, 7, 8, 9]
        slot_idx = 0
        remaining = 0
        current_slot = None
        for grid_slot in furnace_pattern:
            if remaining == 0:
                if slot_idx >= len(cobble_slots):
                    logger.info("ERROR: Ran out of cobblestone during furnace craft")
                    return False
                current_slot, remaining = cobble_slots[slot_idx]
                slot_idx += 1
                safe_inventory_click(ctx, current_slot, "PICKUP", 0)
                time.sleep(0.1)

            safe_inventory_click(ctx, grid_slot, "PICKUP", 1)
            time.sleep(0.1)
            remaining -= 1

            if remaining == 0 and current_slot is not None:
                safe_inventory_click(ctx, current_slot, "PICKUP", 0)
                time.sleep(0.1)
                current_slot = None

        if remaining > 0 and current_slot is not None:
            safe_inventory_click(ctx, current_slot, "PICKUP", 0)
            time.sleep(0.1)

        screen = ctx.client.transport.dispatch("get_screen", {})
        data = screen.get("data", screen)
        result_slot = next((s for s in data.get("slots", []) if s.get("slot") == 0), None)
        logger.info(f"Furnace result slot (0) before taking: {result_slot}")
        safe_inventory_click(ctx, 0, "QUICK_MOVE", 0)
        time.sleep(0.5)

        if safe_count_item(ctx, "minecraft:furnace") > 0:
            logger.info("Manual furnace crafting successful!")
            return True

        logger.info("ERROR: Furnace not in inventory after crafting sequence")
        return False

    def _ensure_furnace_item(ctx: TestContext) -> bool:
        if safe_count_item(ctx, "minecraft:furnace") > 0:
            return True

        store = _get_persistent_store(ctx)
        have_cobble = safe_count_item(ctx, "minecraft:cobblestone")
        if have_cobble < 8:
            needed = 8 - have_cobble
            chests = _gather_known_chests(store)
            if _tower_withdraw_items(ctx, chests, "minecraft:cobblestone", needed) <= 0:
                logger.info("ERROR: Could not withdraw cobblestone for furnace")
                return False
            if safe_count_item(ctx, "minecraft:cobblestone") < 8:
                logger.info("ERROR: Not enough cobblestone after withdraw for furnace")
                return False

        table_pos = _ensure_crafting_table_placed(ctx)
        if not table_pos:
            logger.info("ERROR: Could not place crafting table for furnace")
            return False
        if not ensure_crafting_table_open(ctx, table_pos=table_pos):
            logger.info("ERROR: Could not open crafting table for furnace")
            return False
        if not _craft_furnace_manual(ctx):
            do_close_container(ctx)
            return False
        do_close_container(ctx)
        return safe_count_item(ctx, "minecraft:furnace") > 0

    def _ensure_furnace_placed(ctx: TestContext) -> Optional[List[int]]:
        store = _get_persistent_store(ctx)
        candidates = []
        for key in ("furnace_active", "furnace"):
            pos = _normalize_pos(store.get(key))
            if pos:
                candidates.append(pos)
        candidates.extend(_dedupe_positions(store.get("furnaces", [])))
        for pos in candidates:
            bid = block_id_at(ctx, pos[0], pos[1], pos[2])
            if bid and "furnace" in bid:
                store["furnace"] = pos
                store["furnace_active"] = pos
                store["furnaces"] = _dedupe_positions(store.get("furnaces", []) + [pos])
                _save_persistent(ctx)
                return pos

        base = store.get("base_origin")
        if base:
            px, py, pz = base
        else:
            px, py, pz = ctx.get_position()
        pos = find_place_pos_near(ctx, int(px), int(py), int(pz), radius=4)
        if not pos:
            logger.info("ERROR: Could not find position to place furnace")
            return None
        if not place_block_at(ctx, pos[0], pos[1], pos[2], "minecraft:furnace"):
            logger.info("ERROR: Failed to place furnace")
            return None
        store["furnace"] = [pos[0], pos[1], pos[2]]
        store["furnace_active"] = [pos[0], pos[1], pos[2]]
        store["furnaces"] = _dedupe_positions(store.get("furnaces", []) + [[pos[0], pos[1], pos[2]]])
        _save_persistent(ctx)
        return [pos[0], pos[1], pos[2]]

    def _ensure_matching_planks_for_axe(ctx: TestContext) -> bool:
        plank_counts = _get_plank_counts(ctx)
        for item_id, count in plank_counts.items():
            if count >= 3:
                return True

        inv = get_inventory_counts(ctx)
        log_candidates = [
            (item_id, count) for item_id, count in inv.items()
            if item_id in LOG_BLOCK_IDS and count > 0
        ]
        if not log_candidates:
            if _mine_min_logs(ctx, 1):
                inv = get_inventory_counts(ctx)
                log_candidates = [
                    (item_id, count) for item_id, count in inv.items()
                    if item_id in LOG_BLOCK_IDS and count > 0
                ]
        if not log_candidates:
            return False

        log_candidates.sort(key=lambda entry: entry[1], reverse=True)
        log_item = log_candidates[0][0]
        target_plank = log_item.replace("_log", "_planks")

        while safe_count_item(ctx, target_plank) < 3:
            desired_planks = safe_count_item(ctx, target_plank) + 4
            if not robust_craft(ctx, target_plank, desired_planks):
                return False
            time.sleep(0.3)

        return True

    def _ensure_planks(ctx: TestContext, min_planks: int, allow_mining: bool = False) -> bool:
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
            if not log_item and total_planks < min_planks and allow_mining:
                needed_logs = max(1, (min_planks - total_planks + 3) // 4)
                if _mine_min_logs(ctx, needed_logs):
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
        ordered = _merge_chests_with_index(store, chests, LOG_BLOCK_IDS + PLANK_ITEM_IDS)
        chest_pos = ordered[-1] if ordered else chests[-1]
        move_near(ctx, chest_pos[0], chest_pos[1], chest_pos[2], timeout=20.0)
        if not do_open_container(ctx, (chest_pos[0], chest_pos[1], chest_pos[2])):
            return False

        moved = False
        try:
            time.sleep(0.8)
            slots_info = _open_container_slots(ctx)
            if not slots_info:
                return False
            slots, chest_slots = slots_info
            needed_planks = max(0, min_planks - count_any_planks(ctx))
            if needed_planks <= 0:
                _update_chest_index_from_open(ctx, chest_pos, slots, chest_slots)
                return True

            plank_slots = []
            log_slots = []
            for item in slots:
                slot_idx = item.get("slot", -1)
                if slot_idx < 0 or slot_idx >= chest_slots:
                    continue
                item_id = item.get("id", "")
                if not item_id or item_id == "minecraft:air":
                    continue
                count = item.get("count", 0)
                if item_id.endswith("_planks"):
                    plank_slots.append((count, slot_idx))
                elif (
                    item_id in LOG_BLOCK_IDS
                    or item_id in STRIPPED_LOG_BLOCK_IDS
                    or item_id in WOOD_BLOCK_IDS
                    or item_id in STRIPPED_WOOD_BLOCK_IDS
                ):
                    log_slots.append((count, slot_idx))

            plank_slots.sort(key=lambda entry: entry[0])
            for count, slot_idx in plank_slots:
                safe_inventory_click(ctx, slot_idx, "QUICK_MOVE")
                moved = True
                time.sleep(0.1)
                needed_planks -= count
                if needed_planks <= 0:
                    break

            if needed_planks > 0:
                log_slots.sort(key=lambda entry: entry[0])
                for count, slot_idx in log_slots:
                    safe_inventory_click(ctx, slot_idx, "QUICK_MOVE")
                    moved = True
                    time.sleep(0.1)
                    needed_planks -= (count * 4)
                    if needed_planks <= 0:
                        break
            screen_after = ctx.client.transport.dispatch("get_screen", {})
            screen_data_after = screen_after.get("data", screen_after)
            slots_after = screen_data_after.get("slots", [])
            _update_chest_index_from_open(ctx, chest_pos, slots_after, chest_slots)
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

    EARLY_NIGHT_TICKS = 11000

    def _is_near_night_early(world_time: int) -> bool:
        return (world_time % 24000) >= EARLY_NIGHT_TICKS

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

    def _verify_and_repair_shelter(ctx: TestContext) -> bool:
        """Check all shelter blocks and repair any missing ones."""
        return _ensure_house_matches_plan(ctx)

    def _move_to_shelter(ctx: TestContext, shelter: dict) -> bool:
        origin = shelter.get("origin")
        if not origin:
            return False
        # Target center of house: origin is corner, so +3 on X and +2 on Z to reach center
        target = (origin[0] + 3, origin[1], origin[2] + 2)
        logger.info( f"Moving to night shelter at {list(target)}...")
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
            house = store.get("house_7x7", {})
            origin = house.get("origin")
            door = house.get("door")
            if origin and door:
                store["night_shelter"] = {"origin": origin, "door": door}
                _save_persistent(ctx)
                logger.info("House already exists; rehydrated night_shelter from house_7x7")
            else:
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
        if not _is_near_night_early(world_time):
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

        # Check and repair shelter blocks each night
        if not _verify_and_repair_shelter(ctx):
            return False

        start = time.time()
        next_log = time.time()
        saw_night = (world_time % 24000) >= 12000
        while time.time() - start < 1200.0:
            current_time = get_world_time(ctx)
            day_time = int(current_time) % 24000
            if day_time >= 12000:
                saw_night = True
            if saw_night and day_time < 12000:
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

        _blacklist_shelter_blocks(ctx)
        
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
        
        tool_id, durability = _get_best_axe(ctx)
        logger.info(f"Current tool durability: {durability} ({tool_id})")
        
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
        early_stop = False
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

            if _is_near_night_early(get_world_time(ctx)):
                logger.info("Approaching night; stopping mining to head home.")
                early_stop = True
                break
            
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
            _, durability = _get_best_axe(ctx)
            
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

        _pickup_nearby_items(ctx)
        
        final_logs = max(max_logs, count_all_logs(ctx))  # Include any pickups
        final_gain = final_logs - initial_logs
        logger.info( f"Mining completed: gained {final_gain} logs (now have {final_logs} total)")

        build_ok = True
        store = _get_persistent_store(ctx)
        if store.get("house_7x7"):
            if early_stop or _is_near_night_early(get_world_time(ctx)):
                logger.info("Verifying shelter integrity before storage (near night)...")
                build_ok = _ensure_house_matches_plan(ctx)
                if not build_ok:
                    logger.info("ERROR: Shelter verification failed after mining")
        else:
            if early_stop or _is_near_night_early(get_world_time(ctx)):
                logger.info("Building shelter before storage...")
                build_ok = _build_night_shelter(ctx)
                if not build_ok:
                    logger.info("ERROR: Failed to build shelter after mining")
        if (early_stop or _is_near_night_early(get_world_time(ctx))) and build_ok:
            shelter = store.get("night_shelter")
            if shelter:
                _move_to_shelter(ctx, shelter)
            suite_state["skip_storage"] = True

        mining_ok = success or final_gain >= 5 or early_stop
        _update_stats(ctx, mining_ok and build_ok, final_gain)
        if not mining_ok:
            _explore_for_more_trees(ctx)

        return mining_ok and build_ok

    def t1100_smart_storage(ctx: TestContext) -> bool:
        """Improved storage with better chest management."""
        if not _check_and_recover_death(ctx):
            return False

        if suite_state.pop("skip_storage", False):
            logger.info("Skipping storage to head home before night.")
            return True
        
        logger.info( "Managing storage...")
        store = _get_persistent_store(ctx)
        
        chests = store.get("wood_chests", [])

        
        # Ensure we have at least one chest
        if not chests:
            if not _setup_initial_storage(ctx):
                return False
            chests = store.get("wood_chests", [])
        
        # Replant saplings before depositing items
        _replant_saplings(ctx, max_to_plant=8)

        # Try depositing in most recent non-full chest
        for pos in reversed(chests):
            if _deposit_to_chest(ctx, pos):
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
        store = _get_persistent_store(ctx)
        if _is_chest_known_full(store, chest_pos):
            logger.info(f"Skipping known full chest at {chest_pos}")
            return False
        
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
                inv_by_slot = {item.get("slot", -1): item for item in ctx.get_inventory().get("inventory", [])}
                slots_info = _open_container_slots(ctx)
                if not slots_info:
                    do_close_container(ctx)
                    return False
                slots, chest_slots = slots_info

                # Check capacity
                occupied = sum(
                    1 for item in slots
                    if item.get("slot", -1) < chest_slots and item.get("id") != "minecraft:air"
                )
                if occupied >= chest_slots:
                    _update_chest_index_from_open(ctx, chest_pos, slots, chest_slots)
                    do_close_container(ctx)
                    return False
                inventory_start = chest_slots
                sapling_total = sum(
                    item.get("count", 0)
                    for item in slots
                    if item.get("slot", -1) >= inventory_start and item.get("id") in SAPLING_ITEM_IDS
                )

                # Deposit items (keep usable tools and food, store worn tools)
                for item in slots:
                    slot_idx = item.get("slot", -1)
                    if slot_idx >= inventory_start:
                        item_id = item.get("id", "")
                        if not item_id or item_id == "minecraft:air":
                            continue
                        is_tool = any(t in item_id for t in TOOL_ITEM_SUBSTRINGS)
                        is_food = any(t in item_id for t in FOOD_ITEM_SUBSTRINGS)

                        if item_id in SAPLING_ITEM_IDS:
                            if sapling_total > 8:
                                safe_inventory_click(ctx, slot_idx, "QUICK_MOVE")
                                time.sleep(0.1)
                                sapling_total -= item.get("count", 0)
                            continue

                        damage = item.get("damage")
                        if damage is None:
                            inv_slot = _screen_slot_to_inventory_slot(slot_idx, chest_slots)
                            if inv_slot is not None:
                                damage = inv_by_slot.get(inv_slot, {}).get("damage")

                        if is_tool:
                            if _is_worn_tool(item_id, damage):
                                safe_inventory_click(ctx, slot_idx, "QUICK_MOVE")
                                time.sleep(0.1)
                            continue

                        if not is_food:
                            safe_inventory_click(ctx, slot_idx, "QUICK_MOVE")
                            time.sleep(0.1)
                
                screen_after = ctx.client.transport.dispatch("get_screen", {})
                screen_data_after = screen_after.get("data", screen_after)
                slots_after = screen_data_after.get("slots", [])
                _update_chest_index_from_open(ctx, chest_pos, slots_after, chest_slots)
                do_close_container(ctx)
                return True
            except Exception as e:
                logger.info( f"Deposit error: {e}")
                do_close_container(ctx)
                return False
        
        return False

    def _deposit_items_to_chest(ctx: TestContext, chest_pos: List[int], item_ids: List[str]) -> bool:
        store = _get_persistent_store(ctx)
        if _is_chest_known_full(store, chest_pos):
            logger.info(f"Skipping known full chest at {chest_pos}")
            return False
        if not _move_near_with_reset(ctx, chest_pos, timeout=20.0):
            return False
        if not do_open_container(ctx, (chest_pos[0], chest_pos[1], chest_pos[2])):
            return False
        slots_info = _open_container_slots(ctx)
        if not slots_info:
            do_close_container(ctx)
            return False
        slots, chest_slots = slots_info
        moved = False
        for item in slots:
            slot_idx = item.get("slot", -1)
            if slot_idx < chest_slots:
                continue
            item_id = item.get("id", "")
            if item_id in item_ids:
                safe_inventory_click(ctx, slot_idx, "QUICK_MOVE")
                time.sleep(0.1)
                moved = True
        screen_after = ctx.client.transport.dispatch("get_screen", {})
        screen_data_after = screen_after.get("data", screen_after)
        slots_after = screen_data_after.get("slots", [])
        _update_chest_index_from_open(ctx, chest_pos, slots_after, chest_slots)
        do_close_container(ctx)
        return moved

    def _select_support_block(ctx: TestContext) -> Optional[str]:
        inv = get_inventory_counts(ctx)
        for item_id in LOG_BLOCK_IDS:
            if inv.get(item_id, 0) > 0:
                return item_id
        for item_id in ("minecraft:cobblestone", "minecraft:dirt", "minecraft:stone"):
            if inv.get(item_id, 0) > 0:
                return item_id
        plank = _pick_any_plank(ctx)
        if plank:
            return plank
        return None

    def _place_chest_with_support(
        ctx: TestContext,
        tx: int,
        ty: int,
        tz: int,
        support_block: Optional[str],
    ) -> bool:
        if not _is_placeable_target(block_id_at(ctx, tx, ty, tz)):
            return False
        if not _move_near_with_reset(ctx, [tx, ty, tz], timeout=15.0):
            return False
        below = block_id_at(ctx, tx, ty - 1, tz)
        if not _is_solid_support_block(below):
            if not support_block or not _is_placeable_target(below):
                return False
            logger.info(f"Placing temporary support block at [{tx}, {ty - 1}, {tz}]")
            if not place_block_at(ctx, tx, ty - 1, tz, support_block):
                return False
            time.sleep(0.2)
            below = block_id_at(ctx, tx, ty - 1, tz)
        if not _is_solid_support_block(below):
            return False
        return bot_place_block(ctx, tx, ty, tz, "minecraft:chest")

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
        
        store = _get_persistent_store(ctx)
        border_positions = _get_house_border_positions(store)
        support_block = _select_support_block(ctx)
        if border_positions:
            logger.info("Placing overflow chest in house border area...")
        for pos in border_positions:
            tx, ty, tz = pos
            if _place_chest_with_support(ctx, tx, ty, tz, support_block):
                return [tx, ty, tz]

        # Find adjacent position
        for dx, dz in [(1, 0), (-1, 0), (0, 1), (0, -1), (2, 0), (0, 2)]:
            tx, ty, tz = cx + dx, cy, cz + dz
            if _place_chest_with_support(ctx, tx, ty, tz, support_block):
                return [tx, ty, tz]

        fallback = find_place_pos_near(ctx, int(cx), int(cy), int(cz), radius=6)
        if fallback:
            tx, ty, tz = fallback
            if _place_chest_with_support(ctx, tx, ty, tz, support_block):
                return [tx, ty, tz]

        logger.info("ERROR: Could not find solid support for overflow chest placement")
        
        return None

    def _is_adjacent(pos_a: List[int], pos_b: List[int]) -> bool:
        if not pos_a or not pos_b:
            return False
        dx = abs(pos_a[0] - pos_b[0])
        dy = abs(pos_a[1] - pos_b[1])
        dz = abs(pos_a[2] - pos_b[2])
        return dy == 0 and ((dx == 1 and dz == 0) or (dx == 0 and dz == 1))

    def _find_sign_item_id(ctx: TestContext) -> Optional[str]:
        counts = get_inventory_counts(ctx)
        for item_id in counts:
            if item_id.endswith("_sign") and "hanging" not in item_id:
                return item_id
        return None

    def _ensure_sign_item(ctx: TestContext) -> Optional[str]:
        sign_item = _find_sign_item_id(ctx)
        if sign_item:
            return sign_item
        table_pos = _ensure_crafting_table_placed(ctx)
        if table_pos:
            ensure_crafting_table_open(ctx, table_pos=table_pos)
        if not robust_craft(ctx, "minecraft:oak_sign", 1):
            logger.info("ERROR: Failed to craft sign")
            do_close_container(ctx)
            return None
        do_close_container(ctx)
        if safe_count_item(ctx, "minecraft:oak_sign") > 0:
            return "minecraft:oak_sign"
        return _find_sign_item_id(ctx)

    def _set_sign_text(ctx: TestContext, pos: List[int], text: str) -> bool:
        logger.info(f"Commands blocked; skipping sign text write at {pos}. Expect label: {text}")
        return True

    def _find_sign_spot_for_chest_pair(ctx: TestContext, chest_a: List[int], chest_b: List[int]) -> Optional[List[int]]:
        candidates = []
        for chest in (chest_a, chest_b):
            for dx, dz in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
                pos = [chest[0] + dx, chest[1], chest[2] + dz]
                if _pos_key(pos) == _pos_key(chest_a) or _pos_key(pos) == _pos_key(chest_b):
                    continue
                candidates.append(pos)
        for pos in _dedupe_positions(candidates):
            if not _is_placeable_target(block_id_at(ctx, pos[0], pos[1], pos[2])):
                continue
            below = block_id_at(ctx, pos[0], pos[1] - 1, pos[2])
            if not _is_solid_support_block(below):
                continue
            return pos
        return None

    def _ensure_planks_sign(ctx: TestContext, entry: dict) -> bool:
        chest_a = _normalize_pos(entry.get("chest_a"))
        chest_b = _normalize_pos(entry.get("chest_b"))
        if not chest_a or not chest_b:
            return False
        sign_pos = _normalize_pos(entry.get("sign"))
        if sign_pos:
            bid = block_id_at(ctx, sign_pos[0], sign_pos[1], sign_pos[2])
            if not bid or "sign" not in bid:
                sign_pos = None
        if not sign_pos:
            sign_pos = _find_sign_spot_for_chest_pair(ctx, chest_a, chest_b)
            if not sign_pos:
                logger.info("ERROR: Could not find sign placement spot for planks chest")
                return False
            entry["sign"] = sign_pos
        bid = block_id_at(ctx, sign_pos[0], sign_pos[1], sign_pos[2])
        if not bid or "sign" not in bid:
            sign_item = _ensure_sign_item(ctx)
            if not sign_item:
                return False
            if not _move_near_with_reset(ctx, sign_pos, timeout=10.0):
                logger.info(f"ERROR: Could not reach sign position at {sign_pos}")
                return False
            if not bot_place_block(ctx, sign_pos[0], sign_pos[1], sign_pos[2], sign_item):
                logger.info("ERROR: Failed to place planks sign")
                return False
            do_close_container(ctx)
        if not _set_sign_text(ctx, sign_pos, "PLANKS"):
            logger.info("ERROR: Failed to label planks sign")
            return False
        return True

    def _ensure_chest_items(ctx: TestContext, count: int) -> bool:
        available = safe_count_item(ctx, "minecraft:chest")
        if available >= count:
            return True
        table_pos = _ensure_crafting_table_placed(ctx)
        if not table_pos:
            logger.info("ERROR: Could not place crafting table for double chest")
            return False
        if not ensure_crafting_table_open(ctx, table_pos=table_pos):
            logger.info("ERROR: Could not open crafting table for double chest")
            return False
        needed = count - available
        for _ in range(needed):
            if count_any_planks(ctx) < 8:
                if not _ensure_planks(ctx, 8):
                    logger.info("ERROR: Not enough planks for chest crafting")
                    do_close_container(ctx)
                    return False
            if not craft_chest_manual(ctx):
                logger.info("ERROR: Failed to craft chest manually")
                do_close_container(ctx)
                return False
        do_close_container(ctx)
        return safe_count_item(ctx, "minecraft:chest") >= count

    def _find_double_chest_spot(ctx: TestContext) -> Optional[Tuple[List[int], List[int]]]:
        store = _get_persistent_store(ctx)
        
        # Always start from current player position (house positions may be in void)
        px, py, pz = ctx.get_position()
        candidates = []
        for dx in range(-8, 9):
            for dz in range(-8, 9):
                candidates.append([int(px) + dx, int(py), int(pz) + dz])
        
        # Also add house border positions as fallback
        house_borders = _get_house_border_positions(store)
        if house_borders:
            candidates.extend(house_borders)
        
        candidates = _dedupe_positions(candidates)
        
        for pos in candidates:
            if not _is_placeable_target(block_id_at(ctx, pos[0], pos[1], pos[2])):
                continue
            if not _is_solid_support_block(block_id_at(ctx, pos[0], pos[1] - 1, pos[2])):
                continue
            for dx, dz in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
                other = [pos[0] + dx, pos[1], pos[2] + dz]
                if not _is_placeable_target(block_id_at(ctx, other[0], other[1], other[2])):
                    continue
                if not _is_solid_support_block(block_id_at(ctx, other[0], other[1] - 1, other[2])):
                    continue
                # Sign spot is optional - try but don't require
                sign_spot = _find_sign_spot_for_chest_pair(ctx, pos, other)
                if sign_spot:
                    return pos, other
        
        # Second pass - accept without sign requirement
        for pos in candidates:
            if not _is_placeable_target(block_id_at(ctx, pos[0], pos[1], pos[2])):
                continue
            if not _is_solid_support_block(block_id_at(ctx, pos[0], pos[1] - 1, pos[2])):
                continue
            for dx, dz in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
                other = [pos[0] + dx, pos[1], pos[2] + dz]
                if not _is_placeable_target(block_id_at(ctx, other[0], other[1], other[2])):
                    continue
                if not _is_solid_support_block(block_id_at(ctx, other[0], other[1] - 1, other[2])):
                    continue
                return pos, other
        
        return None

    def _create_planks_double_chest(ctx: TestContext) -> Optional[dict]:
        if not _ensure_chest_items(ctx, 2):
            return None
        spot = _find_double_chest_spot(ctx)
        if not spot:
            logger.info("ERROR: Could not find placement for planks double chest")
            return None
        chest_a, chest_b = spot
        
        # Place first chest
        if not _move_near_with_reset(ctx, chest_a, timeout=20.0):
            logger.info("ERROR: Could not reach planks chest placement")
            return None
        if not bot_place_block(ctx, chest_a[0], chest_a[1], chest_a[2], "minecraft:chest"):
            logger.info("ERROR: Failed to place first planks chest")
            return None
        
        # For double chest to merge, player should be positioned perpendicular to the chest line
        # Calculate perpendicular position to stand at
        dx = chest_b[0] - chest_a[0]
        dz = chest_b[2] - chest_a[2]
        
        # Find perpendicular stand position (rotate 90 degrees)
        if dx != 0:  # Chests are along X axis, stand on Z side
            perp_pos = [chest_a[0], chest_a[1], chest_a[2] + (1 if dz == 0 else -dz)]
        else:  # Chests are along Z axis, stand on X side
            perp_pos = [chest_a[0] + (1 if dx == 0 else -dx), chest_a[1], chest_a[2]]
        
        # Move to perpendicular position before placing second chest
        move_near(ctx, perp_pos[0], perp_pos[1], perp_pos[2], timeout=10.0)
        time.sleep(0.5)
        
        # Place second chest - it should merge with the first
        if not bot_place_block(ctx, chest_b[0], chest_b[1], chest_b[2], "minecraft:chest"):
            logger.info("ERROR: Failed to place second planks chest")
            return None
        
        entry = {"chest_a": chest_a, "chest_b": chest_b, "sign": None}
        if not _ensure_planks_sign(ctx, entry):
            return None
        return entry

    def _get_valid_planks_chests(ctx: TestContext) -> List[dict]:
        store = _get_persistent_store(ctx)
        entries = store.get("planks_chests", [])
        if not isinstance(entries, list):
            entries = []
        valid = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            chest_a = _normalize_pos(entry.get("chest_a"))
            chest_b = _normalize_pos(entry.get("chest_b"))
            sign_pos = _normalize_pos(entry.get("sign"))
            if not chest_a or not chest_b or not _is_adjacent(chest_a, chest_b):
                continue
            if "chest" not in block_id_at(ctx, chest_a[0], chest_a[1], chest_a[2]):
                continue
            if "chest" not in block_id_at(ctx, chest_b[0], chest_b[1], chest_b[2]):
                continue
            valid.append({"chest_a": chest_a, "chest_b": chest_b, "sign": sign_pos})
        store["planks_chests"] = valid
        return valid

    def _ensure_planks_double_chest(ctx: TestContext) -> Optional[dict]:
        store = _get_persistent_store(ctx)
        entries = _get_valid_planks_chests(ctx)
        for entry in entries:
            if _ensure_planks_sign(ctx, entry):
                _save_persistent(ctx)
                return entry
        entry = _create_planks_double_chest(ctx)
        if not entry:
            return None
        entries.append(entry)
        store["planks_chests"] = entries
        _save_persistent(ctx)
        return entry

    def _open_container_slots(ctx: TestContext) -> Optional[Tuple[List[dict], int]]:
        screen = ctx.client.transport.dispatch("get_screen", {})
        data = screen.get("data", screen)
        slots = data.get("slots", [])
        total_slots = int(data.get("total_slots") or 0)
        if total_slots <= 0:
            total_slots = len(slots)
        if total_slots <= 36:
            return None
        chest_slots = total_slots - 36
        return slots, chest_slots

    def _count_items_in_chest(slots: List[dict], chest_slots: int, item_ids: List[str]) -> int:
        total = 0
        for item in slots:
            slot_idx = item.get("slot", -1)
            if slot_idx < 0 or slot_idx >= chest_slots:
                continue
            if item.get("id") in item_ids:
                total += item.get("count", 0)
        return total

    def _withdraw_logs_from_open_chest(ctx: TestContext, slots: List[dict], chest_slots: int, remaining: int) -> int:
        taken = 0
        for item in slots:
            if remaining <= 0:
                break
            slot_idx = item.get("slot", -1)
            if slot_idx < 0 or slot_idx >= chest_slots:
                continue
            item_id = item.get("id", "")
            if item_id not in LOG_BLOCK_IDS:
                continue
            count = item.get("count", 0)
            if count <= 0:
                continue
            safe_inventory_click(ctx, slot_idx, "QUICK_MOVE")
            time.sleep(0.1)
            taken += count
            remaining -= count
        return taken

    def _deposit_logs_to_open_chest(ctx: TestContext, slots: List[dict], chest_slots: int) -> None:
        for item in slots:
            slot_idx = item.get("slot", -1)
            if slot_idx < chest_slots:
                continue
            item_id = item.get("id", "")
            if item_id in LOG_BLOCK_IDS:
                safe_inventory_click(ctx, slot_idx, "QUICK_MOVE")
                time.sleep(0.1)

    def _collect_logs_from_chests(ctx: TestContext, chests: List[List[int]], target_logs: int) -> Tuple[int, Optional[List[int]]]:
        store = _get_persistent_store(ctx)
        ordered_chests = _merge_chests_with_index(store, chests, LOG_BLOCK_IDS)
        total = 0
        last_chest = None
        for pos in ordered_chests:
            if total >= target_logs:
                break
            if not _move_near_with_reset(ctx, pos, timeout=20.0):
                continue
            if not do_open_container(ctx, (pos[0], pos[1], pos[2])):
                continue
            slots_info = _open_container_slots(ctx)
            if not slots_info:
                do_close_container(ctx)
                continue
            slots, chest_slots = slots_info
            taken = _withdraw_logs_from_open_chest(ctx, slots, chest_slots, target_logs - total)
            screen_after = ctx.client.transport.dispatch("get_screen", {})
            screen_data_after = screen_after.get("data", screen_after)
            slots_after = screen_data_after.get("slots", [])
            _update_chest_index_from_open(ctx, pos, slots_after, chest_slots)
            do_close_container(ctx)
            if taken > 0:
                total += taken
                last_chest = pos
        return total, last_chest

    def _scan_for_extra_chests(ctx: TestContext, origin: List[int], radius: int, exclude: List[List[int]]) -> List[List[int]]:
        found = []
        excluded = {_pos_key(pos) for pos in exclude if pos}
        ox, oy, oz = origin
        total_positions = (radius * 2 + 1) ** 2 * 5
        scanned = 0
        last_log = time.time()
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                x = ox + dx
                z = oz + dz
                for dy in range(-2, 3):
                    y = oy + dy
                    pos = [x, y, z]
                    if _pos_key(pos) in excluded:
                        scanned += 1
                        if time.time() - last_log >= 3.0:
                            percent = (scanned / total_positions) * 100 if total_positions else 0
                            logger.info(f"Chest scan progress: {scanned}/{total_positions} ({percent:.1f}%), found={len(found)}")
                            last_log = time.time()
                        continue
                    bid = block_id_at(ctx, x, y, z)
                    scanned += 1
                    if time.time() - last_log >= 3.0:
                        percent = (scanned / total_positions) * 100 if total_positions else 0
                        logger.info(f"Chest scan progress: {scanned}/{total_positions} ({percent:.1f}%), found={len(found)}")
                        last_log = time.time()
                    if bid and "chest" in bid:
                        found.append(pos)
                        break
        return _dedupe_positions(found)

    def _build_circle_offsets(radius: int) -> List[Tuple[int, int]]:
        offsets = []
        r_sq = radius * radius
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                dist_sq = dx * dx + dz * dz
                if dist_sq <= r_sq:
                    offsets.append((dx, dz, dist_sq))
        offsets.sort(key=lambda item: (item[2], item[0], item[1]))
        return [(dx, dz) for dx, dz, _ in offsets]

    def _scan_for_extra_chests_resumable(
        ctx: TestContext,
        origin: List[int],
        radius: int,
        exclude: List[List[int]],
        state: dict,
    ) -> List[List[int]]:
        found = []
        excluded = {_pos_key(pos) for pos in exclude if pos}
        offsets = _build_circle_offsets(radius)
        dy_values = [-2, -1, 0, 1, 2]

        total_positions = len(offsets) * len(dy_values)
        offset_index = int(state.get("offset_index", 0))
        dy_index = int(state.get("dy_index", 0))
        if offset_index < 0:
            offset_index = 0
        if offset_index > len(offsets):
            offset_index = len(offsets)
        if dy_index < 0 or dy_index >= len(dy_values):
            dy_index = 0
        last_log = time.time()

        for idx in range(offset_index, len(offsets)):
            dx, dz = offsets[idx]
            x = int(origin[0]) + dx
            z = int(origin[2]) + dz
            for dy_pos in range(dy_index, len(dy_values)):
                y = int(origin[1]) + dy_values[dy_pos]
                pos = [x, y, z]
                scanned = idx * len(dy_values) + (dy_pos + 1)
                if _pos_key(pos) in excluded:
                    if time.time() - last_log >= 3.0:
                        percent = (scanned / total_positions) * 100 if total_positions else 0
                        logger.info(
                            f"Chest scan progress: {scanned}/{total_positions} ({percent:.1f}%), found={len(found)}"
                        )
                        state["offset_index"] = idx
                        state["dy_index"] = dy_pos
                        state["last_scan"] = int(time.time())
                        _save_persistent(ctx)
                        last_log = time.time()
                    continue

                bid = block_id_at(ctx, x, y, z)
                if bid and "chest" in bid:
                    found.append(pos)
                    break

                scanned = idx * len(dy_values) + (dy_pos + 1)
                if time.time() - last_log >= 3.0:
                    percent = (scanned / total_positions) * 100 if total_positions else 0
                    logger.info(
                        f"Chest scan progress: {scanned}/{total_positions} ({percent:.1f}%), found={len(found)}"
                    )
                    state["offset_index"] = idx
                    state["dy_index"] = dy_pos
                    state["last_scan"] = int(time.time())
                    _save_persistent(ctx)
                    last_log = time.time()
            dy_index = 0
            state["offset_index"] = idx + 1
            state["dy_index"] = 0

        state["completed"] = True
        state["offset_index"] = len(offsets)
        state["dy_index"] = 0
        state["last_scan"] = int(time.time())
        _save_persistent(ctx)
        return _dedupe_positions(found)

    def _dimension_key(ctx: TestContext) -> str:
        return ctx.get_state().get("dimension", "minecraft:overworld")

    def _chunk_for_pos(x: int, z: int) -> Tuple[int, int]:
        return x // 16, z // 16

    def _advance_spiral(state: dict) -> None:
        dirs = [(1, 0), (0, 1), (-1, 0), (0, -1)]
        direction = int(state.get("dir", 0)) % 4
        leg_len = int(state.get("leg_len", 1))
        leg_progress = int(state.get("leg_progress", 0))
        legs_done = int(state.get("legs_done", 0))
        cx = int(state.get("cx", 0))
        cz = int(state.get("cz", 0))

        if leg_progress >= leg_len:
            direction = (direction + 1) % 4
            leg_progress = 0
            legs_done += 1
            if legs_done % 2 == 0:
                leg_len += 1

        dx, dz = dirs[direction]
        cx += dx
        cz += dz
        leg_progress += 1

        state["cx"] = cx
        state["cz"] = cz
        state["dir"] = direction
        state["leg_len"] = leg_len
        state["leg_progress"] = leg_progress
        state["legs_done"] = legs_done

    def _scan_world_cache(ctx: TestContext, state: dict) -> bool:
        dimension = _dimension_key(ctx)
        if state.get("dimension") and state.get("dimension") != dimension:
            return False
        state["dimension"] = dimension

        origin_chunk = state.get("origin_chunk")
        if not origin_chunk:
            px, py, pz = ctx.get_position()
            origin_chunk = [int(px) // 16, int(pz) // 16]
            state["origin_chunk"] = origin_chunk

        spiral = state.get("spiral")
        if not isinstance(spiral, dict):
            spiral = {
                "cx": origin_chunk[0],
                "cz": origin_chunk[1],
                "dir": 0,
                "leg_len": 1,
                "leg_progress": 0,
                "legs_done": 0,
            }
            state["spiral"] = spiral

        chunk_progress = state.get("chunk_progress")
        if not isinstance(chunk_progress, dict):
            chunk_progress = {
                "cx": spiral["cx"],
                "cz": spiral["cz"],
                "x": 0,
                "z": 0,
                "y": WORLD_SCAN_Y_MIN,
            }
            state["chunk_progress"] = chunk_progress

        start = time.time()
        last_log = time.time()
        total_in_chunk = 16 * 16 * (WORLD_SCAN_Y_MAX - WORLD_SCAN_Y_MIN + 1)
        rows = []
        deletes = []
        scanned_in_chunk = 0

        while time.time() - start < WORLD_SCAN_TIME_BUDGET_S:
            cx = int(chunk_progress.get("cx", spiral["cx"]))
            cz = int(chunk_progress.get("cz", spiral["cz"]))
            x_idx = int(chunk_progress.get("x", 0))
            z_idx = int(chunk_progress.get("z", 0))
            y = int(chunk_progress.get("y", WORLD_SCAN_Y_MIN))

            wx = cx * 16 + x_idx
            wz = cz * 16 + z_idx
            block = ctx.get_block(int(wx), int(y), int(wz))
            data = block.get("data", block)
            block_id = data.get("id", "") or block.get("id", "")
            props_json = _block_props_json(block)
            chunk_x, chunk_z = _chunk_for_pos(wx, wz)
            now_ts = int(time.time())

            if block_id and block_id not in EXCLUDED_BLOCK_IDS:
                rows.append(
                    (dimension, wx, y, wz, chunk_x, chunk_z, block_id, props_json, now_ts)
                )
            else:
                deletes.append((dimension, wx, y, wz))

            scanned_in_chunk += 1
            chunk_progress["y"] = y + 1

            if chunk_progress["y"] > WORLD_SCAN_Y_MAX:
                chunk_progress["y"] = WORLD_SCAN_Y_MIN
                chunk_progress["x"] = x_idx + 1
                if chunk_progress["x"] >= 16:
                    chunk_progress["x"] = 0
                    chunk_progress["z"] = z_idx + 1
                    if chunk_progress["z"] >= 16:
                        _cache_block_rows(rows)
                        _cache_block_deletes(deletes)
                        rows = []
                        deletes = []
                        _record_chunk_scan(dimension, cx, cz)
                        _advance_spiral(spiral)
                        chunk_progress = {
                            "cx": spiral["cx"],
                            "cz": spiral["cz"],
                            "x": 0,
                            "z": 0,
                            "y": WORLD_SCAN_Y_MIN,
                        }
                        state["chunk_progress"] = chunk_progress
                        scanned_in_chunk = 0

            if len(rows) >= 250:
                _cache_block_rows(rows)
                rows = []
            if len(deletes) >= 250:
                _cache_block_deletes(deletes)
                deletes = []

            if time.time() - last_log >= 3.0:
                percent = (scanned_in_chunk / total_in_chunk) * 100 if total_in_chunk else 0
                logger.info(
                    f"World scan chunk {cx},{cz}: {scanned_in_chunk}/{total_in_chunk} ({percent:.1f}%)"
                )
                state["last_scan"] = int(time.time())
                _save_persistent(ctx)
                last_log = time.time()

        if rows:
            _cache_block_rows(rows)
        if deletes:
            _cache_block_deletes(deletes)
        state["last_scan"] = int(time.time())
        _save_persistent(ctx)
        return True

    def _convert_logs_to_planks(ctx: TestContext, target_logs: int) -> int:
        inv_counts = get_inventory_counts(ctx)
        log_counts = {item_id: inv_counts.get(item_id, 0) for item_id in LOG_BLOCK_IDS}
        total_logs = sum(log_counts.values())
        if total_logs == 0:
            return 0
        remaining = min(target_logs, total_logs)
        converted = 0
        for log_id, count in log_counts.items():
            if remaining <= 0:
                break
            if count <= 0:
                continue
            use_logs = min(count, remaining)
            target_plank = log_id.replace("_log", "_planks")
            current_planks = safe_count_item(ctx, target_plank)
            desired_total = current_planks + (use_logs * 4)
            if not robust_craft(ctx, target_plank, desired_total):
                logger.info(f"ERROR: Failed to craft planks from {log_id}")
                return converted
            converted += use_logs
            remaining -= use_logs
        return converted

    def _return_extra_logs(ctx: TestContext, chest_pos: Optional[List[int]]) -> None:
        if not chest_pos:
            return
        extra_logs = sum(safe_count_item(ctx, log_id) for log_id in LOG_BLOCK_IDS)
        if extra_logs <= 0:
            return
        if not _move_near_with_reset(ctx, chest_pos, timeout=15.0):
            return
        if not do_open_container(ctx, (chest_pos[0], chest_pos[1], chest_pos[2])):
            return
        slots_info = _open_container_slots(ctx)
        if not slots_info:
            do_close_container(ctx)
            return
        slots, chest_slots = slots_info
        _deposit_logs_to_open_chest(ctx, slots, chest_slots)
        screen_after = ctx.client.transport.dispatch("get_screen", {})
        screen_data_after = screen_after.get("data", screen_after)
        slots_after = screen_data_after.get("slots", [])
        _update_chest_index_from_open(ctx, chest_pos, slots_after, chest_slots)
        do_close_container(ctx)

    def _is_chest_full(ctx: TestContext, chest_pos: List[int]) -> Optional[bool]:
        if not _move_near_with_reset(ctx, chest_pos, timeout=15.0):
            return None
        if not do_open_container(ctx, (chest_pos[0], chest_pos[1], chest_pos[2])):
            return None
        slots_info = _open_container_slots(ctx)
        if not slots_info:
            do_close_container(ctx)
            return None
        slots, chest_slots = slots_info
        occupied = sum(
            1 for item in slots
            if item.get("slot", -1) < chest_slots and item.get("id") != "minecraft:air"
        )
        _update_chest_index_from_open(ctx, chest_pos, slots, chest_slots)
        do_close_container(ctx)
        return occupied >= chest_slots

    def _deposit_planks_to_chest(ctx: TestContext, chest_pos: List[int]) -> Optional[Tuple[int, int]]:
        if not _move_near_with_reset(ctx, chest_pos, timeout=15.0):
            return None
        if not do_open_container(ctx, (chest_pos[0], chest_pos[1], chest_pos[2])):
            return None
        slots_info = _open_container_slots(ctx)
        if not slots_info:
            do_close_container(ctx)
            return None
        slots, chest_slots = slots_info
        before = _count_items_in_chest(slots, chest_slots, PLANK_ITEM_IDS)
        for item in slots:
            slot_idx = item.get("slot", -1)
            if slot_idx < chest_slots:
                continue
            item_id = item.get("id", "")
            if item_id in PLANK_ITEM_IDS:
                safe_inventory_click(ctx, slot_idx, "QUICK_MOVE")
                time.sleep(0.1)
        screen = ctx.client.transport.dispatch("get_screen", {})
        data = screen.get("data", screen)
        slots_after = data.get("slots", [])
        _update_chest_index_from_open(ctx, chest_pos, slots_after, chest_slots)
        after = _count_items_in_chest(slots_after, chest_slots, PLANK_ITEM_IDS)
        do_close_container(ctx)
        return before, after

    def _count_planks_in_chest(ctx: TestContext, chest_pos: List[int]) -> Optional[int]:
        if not _move_near_with_reset(ctx, chest_pos, timeout=15.0):
            return None
        if not do_open_container(ctx, (chest_pos[0], chest_pos[1], chest_pos[2])):
            return None
        slots_info = _open_container_slots(ctx)
        if not slots_info:
            do_close_container(ctx)
            return None
        slots, chest_slots = slots_info
        count = _count_items_in_chest(slots, chest_slots, PLANK_ITEM_IDS)
        _update_chest_index_from_open(ctx, chest_pos, slots, chest_slots)
        do_close_container(ctx)
        return count

    def _count_charcoal_inventory(ctx: TestContext) -> int:
        return safe_count_item(ctx, "minecraft:charcoal") + safe_count_item(ctx, "minecraft:coal")

    def _choose_log_type_for_charcoal(ctx: TestContext, store: dict) -> Optional[str]:
        index = _get_chest_index(store)
        counts = {}
        inv_counts = get_inventory_counts(ctx)
        for log_id in LOG_BLOCK_IDS:
            counts[log_id] = counts.get(log_id, 0) + inv_counts.get(log_id, 0)
        for pos in _unique_chest_positions_from_index(store):
            entry = index.get(_pos_key(pos))
            if not entry:
                continue
            contents = entry.get("contents") or {}
            for log_id in LOG_BLOCK_IDS:
                counts[log_id] = counts.get(log_id, 0) + int(contents.get(log_id, 0))
        if not counts:
            return None
        best_log = max(counts.items(), key=lambda item: item[1])[0]
        return best_log if counts.get(best_log, 0) > 0 else None

    def _collect_furnace_output(ctx: TestContext, furnace_pos: List[int]) -> None:
        if not do_open_container(ctx, (furnace_pos[0], furnace_pos[1], furnace_pos[2]), timeout=3.0):
            return
        screen = ctx.client.transport.dispatch("get_screen", {})
        slots = get_inv_slots(screen.get("data", screen))
        if len(slots) > 2:
            output = slots[2]
            item_id = output.get("id", "")
            if item_id in ("minecraft:charcoal", "minecraft:coal"):
                safe_inventory_click(ctx, 2, "QUICK_MOVE")
                time.sleep(0.1)
        do_close_container(ctx)

    def _furnace_top_off(
        ctx: TestContext,
        furnace_pos: List[int],
        log_item: str,
        fuel_item: str,
    ) -> bool:
        if not do_open_container(ctx, (furnace_pos[0], furnace_pos[1], furnace_pos[2]), timeout=3.0):
            return False
        screen = ctx.client.transport.dispatch("get_screen", {})
        slots = get_inv_slots(screen.get("data", screen))
        if len(slots) < 3:
            do_close_container(ctx)
            return False

        output = slots[2]
        if output.get("id") in ("minecraft:charcoal", "minecraft:coal") and output.get("count", 0) > 0:
            safe_inventory_click(ctx, 2, "QUICK_MOVE")
            time.sleep(0.1)

        input_slot = slots[0]
        fuel_slot = slots[1]
        input_id = input_slot.get("id", "")
        if input_id and input_id != "minecraft:air" and input_id != log_item:
            log_item = input_id

        log_slot_idx = None
        fuel_slot_idx = None
        log_slots = []
        fuel_slots = []
        for idx, item in enumerate(slots):
            if idx < 3:
                continue
            item_id = item.get("id", "")
            if item_id == log_item:
                log_slots.append(idx)
            if item_id == fuel_item:
                fuel_slots.append(idx)

        if log_slots:
            log_slot_idx = log_slots[0]
        if fuel_slots:
            fuel_slot_idx = fuel_slots[0]
            if fuel_item == log_item and len(log_slots) > 1:
                fuel_slot_idx = log_slots[1]

        if log_slot_idx is not None:
            if input_slot.get("id") in ("", "minecraft:air", log_item) and input_slot.get("count", 0) < 64:
                safe_inventory_click(ctx, log_slot_idx, "QUICK_MOVE")
                time.sleep(0.1)

        if fuel_slot_idx is not None:
            if fuel_slot.get("id") in ("", "minecraft:air", fuel_item) and fuel_slot.get("count", 0) < 64:
                safe_inventory_click(ctx, fuel_slot_idx, "QUICK_MOVE")
                time.sleep(0.1)

        do_close_container(ctx)
        return True

    def _ensure_charcoal_for_torches(ctx: TestContext, furnace_pos: List[int], required: int) -> bool:
        store = _get_persistent_store(ctx)
        _collect_furnace_output(ctx, furnace_pos)
        current = _count_charcoal_inventory(ctx)
        if current >= required:
            return True

        chests = _gather_known_chests(store)
        need = required - current
        if _tower_withdraw_items(ctx, chests, "minecraft:charcoal", need) > 0:
            current = _count_charcoal_inventory(ctx)
        if current < required:
            _tower_withdraw_items(ctx, chests, "minecraft:coal", need)
            current = _count_charcoal_inventory(ctx)
        if current >= required:
            return True

        log_item = _choose_log_type_for_charcoal(ctx, store)
        if not log_item:
            logger.info("ERROR: No logs available to smelt for charcoal")
            return False

        missing = required - current
        if safe_count_item(ctx, log_item) < missing:
            _tower_withdraw_items(ctx, chests, log_item, missing)
        if safe_count_item(ctx, log_item) < missing:
            logger.info("ERROR: Not enough logs to smelt for charcoal")
            return False

        fuel_item = "minecraft:charcoal" if safe_count_item(ctx, "minecraft:charcoal") > 0 else None
        if not fuel_item and safe_count_item(ctx, "minecraft:coal") > 0:
            fuel_item = "minecraft:coal"
        if not fuel_item:
            plank = _pick_any_plank(ctx)
            if not plank:
                target_plank = log_item.replace("_log", "_planks")
                if not robust_craft(ctx, target_plank, 4):
                    plank = None
                else:
                    plank = target_plank
            fuel_item = plank if plank else log_item

        remaining = missing
        attempts = 0
        while remaining > 0 and attempts < 6:
            if _is_near_night_early(get_world_time(ctx)):
                t1100_wait_out_night(ctx)
            if not _furnace_top_off(ctx, furnace_pos, log_item, fuel_item):
                return False
            wait_items = min(remaining, 8)
            time.sleep(max(12.0, wait_items * 10.5))
            _collect_furnace_output(ctx, furnace_pos)
            current = _count_charcoal_inventory(ctx)
            remaining = max(0, required - current)
            if remaining == 0:
                break
            attempts += 1

        return _count_charcoal_inventory(ctx) >= required

    def _ensure_sticks_for_torches(ctx: TestContext, required: int) -> bool:
        store = _get_persistent_store(ctx)
        have = safe_count_item(ctx, "minecraft:stick")
        if have >= required:
            return True
        available = _count_items_in_index(store, ["minecraft:stick"])
        if available > 0:
            chests = _gather_known_chests(store)
            _tower_withdraw_items(ctx, chests, "minecraft:stick", required - have)
            have = safe_count_item(ctx, "minecraft:stick")
        if have >= required:
            return True

        missing = required - have
        planks_needed = math.ceil(missing / 4) * 2
        if count_any_planks(ctx) < planks_needed:
            if not _ensure_planks(ctx, planks_needed, allow_mining=False):
                logger.info("ERROR: Not enough planks to craft sticks")
                return False
        if not robust_craft(ctx, "minecraft:stick", required):
            logger.info("ERROR: Failed to craft sticks for torches")
            return False
        return safe_count_item(ctx, "minecraft:stick") >= required

    def t1100_planks_storage(ctx: TestContext) -> bool:
        logger.info("Planks storage: ensuring planks double chest and label")
        store = _get_persistent_store(ctx)
        planks_entry = _ensure_planks_double_chest(ctx)
        if not planks_entry:
            return False

        target_logs = 64
        known_chests = _dedupe_positions(store.get("wood_chests", []))
        total_logs, last_chest = _collect_logs_from_chests(ctx, known_chests, target_logs)

        if total_logs < target_logs:
            origin = store.get("base_origin") or list(ctx.get_position()[:3])
            exclude = known_chests[:]
            extra_chests = store.get("extra_log_chests", [])
            planks_entries = store.get("planks_chests", [])
            if isinstance(planks_entries, list):
                for entry in planks_entries:
                    if isinstance(entry, dict):
                        pos_a = _normalize_pos(entry.get("chest_a"))
                        pos_b = _normalize_pos(entry.get("chest_b"))
                        if pos_a:
                            exclude.append(pos_a)
                        if pos_b:
                            exclude.append(pos_b)
            exclude.extend(extra_chests)
            found = _scan_for_extra_chests(ctx, origin, radius=20, exclude=exclude)
            if found:
                extra_chests = _dedupe_positions(extra_chests + found)
                store["extra_log_chests"] = extra_chests
                _save_persistent(ctx)
                more_logs, last_found = _collect_logs_from_chests(ctx, extra_chests, target_logs - total_logs)
                total_logs += more_logs
                if last_found:
                    last_chest = last_found

        if total_logs < target_logs:
            logger.info(f"ERROR: Only found {total_logs}/{target_logs} logs in known chests")
            return False

        converted = _convert_logs_to_planks(ctx, target_logs)
        if converted < target_logs:
            logger.info(f"ERROR: Only converted {converted}/{target_logs} logs to planks")
            return False

        _return_extra_logs(ctx, last_chest)

        entries = _get_valid_planks_chests(ctx)
        chosen_entry = None
        for entry in entries:
            full = _is_chest_full(ctx, entry["chest_a"])
            if full is False:
                chosen_entry = entry
                break
        if not chosen_entry:
            chosen_entry = _create_planks_double_chest(ctx)
            if not chosen_entry:
                return False
            entries.append(chosen_entry)
            store["planks_chests"] = entries
            _save_persistent(ctx)

        if not _ensure_planks_sign(ctx, chosen_entry):
            return False

        before_planks = _count_planks_in_chest(ctx, chosen_entry["chest_a"])
        if before_planks is None:
            return False

        deposit_result = _deposit_planks_to_chest(ctx, chosen_entry["chest_a"])
        if not deposit_result:
            return False
        after_planks = deposit_result[1]
        if after_planks <= before_planks:
            logger.info("ERROR: Plank count did not increase after deposit")
            return False
        return True

    def t1104_index_chests(ctx: TestContext) -> bool:
        logger.info("Indexing local storage chests...")
        store = _get_persistent_store(ctx)
        scan_state = store.get("chest_scan_state")
        if not isinstance(scan_state, dict):
            scan_state = {}
        if scan_state.get("completed"):
            scan_state = {}
        if scan_state.get("origin"):
            origin = _normalize_pos(scan_state.get("origin")) or list(ctx.get_position()[:3])
            radius = int(scan_state.get("radius") or 30)
        else:
            origin = list(ctx.get_position()[:3])
            radius = 30
            scan_state = {
                "origin": origin,
                "radius": radius,
                "offset_index": 0,
                "dy_index": 0,
                "completed": False,
                "last_scan": int(time.time()),
            }
            store["chest_scan_state"] = scan_state
            _save_persistent(ctx)
        known = _gather_known_chests(store)
        scanned = _scan_for_extra_chests_resumable(ctx, origin, radius=radius, exclude=[], state=scan_state)
        all_chests = _dedupe_positions(known + scanned)
        store["known_chests"] = all_chests
        _save_persistent(ctx)

        visited = set()
        for pos in all_chests:
            key = _pos_key(pos)
            if key in visited:
                continue
            if "chest" not in block_id_at(ctx, pos[0], pos[1], pos[2]):
                continue
            if not _move_near_with_reset(ctx, pos, timeout=20.0):
                logger.info(f"WARNING: Could not reach chest at {pos}")
                continue
            if not do_open_container(ctx, (pos[0], pos[1], pos[2])):
                logger.info(f"WARNING: Could not open chest at {pos}")
                continue
            slots_info = _open_container_slots(ctx)
            if not slots_info:
                do_close_container(ctx)
                continue
            slots, chest_slots = slots_info
            _update_chest_index_from_open(ctx, pos, slots, chest_slots)
            do_close_container(ctx)
            visited.add(key)
            pair = _find_adjacent_chest(ctx, pos)
            if pair:
                visited.add(_pos_key(pair))

        logger.info(f"Chest index updated with {len(_get_chest_index(store))} entries")
        return True

    def t1105_furnace_torches(ctx: TestContext) -> bool:
        store = _get_persistent_store(ctx)
        if _is_near_night_early(get_world_time(ctx)):
            t1100_wait_out_night(ctx)

        if not t1104_index_chests(ctx):
            return False

        furnace_pos = _ensure_furnace_placed(ctx)
        if not furnace_pos:
            if not _ensure_furnace_item(ctx):
                return False
            furnace_pos = _ensure_furnace_placed(ctx)
        if not furnace_pos:
            return False

        required_charcoal = 16
        required_sticks = 16

        if not _ensure_charcoal_for_torches(ctx, furnace_pos, required_charcoal):
            return False
        if not _ensure_sticks_for_torches(ctx, required_sticks):
            return False

        if not robust_craft(ctx, "minecraft:torch", 64):
            logger.info("ERROR: Failed to craft torches")
            return False

        chests = _gather_known_chests(store)
        deposited = False
        for pos in chests:
            if _deposit_items_to_chest(ctx, pos, ["minecraft:torch"]):
                deposited = True
                break
        if not deposited:
            logger.info("ERROR: Failed to deposit torches into storage")
            return False

        return True

    def t1103_world_scan(ctx: TestContext) -> bool:
        store = _get_persistent_store(ctx)
        dim = _dimension_key(ctx)
        state_all = store.get("world_scan_state")
        if not isinstance(state_all, dict):
            state_all = {}
        state = state_all.get(dim)
        if not isinstance(state, dict):
            state = {"dimension": dim, "last_scan": int(time.time())}
            state_all[dim] = state
            store["world_scan_state"] = state_all
            _save_persistent(ctx)
        return _scan_world_cache(ctx, state)

    def _tower_ring_positions(ox: int, oz: int) -> List[Tuple[int, int]]:
        ring = []
        for x in range(ox + 1, ox + 6):
            ring.append((x, oz + 1))
        for z in range(oz + 2, oz + 6):
            ring.append((ox + 5, z))
        for x in range(ox + 4, ox, -1):
            ring.append((x, oz + 5))
        for z in range(oz + 4, oz + 1, -1):
            ring.append((ox + 1, z))
        return ring

    def _tower_wall_positions(ox: int, oy: int, oz: int, level_bottom: int) -> List[Tuple[int, int, int]]:
        positions = []
        for y in range(level_bottom, level_bottom + TOWER_LEVEL_HEIGHT):
            for x in range(ox, ox + TOWER_OUTER_SIZE):
                positions.append((x, y, oz))
                positions.append((x, y, oz + TOWER_OUTER_SIZE - 1))
            for z in range(oz + 1, oz + TOWER_OUTER_SIZE - 1):
                positions.append((ox, y, z))
                positions.append((ox + TOWER_OUTER_SIZE - 1, y, z))
        return positions

    def _tower_stair_steps(
        ox: int,
        oz: int,
        base_y: int,
        phase: int,
    ) -> List[Dict[str, int]]:
        ring = _tower_ring_positions(ox, oz)
        steps = []
        ring_len = len(ring)
        for i in range(TOWER_LEVEL_HEIGHT):
            idx = (phase + i) % ring_len
            next_idx = (phase + i + 1) % ring_len
            x, z = ring[idx]
            nx, nz = ring[next_idx]
            steps.append({
                "x": x,
                "y": base_y + 1 + i,
                "z": z,
                "dx": nx - x,
                "dz": nz - z,
            })
        return steps

    def _tower_missing_blocks(ctx: TestContext, positions: List[Tuple[int, int, int]], block_id: str) -> List[Tuple[int, int, int]]:
        missing = []
        for x, y, z in positions:
            bid = block_id_at(ctx, x, y, z)
            if bid != block_id:
                missing.append((x, y, z))
        return missing

    def _tower_detect_material(ctx: TestContext, wall_positions: List[Tuple[int, int, int]], stair_steps: List[Dict[str, int]]) -> Optional[str]:
        for x, y, z in wall_positions:
            bid = block_id_at(ctx, x, y, z)
            if bid and bid.endswith("_planks"):
                return bid
        for step in stair_steps:
            bid = block_id_at(ctx, step["x"], step["y"], step["z"])
            if bid and bid.endswith("_stairs"):
                return bid.replace("_stairs", "_planks")
        return None

    def _tower_get_supply_chests(store: dict) -> List[List[int]]:
        chests = []
        planks_entries = store.get("planks_chests", [])
        if isinstance(planks_entries, list):
            for entry in planks_entries:
                if isinstance(entry, dict):
                    pos_a = _normalize_pos(entry.get("chest_a"))
                    pos_b = _normalize_pos(entry.get("chest_b"))
                    if pos_a:
                        chests.append(pos_a)
                    if pos_b:
                        chests.append(pos_b)
        chests.extend(store.get("wood_chests", []))
        chests.extend(store.get("extra_log_chests", []))
        index = _get_chest_index(store)
        for entry in index.values():
            contents = entry.get("contents") or {}
            has_wood = any(item_id in contents for item_id in LOG_BLOCK_IDS)
            has_planks = any(item_id in contents for item_id in PLANK_ITEM_IDS)
            if has_wood or has_planks:
                pos = _normalize_pos(entry.get("pos"))
                if pos:
                    chests.append(pos)
        return _dedupe_positions(chests)

    def _tower_refresh_wood_chests(ctx: TestContext) -> List[List[int]]:
        store = _get_persistent_store(ctx)
        chests = _dedupe_positions(store.get("wood_chests", []))
        valid = []
        for pos in chests:
            if "chest" in block_id_at(ctx, pos[0], pos[1], pos[2]):
                valid.append(pos)
                continue
            below = block_id_at(ctx, pos[0], pos[1] - 1, pos[2])
            if not _is_placeable_target(block_id_at(ctx, pos[0], pos[1], pos[2])):
                logger.info(f"WARNING: Supply chest blocked at {pos}")
                continue
            if not _is_solid_support_block(below):
                logger.info(f"WARNING: No support for supply chest at {pos}")
                continue
            if not _ensure_chest_item(ctx):
                logger.info("ERROR: Unable to craft chest for supply recovery")
                continue
            if not _move_near_with_reset(ctx, pos, timeout=20.0):
                logger.info(f"WARNING: Could not reach supply chest position {pos}")
                continue
            if bot_place_block(ctx, pos[0], pos[1], pos[2], "minecraft:chest"):
                valid.append(pos)
        if not valid:
            if _setup_initial_storage(ctx):
                valid = _dedupe_positions(store.get("wood_chests", []))
        store["wood_chests"] = valid
        _save_persistent(ctx)
        return valid

    def _tower_scan_chest_counts(
        ctx: TestContext,
        chests: List[List[int]],
        item_ids: List[str],
    ) -> Dict[str, int]:
        counts = {item_id: 0 for item_id in item_ids}
        store = _get_persistent_store(ctx)
        ordered = _merge_chests_with_index(store, chests, item_ids)
        for pos in ordered:
            if not _move_near_with_reset(ctx, pos, timeout=20.0):
                continue
            if not do_open_container(ctx, (pos[0], pos[1], pos[2])):
                continue
            slots_info = _open_container_slots(ctx)
            if not slots_info:
                do_close_container(ctx)
                continue
            slots, chest_slots = slots_info
            for item in slots:
                slot_idx = item.get("slot", -1)
                if slot_idx < 0 or slot_idx >= chest_slots:
                    continue
                item_id = item.get("id", "")
                if item_id in counts:
                    counts[item_id] += item.get("count", 0)
            _update_chest_index_from_open(ctx, pos, slots, chest_slots)
            do_close_container(ctx)
        return counts

    def _tower_withdraw_items(
        ctx: TestContext,
        chests: List[List[int]],
        item_id: str,
        count: int,
    ) -> int:
        taken = 0
        remaining = count
        store = _get_persistent_store(ctx)
        ordered = _merge_chests_with_index(store, chests, [item_id])
        for pos in ordered:
            if remaining <= 0:
                break
            if not _move_near_with_reset(ctx, pos, timeout=20.0):
                continue
            if not do_open_container(ctx, (pos[0], pos[1], pos[2])):
                continue
            slots_info = _open_container_slots(ctx)
            if not slots_info:
                do_close_container(ctx)
                continue
            slots, chest_slots = slots_info
            for item in slots:
                if remaining <= 0:
                    break
                slot_idx = item.get("slot", -1)
                if slot_idx < 0 or slot_idx >= chest_slots:
                    continue
                if item.get("id") != item_id:
                    continue
                stack_count = item.get("count", 0)
                if stack_count <= 0:
                    continue
                safe_inventory_click(ctx, slot_idx, "QUICK_MOVE")
                time.sleep(0.1)
                taken += stack_count
                remaining -= stack_count
            screen_after = ctx.client.transport.dispatch("get_screen", {})
            screen_data_after = screen_after.get("data", screen_after)
            slots_after = screen_data_after.get("slots", [])
            _update_chest_index_from_open(ctx, pos, slots_after, chest_slots)
            do_close_container(ctx)
        return taken

    def _tower_choose_plank_type(
        ctx: TestContext,
        tower: dict,
        level_bottom: int,
        stair_phase: int,
        supply_chests: List[List[int]],
    ) -> str:
        ox, oy, oz = tower["base_origin"]
        wall_positions = _tower_wall_positions(ox, oy, oz, level_bottom)
        stair_steps = _tower_stair_steps(ox, oz, tower["current_top_y"], stair_phase)
        detected = _tower_detect_material(ctx, wall_positions, stair_steps)
        if detected:
            return detected
        inv_counts = get_inventory_counts(ctx)
        plank_counts = {item_id: inv_counts.get(item_id, 0) for item_id in PLANK_ITEM_IDS}
        chest_planks = _tower_scan_chest_counts(ctx, supply_chests, PLANK_ITEM_IDS)
        for item_id, count in chest_planks.items():
            plank_counts[item_id] = plank_counts.get(item_id, 0) + count
        best_plank = max(plank_counts.items(), key=lambda item: item[1])[0] if plank_counts else "minecraft:oak_planks"
        if plank_counts.get(best_plank, 0) > 0:
            return best_plank
        log_counts = {item_id: inv_counts.get(item_id, 0) for item_id in LOG_BLOCK_IDS}
        chest_logs = _tower_scan_chest_counts(ctx, supply_chests, LOG_BLOCK_IDS)
        for item_id, count in chest_logs.items():
            log_counts[item_id] = log_counts.get(item_id, 0) + count
        if log_counts:
            best_log = max(log_counts.items(), key=lambda item: item[1])[0]
            return best_log.replace("_log", "_planks")
        return "minecraft:oak_planks"

    def _tower_stair_item(plank_id: str) -> str:
        return plank_id.replace("_planks", "_stairs")

    def _tower_slab_item(plank_id: str) -> str:
        return plank_id.replace("_planks", "_slab")

    def _tower_log_item(plank_id: str) -> str:
        return plank_id.replace("_planks", "_log")

    def _tower_place_stair(
        ctx: TestContext,
        x: int,
        y: int,
        z: int,
        stair_item: str,
        dx: int,
        dz: int,
    ) -> bool:
        stand_x = x - dx
        stand_z = z - dz
        move_near(ctx, stand_x, y, stand_z, timeout=10.0)
        return place_block_at(ctx, x, y, z, stair_item)

    def _tower_measure_completed(ctx: TestContext, tower: dict) -> Tuple[int, int]:
        ox, oy, oz = tower["base_origin"]
        roof_y = oy + 3
        current_top = roof_y
        height_built = 0
        phase = 0
        while current_top + TOWER_LEVEL_HEIGHT <= TOWER_MAX_Y:
            level_bottom = current_top + 1
            walls = _tower_wall_positions(ox, oy, oz, level_bottom)
            stair_steps = _tower_stair_steps(ox, oz, current_top, phase)
            walls_complete = all(block_id_at(ctx, x, y, z).endswith("_planks") for x, y, z in walls)
            stairs_complete = all(block_id_at(ctx, step["x"], step["y"], step["z"]).endswith("_stairs") for step in stair_steps)
            if not (walls_complete and stairs_complete):
                break
            height_built += TOWER_LEVEL_HEIGHT
            current_top += TOWER_LEVEL_HEIGHT
            phase = (phase + TOWER_LEVEL_HEIGHT) % len(_tower_ring_positions(ox, oz))
        return height_built, current_top

    def _tower_init_state(ctx: TestContext) -> Optional[dict]:
        store = _get_persistent_store(ctx)
        house = store.get("house_7x7")
        if not house or "origin" not in house:
            logger.info("ERROR: House origin missing; cannot build tower")
            return None
        ox, oy, oz = house["origin"]
        tower = store.get("tower")
        if not isinstance(tower, dict):
            tower = {}
        tower["base_origin"] = [ox, oy, oz]
        tower.setdefault("level_height", TOWER_LEVEL_HEIGHT)
        tower.setdefault("outer_size", TOWER_OUTER_SIZE)
        tower.setdefault("inner_size", TOWER_INNER_SIZE)
        height_built, current_top = _tower_measure_completed(ctx, tower)
        tower["height_built"] = height_built
        tower["current_top_y"] = current_top
        tower["stair_direction"] = "clockwise"
        tower["stair_phase"] = height_built % len(_tower_ring_positions(ox, oz))
        store["tower"] = tower
        _save_persistent(ctx)
        return tower

    def _tower_mine_logs(ctx: TestContext, target_gain: int) -> bool:
        if target_gain <= 0:
            return True
        logger.info(f"Mining {target_gain} logs for tower materials...")
        initial_logs = count_all_logs(ctx)
        target_total = initial_logs + target_gain
        ctx.client.transport.dispatch("chat", {"message": f"#mine {LOG_MINE_ARG}"})
        start = time.time()
        timeout = 180.0
        while time.time() - start < timeout:
            if is_dead(ctx):
                logger.warn("Died during mining! Attempting recovery...")
                if not _check_and_recover_death(ctx):
                    return False
                ctx.client.transport.dispatch("chat", {"message": f"#mine {LOG_MINE_ARG}"})
                time.sleep(2.0)
                continue
            if _is_near_night_early(get_world_time(ctx)):
                logger.info("Approaching night; stopping mining to head home.")
                break
            current = count_all_logs(ctx)
            if current >= target_total:
                break
            time.sleep(2.0)
        ctx.client.transport.dispatch("chat", {"message": "#stop"})
        time.sleep(1.0)
        cancel_pathing(ctx)
        _pickup_nearby_items(ctx)
        final_logs = count_all_logs(ctx)
        logger.info(f"Mining completed: gained {final_logs - initial_logs} logs")
        return final_logs >= target_total

    def _tower_prepare_materials(
        ctx: TestContext,
        plank_type: str,
        supply_chests: List[List[int]],
        total_planks_needed: int,
    ) -> bool:
        have_planks = safe_count_item(ctx, plank_type)
        if have_planks < total_planks_needed:
            have_planks += _tower_withdraw_items(ctx, supply_chests, plank_type, total_planks_needed - have_planks)
        if have_planks >= total_planks_needed:
            return True
        log_item = _tower_log_item(plank_type)
        need_planks = total_planks_needed - have_planks
        need_logs = int(math.ceil(need_planks / 4.0))
        have_logs = safe_count_item(ctx, log_item)
        if have_logs < need_logs:
            have_logs += _tower_withdraw_items(ctx, supply_chests, log_item, need_logs - have_logs)
        if have_logs < need_logs:
            if not _tower_mine_logs(ctx, need_logs - have_logs):
                return False
        have_logs = safe_count_item(ctx, log_item)
        while safe_count_item(ctx, plank_type) < total_planks_needed and have_logs > 0:
            current_planks = safe_count_item(ctx, plank_type)
            desired_planks = current_planks + 4
            if not robust_craft(ctx, plank_type, desired_planks):
                logger.info(f"ERROR: Failed to craft planks ({plank_type})")
                return False
            have_logs = safe_count_item(ctx, log_item)
        return safe_count_item(ctx, plank_type) >= total_planks_needed

    def _tower_ensure_roof_access(ctx: TestContext, tower: dict, plank_type: str) -> bool:
        ox, oy, oz = tower["base_origin"]
        roof_y = oy + 3
        center = {"x": ox + 3, "y": roof_y, "z": oz + 3}
        if do_goto(ctx, center, timeout=12.0):
            return True
        if tower.get("access_built"):
            return False
        stair_item = _tower_stair_item(plank_type)
        if safe_count_item(ctx, stair_item) < 3:
            if not robust_craft(ctx, stair_item, 3):
                logger.info("ERROR: Failed to craft stairs for roof access")
                return False
        base_x = ox + 3
        start_z = oz + 7
        for i in range(3):
            if not _tower_place_stair(ctx, base_x, oy + i, start_z + i, stair_item, 0, -1):
                logger.info("ERROR: Failed to place roof access stair")
                return False
        if do_goto(ctx, center, timeout=12.0):
            tower["access_built"] = True
            _save_persistent(ctx)
            return True
        return False

    def t1102_build_tower(ctx: TestContext) -> bool:
        if not _ensure_house_matches_plan(ctx):
            return False
        _tower_refresh_wood_chests(ctx)
        tower = _tower_init_state(ctx)
        if not tower:
            return False
        if tower["current_top_y"] + TOWER_LEVEL_HEIGHT > TOWER_MAX_Y:
            tower["max_height_reached"] = True
            _save_persistent(ctx)
            logger.info("Tower max height reached; stopping cleanly.")
            return True
        ox, oy, oz = tower["base_origin"]
        level_bottom = tower["current_top_y"] + 1
        supply_chests = _tower_get_supply_chests(_get_persistent_store(ctx))
        plank_type = _tower_choose_plank_type(ctx, tower, level_bottom, tower["stair_phase"], supply_chests)

        access_planks = 6 if not tower.get("access_built") else 0
        total_planks_needed = TOWER_PLANKS_PER_LEVEL + 6 + 2 + access_planks
        if not _tower_prepare_materials(ctx, plank_type, supply_chests, total_planks_needed):
            plank_type = _tower_choose_plank_type(ctx, tower, level_bottom, tower["stair_phase"], supply_chests)
            if not _tower_prepare_materials(ctx, plank_type, supply_chests, total_planks_needed):
                return False

        if not _tower_ensure_roof_access(ctx, tower, plank_type):
            logger.info("ERROR: Could not reach roof to start tower build")
            return False

        wall_positions = _tower_wall_positions(ox, oy, oz, level_bottom)
        missing_walls = _tower_missing_blocks(ctx, wall_positions, plank_type)
        if missing_walls:
            for x, y, z in missing_walls:
                if not place_block_at(ctx, x, y, z, plank_type):
                    logger.info("ERROR: Failed to place tower wall block")
                    return False

        stair_item = _tower_stair_item(plank_type)
        stair_steps = _tower_stair_steps(ox, oz, tower["current_top_y"], tower["stair_phase"])
        have_stairs = safe_count_item(ctx, stair_item)
        if have_stairs < TOWER_STAIRS_PER_LEVEL:
            if not robust_craft(ctx, stair_item, TOWER_STAIRS_PER_LEVEL):
                logger.info("ERROR: Failed to craft stairs for tower")
                return False
        for step in stair_steps:
            bid = block_id_at(ctx, step["x"], step["y"], step["z"])
            if bid != stair_item:
                if not _tower_place_stair(ctx, step["x"], step["y"], step["z"], stair_item, step["dx"], step["dz"]):
                    logger.info("ERROR: Failed to place tower stair block")
                    return False

        top_target = {"x": stair_steps[-1]["x"], "y": tower["current_top_y"] + TOWER_LEVEL_HEIGHT, "z": stair_steps[-1]["z"]}
        if not do_goto(ctx, {"x": ox + 3, "y": tower["current_top_y"], "z": oz + 3}, timeout=12.0):
            logger.info("ERROR: Could not reach previous tower top")
            return False
        if not do_goto(ctx, top_target, timeout=15.0):
            logger.info("ERROR: Could not climb to new tower top")
            return False

        if _tower_missing_blocks(ctx, wall_positions, plank_type):
            logger.info("ERROR: Tower wall validation failed")
            return False
        for step in stair_steps:
            bid = block_id_at(ctx, step["x"], step["y"], step["z"])
            if bid != stair_item:
                logger.info("ERROR: Tower stair validation failed")
                return False

        tower["height_built"] = tower["height_built"] + TOWER_LEVEL_HEIGHT
        tower["current_top_y"] = tower["current_top_y"] + TOWER_LEVEL_HEIGHT
        tower["stair_phase"] = (tower["stair_phase"] + TOWER_LEVEL_HEIGHT) % len(_tower_ring_positions(ox, oz))
        tower["last_plank_type"] = plank_type
        _save_persistent(ctx)
        return True

    # Spacing requirements per sapling type (for T1106)
    SAPLING_SPACING = {
        "minecraft:oak_sapling": 2,
        "minecraft:birch_sapling": 2,
        "minecraft:acacia_sapling": 3,
        "minecraft:spruce_sapling": 5,
        "minecraft:dark_oak_sapling": 5,
        "minecraft:jungle_sapling": 7,
        "minecraft:cherry_sapling": 2,
        "minecraft:mangrove_propagule": 3,
    }

    def _is_valid_torch_ground(ctx: TestContext, x: int, y: int, z: int) -> bool:
        """Check if position is valid for torch placement."""
        bid = block_id_at(ctx, x, y, z)
        if not bid:
            return False
        # Skip if already has torch
        if "torch" in bid:
            return False
        # Must be air to place torch
        if "air" not in bid:
            return False
        # Check block below is solid
        below = block_id_at(ctx, x, y - 1, z)
        if not below:
            return False
        if "air" in below or "water" in below or "lava" in below:
            return False
        return True

    def _place_torches_around_sapling(ctx: TestContext, sapling_pos: Tuple[int, int, int]) -> int:
        """Place up to 4 torches in cardinal directions around a sapling. Returns count placed."""
        x, y, z = sapling_pos
        placed = 0
        # Cardinal directions: N, S, E, W
        for dx, dz in [(0, -1), (0, 1), (1, 0), (-1, 0)]:
            tx, tz = x + dx, z + dz
            if _is_valid_torch_ground(ctx, tx, y, tz):
                if safe_count_item(ctx, "minecraft:torch") > 0:
                    if place_block_at(ctx, tx, y, tz, "minecraft:torch", allow_break=False):
                        placed += 1
        return placed

    def _withdraw_saplings_from_chests(ctx: TestContext, min_saplings: int) -> int:
        """Withdraw saplings from chests until we have enough."""
        store = _get_persistent_store(ctx)
        chests = _gather_known_chests(store)
        total_withdrawn = 0
        
        for sapling_id in SAPLING_ITEM_IDS:
            current = safe_count_item(ctx, sapling_id)
            if current >= min_saplings:
                return total_withdrawn
            need = min_saplings - current
            withdrawn = _tower_withdraw_items(ctx, chests, sapling_id, need)
            total_withdrawn += withdrawn
            if safe_count_item(ctx, sapling_id) >= min_saplings:
                return total_withdrawn
        
        return total_withdrawn

    def _withdraw_torches_from_chests(ctx: TestContext, min_torches: int) -> int:
        """Withdraw torches from chests until we have enough."""
        store = _get_persistent_store(ctx)
        chests = _gather_known_chests(store)
        current = safe_count_item(ctx, "minecraft:torch")
        if current >= min_torches:
            return 0
        need = min_torches - current
        return _tower_withdraw_items(ctx, chests, "minecraft:torch", need)

    def _count_total_saplings(ctx: TestContext) -> int:
        """Count total saplings in inventory."""
        total = 0
        for sapling_id in SAPLING_ITEM_IDS:
            total += safe_count_item(ctx, sapling_id)
        return total

    def t1106_replant_with_torches(ctx: TestContext) -> bool:
        """Plant saplings with torches around each one."""
        store = _get_persistent_store(ctx)
        stats = store.setdefault("stats", {})
        
        # Skip if near night
        if _is_near_night_early(get_world_time(ctx)):
            logger.info("Near night; skipping sapling planting, heading to shelter.")
            t1100_wait_out_night(ctx)
            return True
        
        # Get planting center and avoid box
        house = store.get("house_7x7", {})
        origin = house.get("origin")
        if origin:
            ox, oy, oz = origin
            center = (ox + 3, oy + 1, oz + 3)
            avoid_box = (ox - 20, ox + 26, oz - 20, oz + 26)
        else:
            px, py, pz = ctx.get_position()
            center = (int(px), int(py), int(pz))
            avoid_box = None
        
        max_saplings = 10
        saplings_planted = 0
        torches_placed = 0
        
        # Check if we have saplings, withdraw from chests if needed
        if _count_total_saplings(ctx) < max_saplings:
            _withdraw_saplings_from_chests(ctx, max_saplings)
        
        total_saplings = _count_total_saplings(ctx)
        if total_saplings <= 0:
            logger.info("No saplings available to plant")
            return True  # Not a failure, just nothing to do
        
        # Check if we have torches, withdraw from chests if needed
        torches_needed = max_saplings * 4  # 4 torches per sapling
        if safe_count_item(ctx, "minecraft:torch") < torches_needed:
            _withdraw_torches_from_chests(ctx, torches_needed)
        
        # Get inventory saplings
        inv = get_inventory_counts(ctx)
        saplings = {item_id: count for item_id, count in inv.items() 
                   if item_id in SAPLING_ITEM_IDS and count > 0}
        
        # Plant each sapling type with its required spacing
        for sapling_item, count in list(saplings.items()):
            if count <= 0 or saplings_planted >= max_saplings:
                continue
            
            spacing = SAPLING_SPACING.get(sapling_item, 2)
            to_plant = min(count, max_saplings - saplings_planted)
            
            spots = _find_sapling_spots(
                ctx,
                center,
                radius=40,
                max_positions=to_plant,
                min_clearance=6,
                min_light_clearance=10,
                grid_spacing=spacing,
                avoid_box=avoid_box,
            )
            
            for pos in spots:
                if saplings_planted >= max_saplings:
                    break
                
                # Check for night
                if _is_near_night_early(get_world_time(ctx)):
                    logger.info("Approaching night; stopping planting to head home.")
                    break
                
                x, y, z = pos
                
                # Plant the sapling
                if place_block_at(ctx, x, y, z, sapling_item, allow_break=False):
                    saplings_planted += 1
                    saplings[sapling_item] -= 1
                    
                    # Place torches around it
                    torches = _place_torches_around_sapling(ctx, (x, y, z))
                    torches_placed += torches
        
        # Update stats
        stats["total_saplings_planted"] = stats.get("total_saplings_planted", 0) + saplings_planted
        stats["total_torches_placed"] = stats.get("total_torches_placed", 0) + torches_placed
        _save_persistent(ctx)
        
        if saplings_planted > 0:
            logger.info(f"Planted {saplings_planted} saplings with {torches_placed} torches")
            logger.info(f"Total: {stats.get('total_saplings_planted', 0)} saplings, {stats.get('total_torches_placed', 0)} torches")
        else:
            logger.info("No suitable spots found for sapling planting")
        
        return True

    def t1100_verify_progress(ctx: TestContext) -> bool:
        """Verify run was successful."""
        store = _get_persistent_store(ctx)
        stats = store.get("stats", {})
        
        logger.info(f"Run complete. Total progress: {stats.get('total_logs', 0)} logs over {stats.get('total_runs', 0)} runs")
        
        return True

    # Build test case
    suite.add(TestCase(
        id="T1100",
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
        timeout_seconds=1800
    ))

    suite.add(TestCase(
        id="T1101",
        name="Planks Double Chest Storage",
        description="Convert logs to planks and store in labeled double chest",
        setup=t1100_setup,
        steps=[
            t1100_planks_storage,
        ],
        timeout_seconds=900
    ))

    suite.add(TestCase(
        id="T1102",
        name="Build Tower Level",
        description="Extend tower by one 4-block level and climb the new stairs",
        setup=t1100_setup,
        steps=[
            t1102_build_tower,
        ],
        timeout_seconds=1800
    ))

    suite.add(TestCase(
        id="T1103",
        name="World Cache Scan",
        description="Spiral scan blocks into SQLite world cache",
        setup=t1100_setup,
        steps=[
            t1103_world_scan,
        ],
        timeout_seconds=1800
    ))

    suite.add(TestCase(
        id="T1104",
        name="Chest Index Refresh",
        description="Scan local chests and update metadata in persistent storage",
        setup=t1100_setup,
        steps=[
            t1104_index_chests,
        ],
        timeout_seconds=900
    ))

    suite.add(TestCase(
        id="T1105",
        name="Furnace + Torches",
        description="Craft furnace and smelt logs into charcoal for torches",
        setup=t1100_setup,
        steps=[
            t1105_furnace_torches,
            t1100_wait_out_night,
        ],
        timeout_seconds=1800
    ))

    suite.add(TestCase(
        id="T1106",
        name="Sapling Replanting with Torches",
        description="Plant 10 saplings with 4 torches around each for light and growth",
        setup=t1100_setup,
        steps=[
            t1106_replant_with_torches,
            t1100_wait_out_night,
        ],
        timeout_seconds=900
    ))
    
    return suite
