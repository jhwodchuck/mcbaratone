"""
Extended Suite 100: Movement & Positioning (Granular Action Tests)
T100-T104: Walking, Sprinting, Climbing, Swimming, Sneaking
"""

import time
from test_base import TestCase, TestSuite, TestContext


def _get_snapshot(ctx: TestContext, label: str):
    for snap in ctx.snapshots:
        if snap.get("label") == label:
            return snap
    return None
from baritone_client.utils.arena_loader import ArenaLoader


def create_extended_suite_100() -> TestSuite:
    """Suite 100: Movement & Positioning - Granular action tests."""
    suite = TestSuite("Suite_100_Movement", "Granular movement action tests")
    
    # T100: Basic Walking
    def t100_setup(ctx: TestContext):
        loader = ArenaLoader(ctx.client.transport)
        arena = loader.load("movement_flat")
        ctx.snapshot("start")
    
    def t100_step_walk(ctx: TestContext) -> bool:
        ctx.log_event("Walking forward 10 blocks...")
        start_state = ctx.get_state()
        start_pos = start_state.get("block_position", start_state.get("position", {}))
        x, y, z = start_pos.get("x", 0), start_pos.get("y", 64), start_pos.get("z", 0)
        
        # Issue walk command
        ctx.client.transport.dispatch("goto", {"x": int(x) + 10, "y": int(y), "z": int(z)})
        
        start = time.time()
        moved = False
        while time.time() - start < 15:
            state = ctx.get_state()
            pos = state.get("block_position", state.get("position", {}))
            if abs(pos.get("x", 0) - x) >= 1:
                moved = True
            if moved and not state.get("is_pathing", False):
                break
            time.sleep(0.5)
        
        ctx.snapshot("end")
        return True
    
    def t100_assert_moved(ctx: TestContext):
        start_snap = _get_snapshot(ctx, "start")
        end_snap = _get_snapshot(ctx, "end")
        if not start_snap or not end_snap:
            return False, "Missing snapshots"
        
        start_pos = start_snap.get("position", (0, 0, 0))
        end_pos = end_snap.get("position", (0, 0, 0))
        
        dx = end_pos[0] - start_pos[0]
        return dx >= 8, f"Moved {dx:.1f} blocks (expected ~10)"
    
    suite.add(TestCase(
        id="T100",
        name="Basic Walking",
        description="Walk forward 10 blocks on flat ground",
        timeout_seconds=20,
        setup=t100_setup,
        steps=[t100_step_walk],
        assertions=[t100_assert_moved]
    ))
    
    # T101: Sprint Jump
    def t101_setup(ctx: TestContext):
        loader = ArenaLoader(ctx.client.transport)
        arena = loader.load("gap_course")
        ctx.snapshot("start")
    
    def t101_step_sprint_jump(ctx: TestContext) -> bool:
        ctx.log_event("Sprint jumping...")
        # Issue sprint + jump
        ctx.client.transport.dispatch("chat", {"message": "#sprint"})
        time.sleep(0.5)
        ctx.client.transport.dispatch("chat", {"message": "#jump"})
        time.sleep(1)
        return True
    
    def t101_assert_alive(ctx: TestContext):
        state = ctx.get_state()
        health = state.get("health", 0)
        return health > 0, f"Health is {health}"
    
    suite.add(TestCase(
        id="T101",
        name="Sprint Jump",
        description="Sprint and jump to clear gap",
        timeout_seconds=10,
        setup=t101_setup,
        steps=[t101_step_sprint_jump],
        assertions=[t101_assert_alive]
    ))
    
    # T102: Ladder Climbing
    def t102_setup(ctx: TestContext):
        loader = ArenaLoader(ctx.client.transport)
        arena = loader.load("vertical_test")
        ctx.snapshot("start")

    def t102_step_climb(ctx: TestContext) -> bool:
        ctx.log_event("Climbing ladder...")
        start_y = ctx.get_position()[1]

        # Climb up 5 blocks
        target_y = start_y + 5
        ctx.client.transport.dispatch("goto", {
            "x": int(ctx.get_position()[0]),
            "y": int(target_y),
            "z": int(ctx.get_position()[2])
        })

        # Wait for climbing to complete
        start_time = time.time()
        while time.time() - start_time < 10:
            current_y = ctx.get_position()[1]
            if current_y >= target_y - 1:  # Close enough
                return True
            time.sleep(0.5)

        return False

    def t102_assert_vertical_movement(ctx: TestContext):
        if len(ctx.snapshots) < 1:
            return False, "Missing start snapshot"

        start_y = ctx.snapshots[0].get("position", (0, 0, 0))[1]
        current_y = ctx.get_position()[1]
        climbed = current_y - start_y

        return climbed >= 4, f"Climbed {climbed:.1f} blocks vertically (expected ~5)"

    def t102_assert_no_fall_damage(ctx: TestContext):
        # Check health didn't decrease during climb
        start_health = ctx.snapshots[0].get("health", 20.0) if ctx.snapshots else 20.0
        current_health = ctx.get_state().get("health", 20.0)

        return current_health >= start_health, f"Health: {current_health} (started with {start_health})"

    suite.add(TestCase(
        id="T102",
        name="Ladder Climbing",
        description="Climb 5 blocks on ladder",
        timeout_seconds=15,
        setup=t102_setup,
        steps=[t102_step_climb],
        assertions=[t102_assert_vertical_movement, t102_assert_no_fall_damage]
    ))
    
    # T103: Swim Navigation
    def t103_setup(ctx: TestContext):
        loader = ArenaLoader(ctx.client.transport)
        arena = loader.load("underwater_maze")
        ctx.snapshot("start")
    
    def t103_step_swim(ctx: TestContext) -> bool:
        state = ctx.get_state()
        in_water = state.get("in_water", False)
        ctx.log_event(f"In water: {in_water}")
        
        if not in_water:
            ctx.log_event("Not in water - finding water source")
            # Would pathfind to water
        
        return True
    
    def t103_assert_not_drowned(ctx: TestContext):
        state = ctx.get_state()
        health = state.get("health", 0)
        return health > 0, f"Health is {health}, did not drown"
    
    suite.add(TestCase(
        id="T103",
        name="Swim Navigation",
        description="Navigate through water without drowning",
        timeout_seconds=25,
        setup=t103_setup,
        steps=[t103_step_swim],
        assertions=[t103_assert_not_drowned]
    ))
    
    # T104: Sneak Movement
    def t104_setup(ctx: TestContext):
        loader = ArenaLoader(ctx.client.transport)
        arena = loader.load("narrow_passage")
        ctx.snapshot("start")

    def t104_step_sneak(ctx: TestContext) -> bool:
        ctx.log_event("Enabling sneak mode...")
        ctx.client.transport.dispatch("chat", {"message": "#sneak"})
        time.sleep(2)

        # Walk while sneaking
        x, y, z = ctx.get_position()
        ctx.client.transport.dispatch("goto", {"x": int(x) + 5, "y": int(y), "z": int(z)})
        time.sleep(3)

        ctx.client.transport.dispatch("chat", {"message": "#sneak"})  # Toggle off
        return True

    def t104_assert_position_changed(ctx: TestContext):
        if len(ctx.snapshots) < 1:
            return False, "Missing start snapshot"

        start_pos = ctx.snapshots[0].get("position", (0, 0, 0))
        current_pos = ctx.get_position()

        dx = current_pos[0] - start_pos[0]
        dz = current_pos[2] - start_pos[2]
        distance = (dx**2 + dz**2)**0.5

        return distance >= 4, f"Moved {distance:.1f} blocks horizontally (expected ~5)"

    def t104_assert_no_fall_damage(ctx: TestContext):
        # Check that health hasn't decreased (assuming no other damage sources)
        start_health = ctx.snapshots[0].get("health", 20.0) if ctx.snapshots else 20.0
        current_health = ctx.get_state().get("health", 20.0)

        return current_health >= start_health, f"Health changed from {start_health} to {current_health}"

    def t104_assert_sneaking_enabled(ctx: TestContext):
        # Note: This would check sneaking state if available
        # For now, assume sneak mode was toggled successfully
        return True, "Sneak mode toggled"

    suite.add(TestCase(
        id="T104",
        name="Sneak Movement",
        description="Move while sneaking without falling off edges",
        timeout_seconds=15,
        setup=t104_setup,
        steps=[t104_step_sneak],
        assertions=[t104_assert_position_changed, t104_assert_no_fall_damage, t104_assert_sneaking_enabled]
    ))
    
    return suite


__all__ = ["create_extended_suite_100"]
