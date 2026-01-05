"""
Extended Suite 300: Inventory & Equipment (Granular Action Tests)
T300-T304: Pickup, Transfer, Equip, Switch, Consume
"""

import time
from test_base import TestCase, TestSuite, TestContext
from baritone_client.utils.arena_loader import ArenaLoader


def create_extended_suite_300() -> TestSuite:
    """Suite 300: Inventory & Equipment - Granular action tests."""
    suite = TestSuite("Suite_300_Inventory", "Granular inventory action tests")
    
    # T300: Item Pickup
    def t300_setup(ctx: TestContext):
        loader = ArenaLoader(ctx.client.transport)
        arena = loader.load("item_scatter")
        ctx.snapshot("start")
    
    def t300_step_pickup(ctx: TestContext) -> bool:
        ctx.log_event("Moving to pickup items...")
        x, y, z = ctx.get_position()
        ctx.client.transport.dispatch("goto", {"x": int(x), "y": int(y), "z": int(z)})
        time.sleep(2)
        return True
    
    def t300_assert_picked_up(ctx: TestContext):
        has = ctx.has_item("minecraft:diamond")
        return has, "Diamond picked up" if has else "No diamond"
    
    suite.add(TestCase(
        id="T300",
        name="Item Pickup",
        description="Pickup dropped items from ground",
        timeout_seconds=10,
        setup=t300_setup,
        steps=[t300_step_pickup],
        assertions=[t300_assert_picked_up]
    ))
    
    # T301: Item Transfer
    def t301_setup(ctx: TestContext):
        loader = ArenaLoader(ctx.client.transport)
        arena = loader.load("inventory_test")
        # Ensure we have items to transfer
        ctx.give_item("minecraft:cobblestone", 1)
        ctx.snapshot("start")

    def t301_step_transfer(ctx: TestContext) -> bool:
        ctx.log_event("Transferring items between slots...")
        # Move item from slot 9 (first inventory slot) to slot 10
        ctx.client.transport.dispatch("inventory_click", {
            "slot": 9,
            "type": "PICKUP",
            "button": 0
        })
        time.sleep(0.2)
        ctx.client.transport.dispatch("inventory_click", {
            "slot": 10,
            "type": "PICKUP",
            "button": 0
        })
        time.sleep(0.2)
        return True

    def t301_assert_item_moved(ctx: TestContext):
        # Check that item was moved from slot 9 to slot 10
        inv = ctx.get_inventory()
        slot_9_item = inv["inventory"][9].get("id") if len(inv["inventory"]) > 9 and inv["inventory"][9] else None
        slot_10_item = inv["inventory"][10].get("id") if len(inv["inventory"]) > 10 and inv["inventory"][10] else None

        # Item should no longer be in slot 9 and should be in slot 10
        return slot_9_item != "minecraft:cobblestone" and slot_10_item == "minecraft:cobblestone", "Item moved between slots"

    def t301_assert_inventory_consistent(ctx: TestContext):
        # Total cobblestone count should remain the same
        start_cobble = ctx.snapshots[0].get("inventory_cobble", 0) if ctx.snapshots else 0
        current_cobble = ctx.count_item("minecraft:cobblestone")
        return current_cobble == start_cobble, f"Cobblestone count consistent: {current_cobble}"

    suite.add(TestCase(
        id="T301",
        name="Item Transfer",
        description="Move items between inventory slots",
        timeout_seconds=5,
        setup=t301_setup,
        steps=[t301_step_transfer],
        assertions=[t301_assert_item_moved, t301_assert_inventory_consistent]
    ))
    
    # T302: Armor Equip
    def t302_setup(ctx: TestContext):
        loader = ArenaLoader(ctx.client.transport)
        arena = loader.load("gear_room")
        ctx.snapshot("start")
    
    def t302_step_equip(ctx: TestContext) -> bool:
        ctx.log_event("Equipping armor set...")
        ctx.client.transport.dispatch("equip", {"slot": "head", "item": "minecraft:iron_helmet"})
        ctx.client.transport.dispatch("equip", {"slot": "chest", "item": "minecraft:iron_chestplate"})
        ctx.client.transport.dispatch("equip", {"slot": "legs", "item": "minecraft:iron_leggings"})
        ctx.client.transport.dispatch("equip", {"slot": "feet", "item": "minecraft:iron_boots"})
        time.sleep(1)
        return True
    
    suite.add(TestCase(
        id="T302",
        name="Armor Equip",
        description="Equip full iron armor set",
        timeout_seconds=10,
        setup=t302_setup,
        steps=[t302_step_equip],
        assertions=[]
    ))
    
    # T303: Tool Switching
    def t303_setup(ctx: TestContext):
        loader = ArenaLoader(ctx.client.transport)
        arena = loader.load("tool_bench")
    
    def t303_step_switch(ctx: TestContext) -> bool:
        ctx.log_event("Switching between tools...")
        ctx.client.transport.dispatch("select_slot", {"slot": 0})
        time.sleep(0.3)
        ctx.client.transport.dispatch("select_slot", {"slot": 1})
        time.sleep(0.3)
        ctx.client.transport.dispatch("select_slot", {"slot": 2})
        time.sleep(0.3)
        return True
    
    suite.add(TestCase(
        id="T303",
        name="Tool Switching",
        description="Switch between hotbar tools",
        timeout_seconds=5,
        setup=t303_setup,
        steps=[t303_step_switch],
        assertions=[]
    ))
    
    # T304: Food Consumption
    def t304_setup(ctx: TestContext):
        loader = ArenaLoader(ctx.client.transport)
        arena = loader.load("food_test")
        ctx.snapshot("start")
    
    def t304_step_eat(ctx: TestContext) -> bool:
        ctx.log_event("Eating food...")
        ctx.client.transport.dispatch("use_item", {"item": "minecraft:cooked_beef"})
        time.sleep(2)
        return True
    
    def t304_assert_ate(ctx: TestContext):
        start_count = 8
        current = ctx.count_item("minecraft:cooked_beef")
        return current < start_count, f"Food consumed: {start_count - current}"
    
    suite.add(TestCase(
        id="T304",
        name="Food Consumption",
        description="Eat food to restore hunger",
        timeout_seconds=10,
        setup=t304_setup,
        steps=[t304_step_eat],
        assertions=[t304_assert_ate]
    ))
    
    return suite


__all__ = ["create_extended_suite_300"]
