"""
Extended Suite 400: Crafting & Smelting (Granular Action Tests)
T400-T403: Basic Crafting, Tool Crafting, Furnace, Multi-Step
"""

import time
from test_base import TestCase, TestSuite, TestContext


def create_extended_suite_400() -> TestSuite:
    """Suite 400: Crafting & Smelting - Granular action tests."""
    suite = TestSuite("Suite_400_Crafting", "Granular crafting action tests")
    
    # T400: Basic Crafting
    def t400_setup(ctx: TestContext):
        ctx.clear_inventory()
        ctx.give_item("minecraft:oak_log", 4)
        ctx.give_item("minecraft:crafting_table", 1)
        ctx.snapshot("start")
    
    def t400_step_craft(ctx: TestContext) -> bool:
        ctx.log_event("Crafting planks from logs...")
        ctx.client.transport.dispatch("craft", {"item": "minecraft:oak_planks", "count": 4})
        time.sleep(1)
        return ctx.count_item("minecraft:oak_planks") >= 4
    
    def t400_assert_planks(ctx: TestContext):
        count = ctx.count_item("minecraft:oak_planks")
        return count >= 4, f"Planks crafted: {count}"
    
    suite.add(TestCase(
        id="T400",
        name="Basic Crafting",
        description="Craft planks from logs",
        timeout_seconds=10,
        setup=t400_setup,
        steps=[t400_step_craft],
        assertions=[t400_assert_planks]
    ))
    
    # T401: Tool Crafting
    def t401_setup(ctx: TestContext):
        ctx.clear_inventory()
        ctx.give_item("minecraft:oak_planks", 8)
        ctx.give_item("minecraft:stick", 4)
        ctx.give_item("minecraft:crafting_table", 1)
    
    def t401_step_craft_pick(ctx: TestContext) -> bool:
        ctx.log_event("Crafting wooden pickaxe...")
        ctx.client.transport.dispatch("craft", {"item": "minecraft:wooden_pickaxe", "count": 1})
        time.sleep(2)
        return ctx.has_item("minecraft:wooden_pickaxe")
    
    def t401_assert_pick(ctx: TestContext):
        has = ctx.has_item("minecraft:wooden_pickaxe")
        return has, "Pickaxe crafted" if has else "No pickaxe"
    
    suite.add(TestCase(
        id="T401",
        name="Tool Crafting",
        description="Craft wooden pickaxe",
        timeout_seconds=10,
        setup=t401_setup,
        steps=[t401_step_craft_pick],
        assertions=[t401_assert_pick]
    ))
    
    # T402: Furnace Operation
    def t402_setup(ctx: TestContext):
        ctx.clear_inventory()
        ctx.give_item("minecraft:furnace", 1)
        ctx.give_item("minecraft:raw_beef", 3)
        ctx.give_item("minecraft:coal", 8)
    
    def t402_step_smelt(ctx: TestContext) -> bool:
        x, y, z = ctx.get_position()

        ctx.log_event("Placing furnace...")
        ctx.client.transport.dispatch("place_block", {
            "block": "minecraft:furnace",
            "position": {"x": int(x) + 2, "y": int(y), "z": int(z)}
        })
        time.sleep(1)

        ctx.log_event("Opening furnace...")
        ctx.client.transport.dispatch("interact_block", {
            "position": {"x": int(x) + 2, "y": int(y), "z": int(z)}
        })
        time.sleep(1)

        ctx.log_event("Adding fuel and input...")
        # Add coal to fuel slot (slot 1)
        inv = ctx.client.transport.dispatch("get_inventory", {})
        for item in inv.get("inventory", []):
            if item.get("id") == "minecraft:coal":
                ctx.client.transport.dispatch("inventory_click", {
                    "slot": item["slot"],
                    "type": "QUICK_MOVE",
                    "button": 0
                })
                time.sleep(0.2)
                break

        # Add raw beef to input slot (slot 0)
        for item in inv.get("inventory", []):
            if item.get("id") == "minecraft:raw_beef":
                ctx.client.transport.dispatch("inventory_click", {
                    "slot": item["slot"],
                    "type": "QUICK_MOVE",
                    "button": 0
                })
                time.sleep(0.2)
                break

        ctx.log_event("Smelting in progress...")
        time.sleep(10)  # Wait for smelting

        ctx.log_event("Collecting cooked beef...")
        # Collect from output slot (slot 2)
        ctx.client.transport.dispatch("inventory_click", {
            "slot": 2,
            "type": "QUICK_MOVE",
            "button": 0
        })
        time.sleep(0.2)

        ctx.client.transport.dispatch("close_screen", {})
        return True
    
    suite.add(TestCase(
        id="T402",
        name="Furnace Operation",
        description="Place furnace and smelt raw food",
        timeout_seconds=45,
        setup=t402_setup,
        steps=[t402_step_smelt],
        assertions=[]
    ))
    
    # T403: Multi-Step Crafting
    def t403_setup(ctx: TestContext):
        ctx.clear_inventory()
        ctx.give_item("minecraft:iron_ingot", 3)
        ctx.give_item("minecraft:stick", 2)
        ctx.give_item("minecraft:crafting_table", 1)
    
    def t403_step_craft_iron_pick(ctx: TestContext) -> bool:
        ctx.log_event("Crafting iron pickaxe...")
        ctx.client.transport.dispatch("craft", {"item": "minecraft:iron_pickaxe", "count": 1})
        time.sleep(2)
        return ctx.has_item("minecraft:iron_pickaxe")
    
    def t403_assert_iron_pick(ctx: TestContext):
        has = ctx.has_item("minecraft:iron_pickaxe")
        return has, "Iron pickaxe crafted" if has else "No iron pickaxe"
    
    suite.add(TestCase(
        id="T403",
        name="Multi-Step Crafting",
        description="Craft iron pickaxe from ingots and sticks",
        timeout_seconds=15,
        setup=t403_setup,
        steps=[t403_step_craft_iron_pick],
        assertions=[t403_assert_iron_pick]
    ))
    
    return suite


__all__ = ["create_extended_suite_400"]
