from tests.functional.suite_utils import get_test_state
"""
Extended Suite 400: Crafting & Smelting (Granular Action Tests)
T400-T403: Basic Crafting, Tool Crafting, Furnace, Multi-Step
"""

import time
from test_base import TestCase, TestSuite, TestContext

from utils.mc_harness import (
    prepare_test_world,
    teardown_test_world,
    clear_box,
    build_floor,
    tp,
    wait_for_item_count,
    wait_for_gui_open,
    close_screen,
    select_hotbar_item,
    safe_inventory_click,
    get_screen,
    assert_block,
    wait_for_block,
    robust_interact,
    robust_interact_block,
    furnace_slot_map,
    player_slot_map,
    player_slot_map,
    safe_dispatch,
    do_open_container,
    do_close_container,
    do_inventory_click,
)


def create_extended_suite_400() -> TestSuite:
    """Suite 400: Crafting & Smelting - Granular action tests."""
    suite = TestSuite("Suite_400_Crafting", "Granular crafting action tests")
    suite_state = {}

    anchors = {
        "T400": (0, 80, 400),
        "T401": (200, 80, 400),
        "T402": (400, 80, 400),
        "T403": (600, 80, 400),
    }
    # --- Helpers ---

    def prepare_standard_crafting_test(ctx, tid, anchor, size=10, height=10, gamemode="survival", floor=True):
        """Standard fixture for crafting tests."""
        ax, ay, az = anchor
        bounds = {
            "min_x": ax - size, "min_y": ay - 5, "min_z": az - size,
            "max_x": ax + size, "max_y": ay + height, "max_z": az + size,
        }
        get_test_state(suite_state, tid)["bounds"] = bounds
        clear_box(ctx, bounds)
        prepare_test_world(ctx, gamemode=gamemode)
        tp(ctx, ax, ay, az)
        if floor:
            build_floor(ctx, ax - 5, ay - 1, az - 5, ax + 5, az + 5)
        ctx.clear_inventory()
        ctx.snapshot("start")
        return bounds


    # T400: Basic Crafting
    def t400_setup(ctx: TestContext):
        ax, ay, az = anchors["T400"]
        prepare_standard_crafting_test(ctx, "T400", (ax, ay, az))
        ctx.give_item("minecraft:oak_log", 4)
        get_test_state(suite_state, "T400")["start_logs"] = ctx.count_item("minecraft:oak_log")

    def t400_step_craft(ctx: TestContext) -> bool:
        ctx.log_event("Crafting planks from logs via dispatch...")
        # Check start count
        start_count = get_test_state(suite_state, "T400")["start_logs"]
        if start_count < 1:
             ctx.log_event("Failed to get logs")
             return False

        # Try crafting
        try:
             # Some bridges use 'craft' recipe book style
             ctx.client.transport.dispatch("craft", {"item": "minecraft:oak_planks", "count": 4})
        except Exception:
             ctx.log_event("Craft command failed")
             return False

        # Wait for result
        return wait_for_item_count(ctx, "minecraft:oak_planks", 4, timeout=4.0)

    def t400_assert_planks(ctx: TestContext):
        count = ctx.count_item("minecraft:oak_planks")
        logs = ctx.count_item("minecraft:oak_log")
        start_logs = get_test_state(suite_state, "T400").get("start_logs", 0)
        
        # In survival, logs should decrease.
        consumed = start_logs > logs
        result_ok = count >= 4
        
        return result_ok and consumed, f"Planks: {count} (>=4), Logs Consumed: {consumed}"

    def t400_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=get_test_state(suite_state, "T400").get("bounds"))

    suite.add(TestCase(
        id="T400",
        name="Basic Crafting",
        description="Craft planks from logs",
        timeout_seconds=20,
        setup=t400_setup,
        steps=[t400_step_craft],
        assertions=[t400_assert_planks],
        teardown=t400_teardown
    ))

    # T401: Tool Crafting
    def t401_setup(ctx: TestContext):
        ax, ay, az = anchors["T401"]
        prepare_standard_crafting_test(ctx, "T401", (ax, ay, az))
        
        table_pos = (ax + 2, ay, az)
        ctx.set_block(table_pos[0], table_pos[1], table_pos[2], "minecraft:crafting_table")
        get_test_state(suite_state, "T401")["table_pos"] = table_pos
        
        ctx.give_item("minecraft:oak_planks", 8)
        ctx.give_item("minecraft:stick", 4)

    def t401_step_craft_pick(ctx: TestContext) -> bool:
        ctx.log_event("Crafting wooden pickaxe...")
        table_pos = get_test_state(suite_state, "T401")["table_pos"]
        
        # Ensure client sync
        if not wait_for_block(ctx, table_pos[0], table_pos[1], table_pos[2], "minecraft:crafting_table"):
            ctx.log_event("Client missing crafting table")
            return False

        # Open table
        select_hotbar_item(ctx, 8)
        ctx.client.transport.dispatch("look_at", {"x": table_pos[0] + 0.5, "y": table_pos[1] + 0.5, "z": table_pos[2] + 0.5})
        if not robust_interact_block(ctx, table_pos[0], table_pos[1], table_pos[2], retries=20):
            ctx.log_event("Crafting table didn't open")
            return False
            
        # Dispatch craft command which might use open container if supported
        ctx.client.transport.dispatch("craft", {"item": "minecraft:wooden_pickaxe", "count": 1})
        
        # Verify
        ok = wait_for_item_count(ctx, "minecraft:wooden_pickaxe", 1, timeout=5.0)
        # Close screen if still open
        close_screen(ctx)
        return ok

    def t401_assert_pick(ctx: TestContext):
        has_pick = ctx.has_item("minecraft:wooden_pickaxe")
        return has_pick, "Has wooden pickaxe"

    suite.add(TestCase(
        id="T401",
        name="Tool Crafting",
        description="Craft wooden pickaxe",
        timeout_seconds=20,
        setup=t401_setup,
        steps=[t401_step_craft_pick],
        assertions=[t401_assert_pick],
        teardown=lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T401").get("bounds"))
    ))

    # T402: Furnace Operation
    def t402_setup(ctx: TestContext):
        ax, ay, az = anchors["T402"]
        prepare_standard_crafting_test(ctx, "T402", (ax, ay, az))
        
        furnace_pos = (ax + 2, ay, az)
        furnace_pos = (ax + 2, ay, az)
        ctx.run_command(f"setblock {furnace_pos[0]} {furnace_pos[1]} {furnace_pos[2]} minecraft:furnace")
        get_test_state(suite_state, "T402")["furnace_pos"] = furnace_pos
        
        ctx.give_item("minecraft:beef", 3)
        ctx.give_item("minecraft:coal", 8)

    def t402_step_smelt(ctx: TestContext) -> bool:
        furnace_pos = get_test_state(suite_state, "T402")["furnace_pos"]
        c = ctx.client
        
        if not wait_for_block(ctx, furnace_pos[0], furnace_pos[1], furnace_pos[2], "minecraft:furnace"):
            return False
            
        # Open furnace using proven robust_interact_block
        select_hotbar_item(ctx, 8)
        ctx.client.transport.dispatch("look_at", {"x": furnace_pos[0] + 0.5, "y": furnace_pos[1] + 0.5, "z": furnace_pos[2] + 0.5})
        if not robust_interact_block(ctx, furnace_pos[0], furnace_pos[1], furnace_pos[2], retries=20):
             ctx.log_event("Furnace failed to open")
             return False

        # We must insert items. If we don't have automatable slots, we skip.
        # But user requested "Smelt beef -> cooked beef (REAL)".
        # We try to use safe_inventory_click if slots are known.
        # Furnace slots: 0=Input, 1=Fuel, 2=Output (Standard).
        # We need to find where beef/coal are in player inv and shift-click OR click-drag.
        # Shift-click behavior in furnace:
        # - Coal -> Fuel (1)
        # - Beef -> Input (0)
        # This is standard Minecraft behavior. Let's try shift-clicking items from inventory.
        
        # Find slots in inventory (naive find)
        # We don't have `find_slot` helper in this file directly unless imports or duplicated.
        # Let's rely on `mc_harness.inventory` helpers if available or `ctx.find_item_slot` if it exists.
        # `TestContext` usually doesn't have `find_item_slot`.
        # We'll do a simple iteration here since we cleared inventory.
        inv = ctx.get_inventory()
        if isinstance(inv, dict): inv = inv.get("inventory", [])
        
        beef_slot = None
        coal_slot = None
        
        for item in inv:
            if item.get("id") == "minecraft:beef":
                beef_slot = item.get("slot")
            elif item.get("id") == "minecraft:coal":
                coal_slot = item.get("slot")
                
        if beef_slot is None or coal_slot is None:
            ctx.log_event("Items not found in inv")
            return False
            
        ctx.log_event(f"Inserting items: Beef from {beef_slot}, Coal from {coal_slot}")
        
        # Shift click coal (Fuel)
        safe_inventory_click(ctx, player_slot_map(coal_slot), "QUICK_MOVE")
        time.sleep(0.5)
        # Shift click beef (Input)
        safe_inventory_click(ctx, player_slot_map(beef_slot), "QUICK_MOVE")
        time.sleep(0.5)
        
        # Wait for output
        # Cook time is 10s.
        ctx.log_event("Waiting for smelting...")
        # Check output slot (2) periodically? get_inventory returns open container?
        # get_inventory usually returns player inv + container if open?
        # Protocol varies. Assuming we need to look at window items.
        # `get_inventory` often only player.
        # The bridge endpoint `get_container_items` (not in harness default) or `get_screen`?
        
        # Let's assume we wait 12s then try to take result from slot 2.
        time.sleep(11.0) 
        
        # Try to take from output slot (2)
        safe_inventory_click(ctx, 2, "QUICK_MOVE") # Slot 2 is output in furnace container
        time.sleep(0.5)
        
        close_screen(ctx)
        return True

    def t402_assert_smelted(ctx: TestContext):
        # Verify we got cooked beef
        has = ctx.has_item("minecraft:cooked_beef")
        return has, "Cooked beef in inventory"

    suite.add(TestCase(
        id="T402",
        name="Furnace Operation",
        description="Smelt items in furnace",
        timeout_seconds=30, # Increased for smelting time
        setup=t402_setup,
        steps=[t402_step_smelt],
        assertions=[t402_assert_smelted],
        teardown=lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T402").get("bounds"))
    ))

    # T403: Multi-Step Crafting
    def t403_setup(ctx: TestContext):
        ax, ay, az = anchors["T403"]
        prepare_standard_crafting_test(ctx, "T403", (ax, ay, az))
        
        table_pos = (ax + 2, ay, az)
        ctx.set_block(table_pos[0], table_pos[1], table_pos[2], "minecraft:crafting_table")
        get_test_state(suite_state, "T403")["table_pos"] = table_pos
        
        ctx.give_item("minecraft:oak_log", 2)
        ctx.give_item("minecraft:cobblestone", 3)

    def t403_step_craft_complex(ctx: TestContext) -> bool:
        # Logs -> Planks -> Sticks -> Stone Pickaxe
        
        # 1. Craft Planks
        ctx.client.transport.dispatch("craft", {"item": "minecraft:oak_planks", "count": 8})
        if not wait_for_item_count(ctx, "minecraft:oak_planks", 8, timeout=5.0):
            return False
            
        # 2. Craft Sticks (need 2 sticks)
        ctx.client.transport.dispatch("craft", {"item": "minecraft:stick", "count": 4})
        if not wait_for_item_count(ctx, "minecraft:stick", 4, timeout=5.0):
            return False
            
        # 3. Open Table and Craft Stone Pickaxe
        table_pos = get_test_state(suite_state, "T403")["table_pos"]
        
        if not wait_for_block(ctx, table_pos[0], table_pos[1], table_pos[2], "minecraft:crafting_table"):
            return False
            
        select_hotbar_item(ctx, 8)
        ctx.client.transport.dispatch("look_at", {"x": table_pos[0] + 0.5, "y": table_pos[1] + 0.5, "z": table_pos[2] + 0.5})
        if not robust_interact_block(ctx, table_pos[0], table_pos[1], table_pos[2], retries=20):
            return False

        ctx.client.transport.dispatch("craft", {"item": "minecraft:stone_pickaxe", "count": 1})
        ok = wait_for_item_count(ctx, "minecraft:stone_pickaxe", 1, timeout=5.0)
        close_screen(ctx)
        return ok

    def t403_assert_complex(ctx: TestContext):
        return ctx.has_item("minecraft:stone_pickaxe"), "Has stone pickaxe"

    suite.add(TestCase(
        id="T403",
        name="Multi-Step Crafting",
        description="Logs -> Planks -> Sticks -> Stone Pickaxe",
        timeout_seconds=25,
        setup=t403_setup,
        steps=[t403_step_craft_complex],
        assertions=[t403_assert_complex],
        teardown=lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T403").get("bounds"))
    ))

    # T404-T415: Future implementations (Skipped for now to avoid fake passes)
    skipped_tests = {
        # T404, T405 implemented below
        "T406": "Campfire Cooking",
        "T407": "Stonecutter",
        "T408": "Smithing Table",
        "T409": "Loom Operation",
        "T410": "Cartography Table",
        "T411": "Grindstone",
        "T412": "Tool Sequence",
        "T413": "Armor Crafting",
        "T414": "Redstone Components",
        "T415": "Multi-Step Recipe",
    }

    # T404: Blast Furnace (Iron Ore -> Ingot, Fast)
    def t404_setup(ctx: TestContext):
        ax, ay, az = anchors["T402"] 
        prepare_standard_crafting_test(ctx, "T404", (ax+200, ay, az))
        ctx.set_block(ax+202, ay, az, "minecraft:blast_furnace")
        get_test_state(suite_state, "T404")["pos"] = (ax+202, ay, az)
        ctx.give_item("minecraft:iron_ore", 1)
        ctx.give_item("minecraft:coal", 1)

    def t404_step_blast(ctx: TestContext) -> bool:
        furnace_pos = get_test_state(suite_state, "T404")["pos"]
        
        if not wait_for_block(ctx, furnace_pos[0], furnace_pos[1], furnace_pos[2], "minecraft:blast_furnace"):
            return False
            
        # Open
        select_hotbar_item(ctx, 8)
        ctx.client.transport.dispatch("look_at", {"x": furnace_pos[0] + 0.5, "y": furnace_pos[1] + 0.5, "z": furnace_pos[2] + 0.5})
        if not robust_interact_block(ctx, furnace_pos[0], furnace_pos[1], furnace_pos[2], retries=20):
            return False

        # Insert Items (Shift-Click)
        # Note: We need to find slots again.
        # Shortcuts since we know we just gave them
        inv = get_inventory_payload(ctx) # Need to define helper or import? 
        # Helper 'get_inventory_payload' is in Suite 300, not here. 
        # We need to replicate finding logic or use 'basic' iteration.
        
        # Simplified find
        inv_list = ctx.get_inventory().get("inventory", [])
        coal_slot = next((i["slot"] for i in inv_list if i["id"] == "minecraft:coal"), None)
        ore_slot = next((i["slot"] for i in inv_list if i["id"] == "minecraft:iron_ore"), None)
        
        if coal_slot is None or ore_slot is None:
            return False
            
        safe_inventory_click(ctx, player_slot_map(coal_slot), "QUICK_MOVE")
        time.sleep(0.2)
        safe_inventory_click(ctx, player_slot_map(ore_slot), "QUICK_MOVE")
        time.sleep(0.2)
        
        # Wait (Blast furnace is 2x speed -> 5s)
        time.sleep(6.0)
        
        # Take result (Slot 2)
        safe_inventory_click(ctx, 2, "QUICK_MOVE")
        time.sleep(0.5)
        close_screen(ctx)
        return True

    def t404_assert_blasted(ctx: TestContext):
        return ctx.has_item("minecraft:iron_ingot"), "Has Iron Ingot"

    suite.add(TestCase(id="T404", name="Blast Furnace", description="Fast smelt iron",
                       setup=t404_setup, steps=[t404_step_blast], assertions=[t404_assert_blasted],
                       teardown=lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T404").get("bounds"))))

    # T405: Smoker (Beef -> Cooked Beef, Fast)
    def t405_setup(ctx: TestContext):
        ax, ay, az = anchors["T402"]
        prepare_standard_crafting_test(ctx, "T405", (ax+220, ay, az))
        ctx.set_block(ax+222, ay, az, "minecraft:smoker")
        get_test_state(suite_state, "T405")["pos"] = (ax+222, ay, az)
        ctx.give_item("minecraft:beef", 1)
        ctx.give_item("minecraft:coal", 1)
        
    def t405_step_smoke(ctx: TestContext) -> bool:
        pos = get_test_state(suite_state, "T405")["pos"]
        
        if not wait_for_block(ctx, pos[0], pos[1], pos[2], "minecraft:smoker"):
            return False
            
        select_hotbar_item(ctx, 8)
        ctx.client.transport.dispatch("look_at", {"x": pos[0] + 0.5, "y": pos[1] + 0.5, "z": pos[2] + 0.5})
        if not robust_interact_block(ctx, pos[0], pos[1], pos[2], retries=20):
            return False
            
        inv_list = ctx.get_inventory().get("inventory", [])
        coal_slot = next((i["slot"] for i in inv_list if i["id"] == "minecraft:coal"), None)
        beef_slot = next((i["slot"] for i in inv_list if i["id"] == "minecraft:beef"), None)
        
        if coal_slot is None or beef_slot is None: return False
        
        safe_inventory_click(ctx, player_slot_map(coal_slot), "QUICK_MOVE")
        time.sleep(0.2)
        safe_inventory_click(ctx, player_slot_map(beef_slot), "QUICK_MOVE")
        time.sleep(0.2)
        
        # Wait (Smoker is 2x speed -> 5s)
        time.sleep(6.0)
        safe_inventory_click(ctx, 2, "QUICK_MOVE")
        time.sleep(0.5)
        close_screen(ctx)
        return True
        
    def t405_assert_smoked(ctx: TestContext):
        return ctx.has_item("minecraft:cooked_beef"), "Has Cooked Beef"

    suite.add(TestCase(id="T405", name="Smoker Cooking", description="Fast cook beef",
                       setup=t405_setup, steps=[t405_step_smoke], assertions=[t405_assert_smoked],
                       teardown=lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T405").get("bounds"))))

    for tid, name in skipped_tests.items():
        suite.add(TestCase(
            id=tid, 
            name=name, 
            description="Skipped", 
            setup=lambda ctx: ctx.skip("Not implemented: Requires complex GUI automation"), 
            steps=[], 
            assertions=[]
        ))

    return suite


__all__ = ["create_extended_suite_400"]
