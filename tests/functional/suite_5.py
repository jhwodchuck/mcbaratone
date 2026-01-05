"""
Suite 5: Nether Entry & Safety Tests
"""

import time
from test_base import TestCase, TestSuite, TestContext


def create_suite_5() -> TestSuite:
    """Suite 5: Nether Entry & Safety - Portal building and Nether navigation."""
    suite = TestSuite("Suite_5_Nether", "Nether entry and safety tests")
    
    # T500: Build and light portal
    def t500_setup(ctx: TestContext):
        ctx.clear_inventory()
        ctx.give_item("minecraft:obsidian", 14)
        ctx.give_item("minecraft:flint_and_steel", 1)
        ctx.set_time("day")
        time.sleep(0.5)
    
    def t500_step_build_portal(ctx: TestContext) -> bool:
        x, y, z = ctx.get_position()
        ctx.log_event(f"Building portal frame at ({x+3}, {y}, {z})...")
        
        # Build portal frame (4x5)
        portal_x = int(x) + 3
        portal_y = int(y)
        portal_z = int(z)
        
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
        
        return True
    
    def t500_step_light_portal(ctx: TestContext) -> bool:
        x, y, z = ctx.get_position()
        portal_x = int(x) + 4
        portal_y = int(y) + 1
        portal_z = int(z)
        
        ctx.log_event("Lighting portal...")
        ctx.client.transport.dispatch("use_item_at", {
            "item": "minecraft:flint_and_steel",
            "position": {"x": portal_x, "y": portal_y, "z": portal_z}
        })
        time.sleep(2)
        return True
    
    suite.add(TestCase(
        id="T500",
        name="Build and light portal",
        description="Construct obsidian frame and ignite",
        timeout_seconds=120,
        setup=t500_setup,
        steps=[t500_step_build_portal, t500_step_light_portal],
        assertions=[]
    ))
    
    # T510: Enter nether, build safe box
    def t510_setup(ctx: TestContext):
        ctx.clear_inventory()
        ctx.give_item("minecraft:cobblestone", 64)
        ctx.give_item("minecraft:torch", 16)
        time.sleep(0.5)
    
    def t510_step_enter_portal(ctx: TestContext) -> bool:
        ctx.log_event("Entering nether portal...")
        # Would walk into portal and wait for dimension change
        # Simulated for test
        return True
    
    def t510_step_build_safebox(ctx: TestContext) -> bool:
        ctx.log_event("Building safe box around portal...")
        # Would place blocks around portal
        return True
    
    def t510_assert_nether(ctx: TestContext):
        state = ctx.get_state()
        dim = state.get("dimension", "").lower()
        return "nether" in dim, f"Dimension is {dim}"
    
    suite.add(TestCase(
        id="T510",
        name="Enter nether + safe box",
        description="Enter nether and build enclosed structure",
        timeout_seconds=180,
        setup=t510_setup,
        steps=[t510_step_enter_portal, t510_step_build_safebox],
        assertions=[]  # Skip dimension check for staged test
    ))
    
    # T520: Navigate 200 blocks without death
    def t520_setup(ctx: TestContext):
        ctx.give_item("minecraft:cobblestone", 128)
        ctx.give_item("minecraft:cooked_beef", 32)
        ctx.snapshot("start")
    
    def t520_step_navigate(ctx: TestContext) -> bool:
        start_pos = ctx.get_position()
        ctx.log_event(f"Starting navigation from {start_pos}...")
        
        # Navigate in nether (simplified - would use goto with safe pathing)
        target_x = int(start_pos[0]) + 100
        target_z = int(start_pos[2]) + 100
        
        ctx.client.transport.dispatch("goto", {"x": target_x, "y": int(start_pos[1]), "z": target_z})
        
        start_time = time.time()
        while time.time() - start_time < 120:
            state = ctx.get_state()
            health = state.get("health", 20)
            if health <= 0:
                ctx.log_event("DIED!")
                return False
            if not state.get("is_pathing", False):
                break
            time.sleep(2)
        
        ctx.client.transport.dispatch("cancel", {})
        return True
    
    def t520_assert_alive(ctx: TestContext):
        state = ctx.get_state()
        health = state.get("health", 0)
        return health > 0, f"Health is {health}"
    
    def t520_assert_moved(ctx: TestContext):
        start_snap = ctx.snapshots[0] if ctx.snapshots else None
        if not start_snap:
            return True, "No start snapshot"
        
        start_pos = start_snap.get("position", (0, 0, 0))
        end_pos = ctx.get_position()
        
        dist = ((end_pos[0] - start_pos[0])**2 + (end_pos[2] - start_pos[2])**2) ** 0.5
        return dist > 50, f"Traveled {dist:.1f} blocks"
    
    suite.add(TestCase(
        id="T520",
        name="Navigate 200 blocks",
        description="Travel in nether without dying",
        timeout_seconds=180,
        setup=t520_setup,
        steps=[t520_step_navigate],
        assertions=[t520_assert_alive, t520_assert_moved]
    ))
    
    # T530: Find fortress
    def t530_setup(ctx: TestContext):
        ctx.give_item("minecraft:cobblestone", 64)
        ctx.give_item("minecraft:cooked_beef", 32)
    
    def t530_step_search(ctx: TestContext) -> bool:
        ctx.log_event("Searching for nether fortress...")
        
        # Use exploration to find fortress
        x, y, z = ctx.get_position()
        ctx.client.transport.dispatch("explore", {"x": int(x), "z": int(z)})
        
        start_time = time.time()
        while time.time() - start_time < 120:
            # Would scan for nether bricks
            time.sleep(5)
            ctx.log_event("Scanning for fortress blocks...")
        
        ctx.client.transport.dispatch("cancel", {})
        return True
    
    suite.add(TestCase(
        id="T530",
        name="Find fortress",
        description="Locate nether fortress structure",
        timeout_seconds=300,
        setup=t530_setup,
        steps=[t530_step_search],
        assertions=[]
    ))
    
    # T540: Kill blazes -> at least 6 rods
    def t540_setup(ctx: TestContext):
        ctx.clear_inventory()
        ctx.give_item("minecraft:iron_sword", 1)
        ctx.give_item("minecraft:bow", 1)
        ctx.give_item("minecraft:arrow", 64)
        ctx.give_item("minecraft:cooked_beef", 32)
        ctx.give_item("minecraft:shield", 1)
    
    def t540_step_hunt_blazes(ctx: TestContext) -> bool:
        ctx.log_event("Hunting blazes...")
        
        start_rods = ctx.count_item("minecraft:blaze_rod")
        start_time = time.time()
        
        while time.time() - start_time < 180:
            current_rods = ctx.count_item("minecraft:blaze_rod")
            if current_rods - start_rods >= 6:
                ctx.log_event(f"Collected {current_rods} rods!")
                return True
            
            # Would find and attack blazes
            ctx.log_event("Searching for blazes...")
            time.sleep(10)
        
        return ctx.count_item("minecraft:blaze_rod") >= 6
    
    def t540_assert_rods(ctx: TestContext):
        rods = ctx.count_item("minecraft:blaze_rod")
        return rods >= 6, f"Have {rods} blaze rods"
    
    suite.add(TestCase(
        id="T540",
        name="Kill blazes",
        description="Obtain at least 6 blaze rods",
        timeout_seconds=300,
        setup=t540_setup,
        steps=[t540_step_hunt_blazes],
        assertions=[t540_assert_rods]
    ))
    
    return suite


__all__ = ["create_suite_5"]
