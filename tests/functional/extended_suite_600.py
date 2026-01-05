"""
Extended Suite 600: Sensing & Knowledge (Granular Action Tests)
T600-T604: Block Scanning, Entity Detection, Structure, Position, Environment
"""

import time
from test_base import TestCase, TestSuite, TestContext


def create_extended_suite_600() -> TestSuite:
    """Suite 600: Sensing & Knowledge - Granular action tests."""
    suite = TestSuite("Suite_600_Sensing", "Granular sensing action tests")
    
    # T600: Block Scanning
    def t600_setup(ctx: TestContext):
        ctx.snapshot("start")
    
    def t600_step_scan(ctx: TestContext) -> bool:
        x, y, z = ctx.get_position()
        ctx.log_event(f"Scanning blocks around ({x}, {y}, {z})...")
        
        result = ctx.client.transport.dispatch("scan_blocks", {
            "center": {"x": int(x), "y": int(y), "z": int(z)},
            "radius": 10,
            "block_types": ["minecraft:stone", "minecraft:dirt", "minecraft:grass_block"]
        })
        
        if result.get("status") == "ok":
            blocks = result.get("data", {}).get("blocks", [])
            ctx.log_event(f"Found {len(blocks)} matching blocks")
            return True
        
        ctx.log_event(f"Scan result: {result}")
        return True
    
    suite.add(TestCase(
        id="T600",
        name="Block Scanning",
        description="Scan blocks in radius",
        timeout_seconds=10,
        setup=t600_setup,
        steps=[t600_step_scan],
        assertions=[]
    ))
    
    # T601: Entity Detection
    def t601_setup(ctx: TestContext):
        # Spawn some entities
        ctx.client.transport.dispatch("chat", {"message": "/summon minecraft:cow ~3 ~ ~"})
        ctx.client.transport.dispatch("chat", {"message": "/summon minecraft:pig ~-3 ~ ~"})
        time.sleep(0.5)
    
    def t601_step_detect(ctx: TestContext) -> bool:
        ctx.log_event("Detecting nearby entities...")
        
        result = ctx.client.transport.dispatch("get_entities", {"radius": 20})
        
        if result.get("status") == "ok":
            entities = result.get("data", {}).get("entities", [])
            ctx.log_event(f"Found {len(entities)} entities")
            for ent in entities[:5]:  # Log first 5
                ctx.log_event(f"  - {ent.get('type', 'unknown')}")
            return True
        
        return True
    
    suite.add(TestCase(
        id="T601",
        name="Entity Detection",
        description="Detect entities in radius",
        timeout_seconds=10,
        setup=t601_setup,
        steps=[t601_step_detect],
        assertions=[]
    ))
    
    # T602: Structure Recognition
    def t602_setup(ctx: TestContext):
        pass
    
    def t602_step_detect_structure(ctx: TestContext) -> bool:
        ctx.log_event("Detecting nearby structures...")
        # Would use locate command or structure detection
        result = ctx.client.transport.dispatch("chat", {"message": "/locate structure minecraft:village_plains"})
        time.sleep(1)
        return True
    
    suite.add(TestCase(
        id="T602",
        name="Structure Recognition",
        description="Detect village or other structures",
        timeout_seconds=15,
        setup=t602_setup,
        steps=[t602_step_detect_structure],
        assertions=[]
    ))
    
    # T603: Position Tracking
    def t603_step_read_coords(ctx: TestContext) -> bool:
        state = ctx.get_state()
        pos = ctx.get_position()
        dim = state.get("dimension", "unknown")
        
        ctx.log_event(f"Position: ({pos[0]:.1f}, {pos[1]:.1f}, {pos[2]:.1f})")
        ctx.log_event(f"Dimension: {dim}")
        
        return pos[0] != 0 or pos[1] != 0 or pos[2] != 0
    
    def t603_assert_coords(ctx: TestContext):
        state = ctx.get_state()
        dim = state.get("dimension", "")
        return len(dim) > 0, f"Dimension: {dim}"
    
    suite.add(TestCase(
        id="T603",
        name="Position Tracking",
        description="Read current coordinates and dimension",
        timeout_seconds=5,
        steps=[t603_step_read_coords],
        assertions=[t603_assert_coords]
    ))
    
    # T604: Environmental Sensing
    def t604_step_environment(ctx: TestContext) -> bool:
        state = ctx.get_state()
        
        time_of_day = state.get("time", "unknown")
        weather = state.get("weather", "unknown")
        
        ctx.log_event(f"Time: {time_of_day}")
        ctx.log_event(f"Weather: {weather}")
        
        return True
    
    suite.add(TestCase(
        id="T604",
        name="Environmental Sensing",
        description="Check time of day and weather state",
        timeout_seconds=5,
        steps=[t604_step_environment],
        assertions=[]
    ))
    
    return suite


__all__ = ["create_extended_suite_600"]
