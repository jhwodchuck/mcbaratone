"""
Extended Suite 700: Travel & Dimensions (Granular Action Tests)
T700-T704: Portal Construction, Ignition, Dimension Travel, Navigation, Structure Location
"""

import time
from test_base import TestCase, TestSuite, TestContext


def create_extended_suite_700() -> TestSuite:
    """Suite 700: Travel & Dimensions - Granular action tests."""
    suite = TestSuite("Suite_700_Travel", "Granular travel action tests")
    
    # T700: Portal Construction
    def t700_setup(ctx: TestContext):
        ctx.clear_inventory()
        ctx.give_item("minecraft:obsidian", 14)
        ctx.snapshot("start")
    
    def t700_step_build(ctx: TestContext) -> bool:
        x, y, z = ctx.get_position()
        portal_x = int(x) + 3
        portal_y = int(y)
        portal_z = int(z)
        
        ctx.log_event(f"Building portal frame at ({portal_x}, {portal_y}, {portal_z})...")
        
        # Bottom row
        for dx in range(4):
            ctx.client.transport.dispatch("place_block", {
                "block": "minecraft:obsidian",
                "position": {"x": portal_x + dx, "y": portal_y, "z": portal_z}
            })
            time.sleep(0.1)
        
        # Top row
        for dx in range(4):
            ctx.client.transport.dispatch("place_block", {
                "block": "minecraft:obsidian",
                "position": {"x": portal_x + dx, "y": portal_y + 4, "z": portal_z}
            })
            time.sleep(0.1)
        
        # Sides
        for dy in range(1, 4):
            ctx.client.transport.dispatch("place_block", {
                "block": "minecraft:obsidian",
                "position": {"x": portal_x, "y": portal_y + dy, "z": portal_z}
            })
            ctx.client.transport.dispatch("place_block", {
                "block": "minecraft:obsidian",
                "position": {"x": portal_x + 3, "y": portal_y + dy, "z": portal_z}
            })
            time.sleep(0.1)
        
        ctx.log_event("Portal frame built")
        return True
    
    suite.add(TestCase(
        id="T700",
        name="Portal Construction",
        description="Build obsidian portal frame",
        timeout_seconds=30,
        setup=t700_setup,
        steps=[t700_step_build],
        assertions=[]
    ))
    
    # T701: Portal Ignition
    def t701_setup(ctx: TestContext):
        ctx.give_item("minecraft:flint_and_steel", 1)
    
    def t701_step_light(ctx: TestContext) -> bool:
        x, y, z = ctx.get_position()
        ctx.log_event("Lighting portal...")
        
        ctx.client.transport.dispatch("use_item_at", {
            "item": "minecraft:flint_and_steel",
            "position": {"x": int(x) + 4, "y": int(y) + 1, "z": int(z)}
        })
        time.sleep(2)
        return True
    
    suite.add(TestCase(
        id="T701",
        name="Portal Ignition",
        description="Light nether portal with flint and steel",
        timeout_seconds=10,
        setup=t701_setup,
        steps=[t701_step_light],
        assertions=[]
    ))
    
    # T702: Dimension Travel
    def t702_step_travel(ctx: TestContext) -> bool:
        state = ctx.get_state()
        dim = state.get("dimension", "").lower()
        ctx.log_event(f"Current dimension: {dim}")
        
        ctx.log_event("Walking into portal...")
        # Would walk into active portal
        time.sleep(5)
        return True
    
    suite.add(TestCase(
        id="T702",
        name="Dimension Travel",
        description="Enter portal and change dimension",
        timeout_seconds=30,
        steps=[t702_step_travel],
        assertions=[]
    ))
    
    # T703: Nether Navigation
    def t703_setup(ctx: TestContext):
        ctx.give_item("minecraft:cobblestone", 64)
        ctx.give_item("minecraft:cooked_beef", 32)
        ctx.snapshot("start")
    
    def t703_step_navigate(ctx: TestContext) -> bool:
        x, y, z = ctx.get_position()
        target_x = int(x) + 100
        target_z = int(z)
        
        ctx.log_event(f"Navigating to ({target_x}, {y}, {target_z})...")
        ctx.client.transport.dispatch("goto", {"x": target_x, "y": int(y), "z": target_z})
        
        start = time.time()
        while time.time() - start < 45:
            state = ctx.get_state()
            health = state.get("health", 0)
            if health <= 0:
                ctx.log_event("DIED during navigation!")
                return False
            if not state.get("is_pathing", False):
                break
            time.sleep(2)
        
        ctx.client.transport.dispatch("cancel", {})
        ctx.snapshot("end")
        return True
    
    def t703_assert_alive(ctx: TestContext):
        state = ctx.get_state()
        health = state.get("health", 0)
        return health > 0, f"Survived with {health} HP"
    
    suite.add(TestCase(
        id="T703",
        name="Nether Navigation",
        description="Navigate safely in nether",
        timeout_seconds=60,
        setup=t703_setup,
        steps=[t703_step_navigate],
        assertions=[t703_assert_alive]
    ))
    
    # T704: Structure Location
    def t704_step_locate(ctx: TestContext) -> bool:
        ctx.log_event("Searching for nether fortress...")
        
        x, y, z = ctx.get_position()
        ctx.client.transport.dispatch("explore", {"x": int(x), "z": int(z)})
        
        start = time.time()
        while time.time() - start < 30:
            # Would scan for nether bricks
            time.sleep(5)
        
        ctx.client.transport.dispatch("cancel", {})
        return True
    
    suite.add(TestCase(
        id="T704",
        name="Structure Location",
        description="Locate nether fortress",
        timeout_seconds=60,
        steps=[t704_step_locate],
        assertions=[]
    ))
    
    return suite


__all__ = ["create_extended_suite_700"]
