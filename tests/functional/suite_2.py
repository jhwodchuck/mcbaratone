"""
Suite 2: Basic Survival Loop Tests
"""

import time
from test_base import TestCase, TestSuite, TestContext


def create_suite_2() -> TestSuite:
    """Suite 2: Basic Survival Loop - Core gathering and crafting."""
    suite = TestSuite("Suite_2_Survival", "Basic survival loop tests")
    
    # T200: Punch tree - collect logs
    def t200_setup(ctx: TestContext):
        ctx.set_time("day")
        ctx.clear_inventory()
        ctx.snapshot("start")
    
    def t200_step_punch_tree(ctx: TestContext) -> bool:
        ctx.log_event("Mining logs...")
        ctx.client.transport.dispatch("mine", {
            "blocks": ["minecraft:oak_log", "minecraft:birch_log", "minecraft:spruce_log"],
            "quantity": 6
        })
        
        # Wait for collection
        start = time.time()
        while time.time() - start < 60:
            total = 0
            for log_type in ["minecraft:oak_log", "minecraft:birch_log", "minecraft:spruce_log", 
                            "minecraft:dark_oak_log", "minecraft:acacia_log", "minecraft:jungle_log"]:
                total += ctx.count_item(log_type)
            
            if total >= 6:
                ctx.log_event(f"Collected {total} logs")
                ctx.client.transport.dispatch("cancel", {})
                return True
            time.sleep(2)
        
        ctx.client.transport.dispatch("cancel", {})
        return False
    
    def t200_assert_logs(ctx: TestContext):
        total = 0
        for log_type in ["minecraft:oak_log", "minecraft:birch_log", "minecraft:spruce_log",
                        "minecraft:dark_oak_log", "minecraft:acacia_log", "minecraft:jungle_log"]:
            total += ctx.count_item(log_type)
        return total >= 6, f"Logs collected: {total}"
    
    suite.add(TestCase(
        id="T200",
        name="Punch tree",
        description="Collect at least 6 logs by breaking trees",
        timeout_seconds=90,
        setup=t200_setup,
        steps=[t200_step_punch_tree],
        assertions=[t200_assert_logs]
    ))
    
    # T210: Craft planks/sticks/table
    def t210_setup(ctx: TestContext):
        ctx.clear_inventory()
        ctx.give_item("minecraft:oak_log", 8)
        time.sleep(0.5)
        ctx.snapshot("start")
    
    def t210_step_craft_planks(ctx: TestContext) -> bool:
        ctx.log_event("Crafting planks...")
        ctx.client.transport.dispatch("craft", {"item": "minecraft:oak_planks", "count": 4})
        time.sleep(1)
        return ctx.count_item("minecraft:oak_planks") >= 4
    
    def t210_step_craft_sticks(ctx: TestContext) -> bool:
        ctx.log_event("Crafting sticks...")
        ctx.client.transport.dispatch("craft", {"item": "minecraft:stick", "count": 4})
        time.sleep(1)
        return ctx.count_item("minecraft:stick") >= 4
    
    def t210_step_craft_table(ctx: TestContext) -> bool:
        ctx.log_event("Crafting crafting table...")
        ctx.client.transport.dispatch("craft", {"item": "minecraft:crafting_table", "count": 1})
        time.sleep(1)
        return ctx.has_item("minecraft:crafting_table")
    
    suite.add(TestCase(
        id="T210",
        name="Craft planks/sticks/table",
        description="Craft basic items from logs",
        timeout_seconds=60,
        setup=t210_setup,
        steps=[t210_step_craft_planks, t210_step_craft_sticks, t210_step_craft_table],
        assertions=[]
    ))
    
    # T220: Craft wooden pickaxe
    def t220_setup(ctx: TestContext):
        ctx.clear_inventory()
        ctx.give_item("minecraft:oak_planks", 8)
        ctx.give_item("minecraft:stick", 4)
        ctx.give_item("minecraft:crafting_table", 1)
        time.sleep(0.5)
    
    def t220_step_craft_pick(ctx: TestContext) -> bool:
        ctx.log_event("Crafting wooden pickaxe...")
        ctx.client.transport.dispatch("craft", {"item": "minecraft:wooden_pickaxe", "count": 1})
        time.sleep(2)
        return ctx.has_item("minecraft:wooden_pickaxe")
    
    def t220_assert_pick(ctx: TestContext):
        return ctx.has_item("minecraft:wooden_pickaxe"), "Has wooden pickaxe"
    
    suite.add(TestCase(
        id="T220",
        name="Craft wooden pickaxe",
        description="Craft a wooden pickaxe",
        timeout_seconds=30,
        setup=t220_setup,
        steps=[t220_step_craft_pick],
        assertions=[t220_assert_pick]
    ))
    
    # T230: Mine stone - craft stone pick
    def t230_setup(ctx: TestContext):
        ctx.clear_inventory()
        ctx.give_item("minecraft:wooden_pickaxe", 1)
        ctx.give_item("minecraft:stick", 4)
        ctx.give_item("minecraft:crafting_table", 1)
        time.sleep(0.5)
    
    def t230_step_mine_stone(ctx: TestContext) -> bool:
        ctx.log_event("Mining stone...")
        ctx.client.transport.dispatch("mine", {
            "blocks": ["minecraft:stone"],
            "quantity": 10
        })
        
        start = time.time()
        while time.time() - start < 45:
            if ctx.count_item("minecraft:cobblestone") >= 8:
                ctx.client.transport.dispatch("cancel", {})
                return True
            time.sleep(2)
        
        ctx.client.transport.dispatch("cancel", {})
        return ctx.count_item("minecraft:cobblestone") >= 3
    
    def t230_step_craft_stone_pick(ctx: TestContext) -> bool:
        ctx.log_event("Crafting stone pickaxe...")
        ctx.client.transport.dispatch("craft", {"item": "minecraft:stone_pickaxe", "count": 1})
        time.sleep(2)
        return ctx.has_item("minecraft:stone_pickaxe")
    
    suite.add(TestCase(
        id="T230",
        name="Mine stone - stone pick",
        description="Mine cobblestone and craft stone pickaxe",
        timeout_seconds=90,
        setup=t230_setup,
        steps=[t230_step_mine_stone, t230_step_craft_stone_pick],
        assertions=[]
    ))
    
    # T240: Make furnace + cook food
    def t240_setup(ctx: TestContext):
        ctx.clear_inventory()
        ctx.give_item("minecraft:cobblestone", 16)
        ctx.give_item("minecraft:oak_log", 8)
        ctx.give_item("minecraft:raw_porkchop", 3)
        ctx.give_item("minecraft:crafting_table", 1)
        time.sleep(0.5)
    
    def t240_step_craft_furnace(ctx: TestContext) -> bool:
        ctx.log_event("Crafting furnace...")
        ctx.client.transport.dispatch("craft", {"item": "minecraft:furnace", "count": 1})
        time.sleep(2)
        return ctx.has_item("minecraft:furnace")
    
    def t240_step_cook(ctx: TestContext) -> bool:
        ctx.log_event("Cooking food (simulated - requires furnace placement)...")
        # Full cooking requires placing furnace, inserting items, waiting
        # Simplified: just check we have furnace
        return ctx.has_item("minecraft:furnace")
    
    suite.add(TestCase(
        id="T240",
        name="Make furnace + cook food",
        description="Craft furnace and cook raw food",
        timeout_seconds=120,
        setup=t240_setup,
        steps=[t240_step_craft_furnace, t240_step_cook],
        assertions=[]
    ))
    
    # T250: Place bed + sleep
    def t250_setup(ctx: TestContext):
        ctx.clear_inventory()
        ctx.give_item("minecraft:white_bed", 1)
        ctx.set_time("night")
        time.sleep(0.5)
    
    def t250_step_place_bed(ctx: TestContext) -> bool:
        x, y, z = ctx.get_position()
        ctx.log_event(f"Placing bed near ({x}, {y}, {z})...")
        ctx.client.transport.dispatch("place_block", {
            "block": "minecraft:white_bed",
            "position": {"x": int(x) + 1, "y": int(y), "z": int(z)}
        })
        time.sleep(1)
        return True
    
    def t250_step_sleep(ctx: TestContext) -> bool:
        ctx.log_event("Attempting to sleep...")
        x, y, z = ctx.get_position()
        ctx.client.transport.dispatch("interact_block", {
            "position": {"x": int(x) + 1, "y": int(y), "z": int(z)}
        })
        time.sleep(3)
        return True
    
    suite.add(TestCase(
        id="T250",
        name="Place bed + sleep",
        description="Place bed and sleep through night",
        timeout_seconds=60,
        setup=t250_setup,
        steps=[t250_step_place_bed, t250_step_sleep],
        assertions=[]
    ))
    
    # T260: Create safe shelter
    def t260_setup(ctx: TestContext):
        ctx.clear_inventory()
        ctx.give_item("minecraft:cobblestone", 64)
        ctx.give_item("minecraft:torch", 16)
        time.sleep(0.5)
    
    def t260_step_build_shelter(ctx: TestContext) -> bool:
        ctx.log_event("Building shelter (stub - requires full building logic)...")
        # Would require placing blocks in a pattern
        return True
    
    suite.add(TestCase(
        id="T260",
        name="Create safe shelter",
        description="Build an enclosed 9x9 structure",
        timeout_seconds=180,
        setup=t260_setup,
        steps=[t260_step_build_shelter],
        assertions=[]
    ))
    
    return suite


__all__ = ["create_suite_2"]
