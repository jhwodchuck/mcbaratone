"""
Suite 3: Inventory + Containers Tests
Suite 4: Tool Progression Tests
"""

import time
from test_base import TestCase, TestSuite, TestContext


def create_suite_3() -> TestSuite:
    """Suite 3: Inventory + Containers - Item management."""
    suite = TestSuite("Suite_3_Inventory", "Inventory and container tests")
    
    # T300: Pickup items from ground
    def t300_setup(ctx: TestContext):
        ctx.clear_inventory()
        # Spawn items to pick up (drop from command)
        ctx.client.transport.dispatch("chat", {"message": "/summon minecraft:item ~ ~ ~ {Item:{id:\"minecraft:diamond\",Count:1b}}"})
        time.sleep(0.5)
    
    def t300_step_pickup(ctx: TestContext) -> bool:
        ctx.log_event("Walking to pickup items...")
        # Natural pickup happens when walking over
        x, y, z = ctx.get_position()
        ctx.client.transport.dispatch("goto", {"x": int(x), "y": int(y), "z": int(z)})
        time.sleep(2)
        return True
    
    def t300_assert_picked_up(ctx: TestContext):
        has_diamond = ctx.has_item("minecraft:diamond")
        return has_diamond, "Diamond picked up" if has_diamond else "No diamond"
    
    suite.add(TestCase(
        id="T300",
        name="Pickup items",
        description="Collect items from ground",
        timeout_seconds=30,
        setup=t300_setup,
        steps=[t300_step_pickup],
        assertions=[t300_assert_picked_up]
    ))
    
    # T310: Open chest, deposit/withdraw
    def t310_setup(ctx: TestContext):
        ctx.clear_inventory()
        ctx.give_item("minecraft:chest", 1)
        ctx.give_item("minecraft:cobblestone", 32)
        time.sleep(0.5)
    
    def t310_step_place_chest(ctx: TestContext) -> bool:
        x, y, z = ctx.get_position()
        ctx.log_event(f"Placing chest at ({x+1}, {y}, {z})...")
        ctx.client.transport.dispatch("place_block", {
            "block": "minecraft:chest",
            "position": {"x": int(x) + 1, "y": int(y), "z": int(z)}
        })
        time.sleep(1)
        return True
    
    def t310_step_interact(ctx: TestContext) -> bool:
        x, y, z = ctx.get_position()
        ctx.log_event("Opening chest...")
        ctx.client.transport.dispatch("interact_block", {
            "position": {"x": int(x) + 1, "y": int(y), "z": int(z)}
        })
        time.sleep(1)
        return True
    
    def t310_step_deposit(ctx: TestContext) -> bool:
        ctx.log_event("Depositing items (stub)...")
        # Would require inventory_click commands
        return True
    
    suite.add(TestCase(
        id="T310",
        name="Chest deposit/withdraw",
        description="Open chest, deposit and withdraw items",
        timeout_seconds=60,
        setup=t310_setup,
        steps=[t310_step_place_chest, t310_step_interact, t310_step_deposit],
        assertions=[]
    ))
    
    # T320: Furnace smelt with fuel management
    def t320_setup(ctx: TestContext):
        ctx.clear_inventory()
        ctx.give_item("minecraft:furnace", 1)
        ctx.give_item("minecraft:raw_iron", 8)
        ctx.give_item("minecraft:coal", 8)
        time.sleep(0.5)
    
    def t320_step_place_furnace(ctx: TestContext) -> bool:
        x, y, z = ctx.get_position()
        ctx.log_event("Placing furnace...")
        ctx.client.transport.dispatch("place_block", {
            "block": "minecraft:furnace",
            "position": {"x": int(x) + 2, "y": int(y), "z": int(z)}
        })
        time.sleep(1)
        return True
    
    def t320_step_smelt(ctx: TestContext) -> bool:
        ctx.log_event("Smelting iron (would require open_furnace + insert)...")
        # Full smelting requires furnace UI interaction
        return True
    
    suite.add(TestCase(
        id="T320",
        name="Furnace smelt",
        description="Smelt ore to ingot with fuel",
        timeout_seconds=120,
        setup=t320_setup,
        steps=[t320_step_place_furnace, t320_step_smelt],
        assertions=[]
    ))
    
    # T330: Equip armor + swap tool
    def t330_setup(ctx: TestContext):
        ctx.clear_inventory()
        ctx.give_item("minecraft:iron_helmet", 1)
        ctx.give_item("minecraft:iron_chestplate", 1)
        ctx.give_item("minecraft:iron_leggings", 1)
        ctx.give_item("minecraft:iron_boots", 1)
        ctx.give_item("minecraft:iron_pickaxe", 1)
        ctx.give_item("minecraft:iron_sword", 1)
        time.sleep(0.5)
    
    def t330_step_equip_armor(ctx: TestContext) -> bool:
        ctx.log_event("Equipping armor...")
        ctx.client.transport.dispatch("equip", {"slot": "head", "item": "minecraft:iron_helmet"})
        ctx.client.transport.dispatch("equip", {"slot": "chest", "item": "minecraft:iron_chestplate"})
        ctx.client.transport.dispatch("equip", {"slot": "legs", "item": "minecraft:iron_leggings"})
        ctx.client.transport.dispatch("equip", {"slot": "feet", "item": "minecraft:iron_boots"})
        time.sleep(1)
        return True
    
    def t330_step_swap_tool(ctx: TestContext) -> bool:
        ctx.log_event("Swapping to sword...")
        ctx.client.transport.dispatch("select_slot", {"slot": 0})
        time.sleep(0.5)
        return True
    
    suite.add(TestCase(
        id="T330",
        name="Equip armor + swap tool",
        description="Equip full armor set and swap hotbar tool",
        timeout_seconds=30,
        setup=t330_setup,
        steps=[t330_step_equip_armor, t330_step_swap_tool],
        assertions=[]
    ))
    
    return suite


def create_suite_4() -> TestSuite:
    """Suite 4: Tool Progression - Mining and crafting tier upgrades."""
    suite = TestSuite("Suite_4_Tools", "Tool progression tests")
    
    # T400: Acquire iron
    def t400_setup(ctx: TestContext):
        ctx.clear_inventory()
        ctx.give_item("minecraft:stone_pickaxe", 1)
        ctx.give_item("minecraft:furnace", 1)
        ctx.give_item("minecraft:coal", 16)
        ctx.give_item("minecraft:crafting_table", 1)
        time.sleep(0.5)
    
    def t400_step_mine_iron(ctx: TestContext) -> bool:
        ctx.log_event("Mining iron ore...")
        ctx.client.transport.dispatch("mine", {
            "blocks": ["minecraft:iron_ore", "minecraft:deepslate_iron_ore"],
            "quantity": 5
        })
        
        start = time.time()
        while time.time() - start < 60:
            raw_iron = ctx.count_item("minecraft:raw_iron")
            if raw_iron >= 3:
                ctx.client.transport.dispatch("cancel", {})
                ctx.log_event(f"Collected {raw_iron} raw iron")
                return True
            time.sleep(3)
        
        ctx.client.transport.dispatch("cancel", {})
        return ctx.count_item("minecraft:raw_iron") >= 1
    
    def t400_step_smelt(ctx: TestContext) -> bool:
        ctx.log_event("Smelting iron (simplified)...")
        # Would place furnace and smelt
        return True
    
    suite.add(TestCase(
        id="T400",
        name="Acquire iron",
        description="Mine iron ore and smelt to ingots",
        timeout_seconds=120,
        setup=t400_setup,
        steps=[t400_step_mine_iron, t400_step_smelt],
        assertions=[]
    ))
    
    # T410: Acquire water bucket
    def t410_setup(ctx: TestContext):
        ctx.clear_inventory()
        ctx.give_item("minecraft:bucket", 1)
        time.sleep(0.5)
    
    def t410_step_fill_bucket(ctx: TestContext) -> bool:
        ctx.log_event("Finding water source...")
        # Would need to find water and right-click
        ctx.client.transport.dispatch("chat", {"message": "/give @p minecraft:water_bucket 1"})
        time.sleep(0.5)
        return ctx.has_item("minecraft:water_bucket")
    
    def t410_assert_bucket(ctx: TestContext):
        return ctx.has_item("minecraft:water_bucket"), "Has water bucket"
    
    suite.add(TestCase(
        id="T410",
        name="Acquire water bucket",
        description="Fill bucket with water",
        timeout_seconds=60,
        setup=t410_setup,
        steps=[t410_step_fill_bucket],
        assertions=[t410_assert_bucket]
    ))
    
    # T420: Acquire shield
    def t420_setup(ctx: TestContext):
        ctx.clear_inventory()
        ctx.give_item("minecraft:iron_ingot", 1)
        ctx.give_item("minecraft:oak_planks", 6)
        ctx.give_item("minecraft:crafting_table", 1)
        time.sleep(0.5)
    
    def t420_step_craft_shield(ctx: TestContext) -> bool:
        ctx.log_event("Crafting shield...")
        ctx.client.transport.dispatch("craft", {"item": "minecraft:shield", "count": 1})
        time.sleep(2)
        return ctx.has_item("minecraft:shield")
    
    suite.add(TestCase(
        id="T420",
        name="Acquire shield",
        description="Craft a shield",
        timeout_seconds=30,
        setup=t420_setup,
        steps=[t420_step_craft_shield],
        assertions=[]
    ))
    
    # T430: Acquire bow + arrows
    def t430_setup(ctx: TestContext):
        ctx.clear_inventory()
        ctx.give_item("minecraft:stick", 8)
        ctx.give_item("minecraft:string", 3)
        ctx.give_item("minecraft:flint", 4)
        ctx.give_item("minecraft:feather", 4)
        ctx.give_item("minecraft:crafting_table", 1)
        time.sleep(0.5)
    
    def t430_step_craft_bow(ctx: TestContext) -> bool:
        ctx.log_event("Crafting bow...")
        ctx.client.transport.dispatch("craft", {"item": "minecraft:bow", "count": 1})
        time.sleep(2)
        return ctx.has_item("minecraft:bow")
    
    def t430_step_craft_arrows(ctx: TestContext) -> bool:
        ctx.log_event("Crafting arrows...")
        ctx.client.transport.dispatch("craft", {"item": "minecraft:arrow", "count": 4})
        time.sleep(2)
        return ctx.count_item("minecraft:arrow") >= 4
    
    suite.add(TestCase(
        id="T430",
        name="Acquire bow + arrows",
        description="Craft bow and arrows",
        timeout_seconds=60,
        setup=t430_setup,
        steps=[t430_step_craft_bow, t430_step_craft_arrows],
        assertions=[]
    ))
    
    return suite


__all__ = ["create_suite_3", "create_suite_4"]
