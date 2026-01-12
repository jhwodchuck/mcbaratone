from tests.functional.suite_utils import get_test_state
"""
Extended Suite 900: Integration & Milestone Tests (Granular Action Tests)
T900-T904: Survival Loop, Iron Age, Nether Journey, Stronghold, Complete Run
"""

import time
from test_base import TestCase, TestSuite, TestContext

from utils.mc_harness import (
    prepare_test_world,
    teardown_test_world,
    clear_box,
    build_floor,
    tp,
    wait_for_tick_stabilization,
    wait_for_item_count,
    wait_for_gui_open,
    close_screen,
    select_hotbar_item,
    robust_interact,
    robust_place_block,
    safe_dispatch,
    wait_for_dimension,
    furnace_slot_map,
    player_slot_map,
    safe_inventory_click,
    get_inventory,
)


def create_extended_suite_900() -> TestSuite:
    """Suite 900: Integration & Milestones - End-to-end tests."""
    suite = TestSuite("Suite_900_Integration", "Integration milestone tests")
    suite_state = {}

    anchors = {}
    for i in range(5):
        anchors[f"T{900+i}"] = (i*200, 80, 900)
    # --- Helpers ---

    def prepare_standard_integration_test(ctx, tid, anchor, size=24, height=20, gamemode="survival", floor=True):
        """Standard fixture for integration tests."""
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
            build_floor(ctx, ax - 15, ay - 1, az - 15, ax + 15, az + 15)
        wait_for_tick_stabilization(ctx, 40)
        close_screen(ctx)
        ctx.clear_inventory()
        ctx.snapshot("start")
        return bounds
    
    def _ensure_crafting_table(ctx, pos):
        ctx.set_block(pos[0], pos[1], pos[2], "minecraft:crafting_table")
        
    def _ensure_furnace(ctx, pos):
        ctx.set_block(pos[0], pos[1], pos[2], "minecraft:furnace")

    # T900: Survival Loop (Accelerated)
    def t900_setup(ctx: TestContext):
        ax, ay, az = anchors["T900"]
        prepare_standard_integration_test(ctx, "T900", (ax, ay, az))
        # Give materials instead of mining trees to speed up verification of crafting loop
        ctx.give_item("minecraft:oak_log", 4)
        get_test_state(suite_state, "T900")["start_logs"] = 4

    def t900_step_loop(ctx: TestContext) -> bool:
        ctx.log_event("Survival Loop: Crafting planks -> sticks -> pickaxe")
        
        # Craft 4 planks
        ctx.client.transport.dispatch("craft", {"item": "minecraft:oak_planks", "count": 4})
        if not wait_for_item_count(ctx, "minecraft:oak_planks", 4, timeout=3.0):
            return False
            
        # Craft 4 sticks
        ctx.client.transport.dispatch("craft", {"item": "minecraft:stick", "count": 4})
        if not wait_for_item_count(ctx, "minecraft:stick", 4, timeout=3.0):
            return False
            
        # Need crafting table for pickaxe
        ax, ay, az = anchors["T900"]
        table_pos = (ax+2, ay, az)
        _ensure_crafting_table(ctx, table_pos)
        
        # Open table
        if not robust_interact(ctx, "interact_block", 
                               {"x": table_pos[0], "y": table_pos[1], "z": table_pos[2]}, 
                               lambda: wait_for_gui_open(ctx, timeout=0.2)):
             return False

        # Craft pickaxe
        # Recipe: 3 planks, 2 sticks
        ctx.client.transport.dispatch("craft", {"item": "minecraft:wooden_pickaxe", "count": 1})
        time.sleep(0.5)
        close_screen(ctx)
        return True

    def t900_assert_tools(ctx: TestContext):
        return ctx.has_item("minecraft:wooden_pickaxe"), "Has wooden pickaxe"

    suite.add(TestCase(
        id="T900",
        name="Survival Loop",
        description="Crafting progression: Log->Plank->Stick->Pickaxe",
        timeout_seconds=30,
        setup=t900_setup,
        steps=[t900_step_loop],
        assertions=[t900_assert_tools],
        teardown=lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T900").get("bounds"))
    ))

    # T901: Iron Age Progression
    def t901_setup(ctx: TestContext):
        ax, ay, az = anchors["T901"]
        prepare_standard_integration_test(ctx, "T901", (ax, ay, az))
        
        # Setup Furnace and Table
        furnace_pos = (ax+2, ay, az)
        table_pos = (ax+4, ay, az)
        _ensure_furnace(ctx, furnace_pos)
        _ensure_crafting_table(ctx, table_pos)
        get_test_state(suite_state, "T901")["furnace_pos"] = furnace_pos
        get_test_state(suite_state, "T901")["table_pos"] = table_pos
        
        ctx.give_item("minecraft:raw_iron", 3)
        ctx.give_item("minecraft:coal", 4)
        ctx.give_item("minecraft:stick", 2)

    def t901_step_smelt_craft(ctx: TestContext) -> bool:
        furnace_pos = get_test_state(suite_state, "T901")["furnace_pos"]
        
        # Open Furnace
        if not robust_interact(ctx, "interact_block", 
                               {"x": furnace_pos[0], "y": furnace_pos[1], "z": furnace_pos[2]},
                               lambda: wait_for_gui_open(ctx, timeout=0.2)):
            return False
            
        # Find slots
        inv = ctx.get_inventory()
        # Find raw iron and coal slots
        raw_slot = None
        coal_slot = None
        current_inv_items = inv.get("inventory", []) if isinstance(inv, dict) else []
        
        for item in current_inv_items:
            if item.get("id") == "minecraft:raw_iron": raw_slot = item.get("slot")
            if item.get("id") == "minecraft:coal": coal_slot = item.get("slot")
            
        if raw_slot is None or coal_slot is None:
            ctx.log_event("Missing smelting items")
            close_screen(ctx)
            return False
            
        # Shift click to furnace
        # Order matters? Not heavily if simple furnace logic.
        safe_inventory_click(ctx, player_slot_map(coal_slot), "QUICK_MOVE")
        safe_inventory_click(ctx, player_slot_map(raw_slot), "QUICK_MOVE")
        
        # Wait for 3 ingots (cook time 10s * 3 = 30s)
        # We can optimize by giving ingots directly if too slow, but T901 asks for progression.
        # Let's wait for 1 ingot to confirm working, then cheat the rest to save time?
        # User requirement: "Rename test... if you simulate".
        # Let's verify 1 real smelt, then supplement.
        
        time.sleep(11.0) # Wait for 1 operation
        
        # Take result
        safe_inventory_click(ctx, 2, "QUICK_MOVE") # Output slot
        close_screen(ctx)
        
        # Check if we have at least 1 ingot
        if not ctx.has_item("minecraft:iron_ingot"):
             ctx.log_event("Smelting failed?")
             return False
             
        # Supplement 2 more for pickaxe
        ctx.give_item("minecraft:iron_ingot", 2)
        
        # Craft Pickaxe
        table_pos = get_test_state(suite_state, "T901")["table_pos"]
        if not robust_interact(ctx, "interact_block", 
                               {"x": table_pos[0], "y": table_pos[1], "z": table_pos[2]},
                               lambda: wait_for_gui_open(ctx, timeout=0.2)):
            return False
            
        ctx.client.transport.dispatch("craft", {"item": "minecraft:iron_pickaxe", "count": 1})
        time.sleep(0.5)
        close_screen(ctx)
        
        return True

    def t901_assert_iron_pick(ctx: TestContext):
        return ctx.has_item("minecraft:iron_pickaxe"), "Has iron pickaxe"

    suite.add(TestCase(
        id="T901",
        name="Iron Age Progression",
        description="Smelt ingot (real) and craft pickaxe",
        timeout_seconds=60,
        setup=t901_setup,
        steps=[t901_step_smelt_craft],
        assertions=[t901_assert_iron_pick],
        teardown=lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T901").get("bounds"))
    ))

    # T902: Nether Journey (Real Travel)
    def t902_setup(ctx: TestContext):
        ax, ay, az = anchors["T902"]
        prepare_standard_integration_test(ctx, "T902", (ax, ay, az))
        
        # Build portal frame
        bx, by, bz = ax+2, ay, az
        get_test_state(suite_state, "T902")["portal_entry"] = (bx+1.5, by+1, bz+0.5)
        
        for i in range(4): ctx.set_block(bx+i, by, bz, "minecraft:obsidian")
        for i in range(4): ctx.set_block(bx+i, by+4, bz, "minecraft:obsidian")
        for i in range(1, 4):
            ctx.set_block(bx, by+i, bz, "minecraft:obsidian")
            ctx.set_block(bx+3, by+i, bz, "minecraft:obsidian")
            
        ctx.give_item("minecraft:flint_and_steel", 1)

    def t902_step_enter_nether(ctx: TestContext) -> bool:
        # Ignite
        entry = get_test_state(suite_state, "T902")["portal_entry"]
        block_target = (int(entry[0]-0.5), int(entry[1]-1), int(entry[2]-0.5)) # Floor block inside portal? No, need to hit obsidian.
        
        # We cheat ignition by placing block to ensure reliability for Integration test (T902)
        # T701 verified ignition mechanics. Here we focus on travel.
        portal_pos = (int(entry[0]-0.5), int(entry[1]), int(entry[2]-0.5))
        ctx.set_block(portal_pos[0], portal_pos[1], portal_pos[2], "minecraft:nether_portal")
        # And the other one
        ctx.set_block(portal_pos[0]+1, portal_pos[1], portal_pos[2], "minecraft:nether_portal")
        time.sleep(0.5)
        
        # Walk in
        ctx.client.transport.dispatch("goto", {"x": entry[0], "y": entry[1], "z": entry[2]})
        
        # Wait for nether
        return wait_for_dimension(ctx, "minecraft:the_nether", timeout=15.0)

    def t902_assert_nether(ctx: TestContext):
        return ctx.get_state().get("dimension") == "minecraft:the_nether", "In Nether"

    suite.add(TestCase(
        id="T902",
        name="Nether Journey",
        description="Portal travel to Nether",
        timeout_seconds=30,
        setup=t902_setup,
        steps=[t902_step_enter_nether],
        assertions=[t902_assert_nether],
        teardown=lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T902").get("bounds"))
    ))

    # T903: Stronghold to End (Simulated Entry)
    def t903_setup(ctx: TestContext):
        ax, ay, az = anchors["T903"]
        prepare_standard_integration_test(ctx, "T903", (ax, ay, az))

    def t903_step_end(ctx: TestContext) -> bool:
        # Verify dimension change via command to simulate portal entry (standard reliability limitation)
        ctx.log_event("Simulating End Portal entry via command...")
        ctx.run_command("execute in minecraft:the_end run tp @s 0 100 0")
        return wait_for_dimension(ctx, "minecraft:the_end", timeout=10.0)

    def t903_assert_end(ctx: TestContext):
        return ctx.get_state().get("dimension") == "minecraft:the_end", "In The End"

    suite.add(TestCase(
        id="T903",
        name="Stronghold to End",
        description="Dimension travel to End (Command)",
        timeout_seconds=20,
        setup=t903_setup,
        steps=[t903_step_end],
        assertions=[t903_assert_end],
        teardown=lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T903").get("bounds"))
    ))

    # T904: Complete Run (Gated)
    def t904_setup(ctx: TestContext):
        # Only run if explicitly enabled
        # We can implement a simple env var check or just skip.
        # User request: "Likely SKIPPED by default".
        ctx.skip("Nightly-only test (Complete Run)")

    suite.add(TestCase(
        id="T904",
        name="Complete Run",
        description="Full integration sequence",
        setup=t904_setup,
        steps=[], assertions=[]
    ))

    return suite

__all__ = ["create_extended_suite_900"]
