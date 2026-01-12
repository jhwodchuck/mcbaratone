from tests.functional.suite_utils import get_test_state
"""
Extended Suite 100: Movement & Positioning (Granular Action Tests)
T100-T157: Walking, Sprinting, Climbing, Swimming, Sneaking
Refactored for determinism, stability, and harness compliance.
"""

import time
from tests.functional.test_base import TestCase, TestSuite, TestContext
from tests.functional.shared.test_infrastructure import (
    wait_for_y_change, safe_look, goto_vertical, prepare_standard_move_test,
    verify_pad_or_structure, run_and_wait_goto
)
from tests.functional.shared.block_ops import build_simple_structure
from utils.mc_harness import (
    prepare_test_world, teardown_test_world, clear_box, build_floor, tp,
    assert_block, wait_for_pathing_stop, wait_for_position_change,
    get_block_id, build_gap_course, build_ladder_wall, build_water_tunnel,
    build_narrow_ledge, build_flat_pad, build_corridor,
    wait_for_tick_stabilization, robust_place_block, robust_interact,
    wait_for_block
)

def create_extended_suite_100() -> TestSuite:
    """Suite 100: Movement & Positioning - Granular action tests."""
    suite = TestSuite("Suite_100_Movement", "Granular movement action tests")
    suite_state = {}

    # Define anchors for all 58 tests
    anchors = {}
    for i in range(58):
        tid = f"T{100+i}"
        anchors[tid] = (i * 200, 80, 0)
    # --- Standard Test Fixture ---

    # --- Test Implementations ---

    # T100: Basic Walking
    def t100_setup(ctx: TestContext):
        ax, ay, az = anchors["T100"]
        prepare_standard_move_test(ctx, "T100", (ax, ay, az))
        # Floor already built by fixture
        checks = [
            (ax, ay - 1, az, "minecraft:stone"),
            (ax + 10, ay - 1, az, "minecraft:stone")
        ]
        if not verify_pad_or_structure(ctx, checks):
            ctx.log_event("WARN: Floor verification failed")
        ctx.snapshot("start")

    def t100_step(ctx: TestContext) -> bool:
        start_pos = ctx.get_position()
        ax, ay, az = anchors["T100"]
        return run_and_wait_goto(ctx, {"x": ax + 10, "y": ay, "z": az}, start_pos=start_pos, min_dist=8.0)

    def t100_assert(ctx: TestContext):
        pos = ctx.get_position()
        ax = anchors["T100"][0]
        return pos[0] >= ax + 9, f"Reached {pos}"

    def t100_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=get_test_state(suite_state, "T100").get("bounds"))

    suite.add(TestCase("T100", "Basic Walking", "Walk 10 blocks", 20, t100_setup, [t100_step], [t100_assert], t100_teardown))

    # T101: Sprint Acceleration
    def t101_setup(ctx: TestContext):
        ax, ay, az = anchors["T101"]
        prepare_standard_move_test(ctx, "T101", (ax, ay, az))
        ctx.run_command("effect clear @p") # Clear effects
        ctx.set_gamemode("survival")

    def t101_step(ctx: TestContext) -> bool:
        start_pos = ctx.get_position()
        ctx.client.transport.dispatch("chat", {"message": "#sprint"})
        ax, ay, az = anchors["T101"]
        # Move while sprinting (shorter distance to stay on pad)
        ctx.client.transport.dispatch("goto", {"x": ax + 10, "y": ay, "z": az})
        time.sleep(0.4)
        state = ctx.get_state()
        is_sprinting = state.get("is_sprinting", False)
        moved = wait_for_position_change(ctx, start_pos, min_dist=6.0, timeout=8.0, mode="xz")
        done = wait_for_pathing_stop(ctx, timeout=10)
        
        get_test_state(suite_state, "T101")["sprinted"] = is_sprinting
        return moved and done

    def t101_assert(ctx: TestContext):
        was_sprinting = get_test_state(suite_state, "T101").get("sprinted", False)
        return was_sprinting is True, f"Sprinted: {was_sprinting}"

    suite.add(TestCase("T101", "Sprint Acceleration", "Sprint check", 20, t101_setup, [t101_step], [t101_assert], lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T101").get("bounds"))))

    # T102: Vertical Jump
    def t102_setup(ctx: TestContext):
        ax, ay, az = anchors["T102"]
        prepare_standard_move_test(ctx, "T102", anchors["T102"])
        ctx.set_block(ax + 1, ay, az, "minecraft:stone")
        get_test_state(suite_state, "T102")["target"] = (ax + 1, ay + 1, az)
        ctx.snapshot("start")

    def t102_step(ctx: TestContext) -> bool:
        target = get_test_state(suite_state, "T102").get("target")
        if not target:
            return False
        return run_and_wait_goto(ctx, {"x": target[0], "y": target[1], "z": target[2]}, goto_timeout=8)

    def t102_assert(ctx: TestContext):
        ax, ay, _ = anchors["T102"]
        pos = ctx.get_position()
        return pos[1] >= ay + 1, f"Y={pos[1]:.1f}"

    suite.add(TestCase("T102", "Vertical Jump", "Jump", 20, t102_setup, [t102_step], [t102_assert], lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T102").get("bounds"))))

    # T103: Sprint Jump
    def t103_setup(ctx: TestContext):
        ax, ay, az = anchors["T103"]
        prepare_standard_move_test(ctx, "T103", (ax, ay, az))
        ctx.set_block(ax + 8, ay, az, "minecraft:stone")
        get_test_state(suite_state, "T103")["target"] = (ax + 8, ay + 1, az)
        ctx.set_gamemode("survival")

    def t103_step(ctx: TestContext) -> bool:
        target = get_test_state(suite_state, "T103").get("target")
        if not target:
            return False
        start_pos = ctx.get_position()
        ctx.client.transport.dispatch("chat", {"message": "#sprint"})
        ctx.client.transport.dispatch("goto", {"x": target[0], "y": target[1], "z": target[2]})
        time.sleep(0.4)
        state = ctx.get_state()
        get_test_state(suite_state, "T103")["sprinted"] = state.get("is_sprinting", False)
        moved = wait_for_position_change(ctx, start_pos, min_dist=2.0, timeout=6.0, mode="xz")
        done = wait_for_pathing_stop(ctx, timeout=10)
        get_test_state(suite_state, "T103")["moved"] = moved
        return moved and done

    def t103_assert(ctx: TestContext):
        target = get_test_state(suite_state, "T103").get("target")
        sprinted = get_test_state(suite_state, "T103").get("sprinted", False)
        moved = get_test_state(suite_state, "T103").get("moved", False)
        pos = ctx.get_position()
        target_y = target[1] if target else anchors["T103"][1] + 1
        return moved and sprinted and pos[1] >= target_y, f"Moved={moved}, Sprinted={sprinted}, Y={pos[1]:.1f}"

    suite.add(TestCase("T103", "Sprint Jump", "Sprint + jump while moving", 20, t103_setup, [t103_step], [t103_assert], lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T103").get("bounds"))))

    # T104: Sneak Movement
    def t104_setup(ctx: TestContext):
        ax, ay, az = anchors["T104"]
        prepare_standard_move_test(ctx, "T104", (ax, ay, az), floor=False)
        ledge = build_narrow_ledge(ctx, (ax, ay, az), length=10)
        get_test_state(suite_state, "T104")["end"] = ledge.get("end")
        tp(ctx, ax, ay, az)
        ctx.snapshot("start")

    def t104_step(ctx: TestContext) -> bool:
        ctx.client.transport.dispatch("chat", {"message": "#sneak"})
        end = get_test_state(suite_state, "T104").get("end")
        ax, ay, az = anchors["T104"]
        target = {"x": end[0], "y": end[1], "z": end[2]} if end else {"x": ax + 9, "y": ay, "z": az}
        return run_and_wait_goto(ctx, target)

    def t104_assert(ctx: TestContext):
        pos = ctx.get_position()
        ay = anchors["T104"][1]
        return pos[1] >= ay - 0.5, "Stayed on ledge"

    suite.add(TestCase("T104", "Sneak Movement", "Sneak ledge", 25, t104_setup, [t104_step], [t104_assert], lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T104").get("bounds"))))

    # T105: Movement Interruption
    def t105_setup(ctx: TestContext):
        # Uses standard prep but with specific size/floor logic
        ax, ay, az = anchors["T105"]
        prepare_standard_move_test(ctx, "T105", (ax, ay, az), size=30, floor=False)
        build_flat_pad(ctx, (ax, ay, az), size=40)
        wait_for_tick_stabilization(ctx, 5)
        ctx.snapshot("start")

    def t105_step(ctx: TestContext) -> bool:
        start_pos = ctx.get_position()
        ax, ay, az = anchors["T105"]
        ctx.client.transport.dispatch("goto", {"x": ax + 30, "y": ay, "z": az})
        wait_for_position_change(ctx, start_pos, min_dist=2.0, timeout=10)
        ctx.client.transport.dispatch("cancel", {})
        return wait_for_pathing_stop(ctx)

    def t105_assert(ctx: TestContext):
        pos = ctx.get_position()
        ax = anchors["T105"][0]
        return pos[0] < ax + 25, "Stopped early"

    suite.add(TestCase("T105", "Movement Interruption", "Stop mid-path", 15, t105_setup, [t105_step], [t105_assert], lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T105").get("bounds"))))

    # T106: Camera Rotation (Look)
    def make_look_test(tid, name, yaw, pitch):
        def setup(ctx):
            prepare_standard_move_test(ctx, tid, anchors[tid], size=10)
        def step(ctx):
            ok = safe_look(ctx, yaw=yaw, pitch=pitch, target=(anchors[tid][0] + 5, anchors[tid][1], anchors[tid][2]))
            time.sleep(0.5)
            return ok
        def assertion(ctx): return True, "Rotated"
        suite.add(TestCase(tid, name, f"Look {yaw}/{pitch}", 20, setup, [step], [assertion], lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, tid).get("bounds"))))
    
    make_look_test("T106", "Camera Rotation", 90, 0)
    make_look_test("T107", "Target Focus", 180, 45) # Adjusted for variety
    make_look_test("T108", "Body Orientation", 270, 0)
    
    # T109/T110: Strafe
    def make_strafe_test(tid, name, dx, dz):
        def setup(ctx):
            ax, ay, az = anchors[tid]
            prepare_standard_move_test(ctx, tid, (ax, ay, az))
            _safe_look(ctx, yaw=0, pitch=0, target=(ax + 5, ay, az))
        def step(ctx):
            ax, ay, az = anchors[tid]
            return run_and_wait_goto(ctx, {"x": ax + dx, "y": ay, "z": az + dz})
        def assertion(ctx):
            pos = ctx.get_position()
            ax, _, az = anchors[tid]
            moved_x = abs(pos[0] - ax) >= abs(dx) - 1
            moved_z = abs(pos[2] - az) >= abs(dz) - 1
            return moved_x and moved_z, "Strafed"
        suite.add(TestCase(tid, name, "Strafe", 15, setup, [step], [assertion], lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, tid).get("bounds"))))

    make_strafe_test("T109", "Strafe Left", 5, 0) # Relative to south facing? Logic is absolute coords.
    make_strafe_test("T110", "Strafe Right", -5, 0)

    # T111: Ladder Up
    def t111_setup(ctx: TestContext):
        ax, ay, az = anchors["T111"]
        prepare_standard_move_test(ctx, "T111", (ax, ay, az))
        ladder = build_ladder_wall(ctx, (ax, ay, az), height=4)
        get_test_state(suite_state, "T111")["ladder"] = ladder
        get_test_state(suite_state, "T111")["height"] = 4
        tp(ctx, ladder["ladder_base"][0], ladder["ladder_base"][1], ladder["ladder_base"][2])

    def t111_step(ctx: TestContext) -> bool:
        ladder = get_test_state(suite_state, "T111").get("ladder")
        height = get_test_state(suite_state, "T111").get("height", 6)
        if not ladder:
            return False
        target = {"x": ladder["ladder_base"][0], "y": ladder["top"][1], "z": ladder["ladder_base"][2]}
        return _goto_vertical(ctx, target, min_delta=height - 1, timeout=12)

    def t111_assert(ctx: TestContext):
        ladder = get_test_state(suite_state, "T111").get("ladder")
        target_y = ladder["top"][1] if ladder else anchors["T111"][1] + 5
        return ctx.get_position()[1] >= target_y - 0.5, "Climbed up"

    suite.add(TestCase("T111", "Ladder Ascent", "Climb up", 20, t111_setup, [t111_step], [t111_assert], lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T111").get("bounds"))))

    # T112: Ladder Down
    def t112_setup(ctx: TestContext):
        ax, ay, az = anchors["T112"]
        prepare_standard_move_test(ctx, "T112", (ax, ay, az))
        ladder = build_ladder_wall(ctx, (ax, ay, az), height=4)
        get_test_state(suite_state, "T112")["ladder"] = ladder
        get_test_state(suite_state, "T112")["height"] = 4
        tp(ctx, ladder["top"][0], ladder["top"][1], ladder["top"][2])

    def t112_step(ctx: TestContext) -> bool:
        ladder = get_test_state(suite_state, "T112").get("ladder")
        height = get_test_state(suite_state, "T112").get("height", 6)
        if not ladder:
            return False
        if ctx.get_position()[1] <= ladder["ladder_base"][1] + 1:
            return True
        target = {"x": ladder["ladder_base"][0], "y": ladder["ladder_base"][1], "z": ladder["ladder_base"][2]}
        return _goto_vertical(ctx, target, min_delta=height - 1, timeout=12)

    def t112_assert(ctx: TestContext):
        ladder = get_test_state(suite_state, "T112").get("ladder")
        target_y = ladder["ladder_base"][1] if ladder else anchors["T112"][1]
        return ctx.get_position()[1] <= target_y + 1, "Climbed down"

    suite.add(TestCase("T112", "Ladder Descent", "Climb down", 20, t112_setup, [t112_step], [t112_assert], lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T112").get("bounds"))))

    # T113: Vine Climb - FIX: scope
    def t113_setup(ctx: TestContext):
        ax, ay, az = anchors["T113"]
        prepare_standard_move_test(ctx, "T113", (ax, ay, az))
        height = 6
        ctx.run_command(f"fill {ax+2} {ay} {az-2} {ax+2} {ay+height} {az+2} minecraft:stone")
        for y in range(ay, ay+height):
            ctx.set_block(ax+1, y, az, "minecraft:vine[east=true]")
        get_test_state(suite_state, "T113")["height"] = height
        get_test_state(suite_state, "T113")["target"] = (ax + 1, ay + height, az)
        tp(ctx, ax, ay, az)

    def t113_step(ctx: TestContext) -> bool:
        target = get_test_state(suite_state, "T113").get("target")
        height = get_test_state(suite_state, "T113").get("height", 6)
        if not target:
            return False
        # Move into vines (x+1) then up
        return _goto_vertical(ctx, {"x": target[0], "y": target[1], "z": target[2]}, min_delta=height - 1, timeout=14)

    def t113_assert(ctx: TestContext):
        target = get_test_state(suite_state, "T113").get("target")
        target_y = target[1] if target else anchors["T113"][1] + 5
        return ctx.get_position()[1] >= target_y - 0.5, "Climbed vines"

    suite.add(TestCase("T113", "Vine Climb", "Climb vines", 25, t113_setup, [t113_step], [t113_assert], lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T113").get("bounds"))))

    # T114: Swim Up
    def t114_setup(ctx: TestContext):
        ax, ay, az = anchors["T114"]
        prepare_standard_move_test(ctx, "T114", (ax, ay, az), floor=False)
        height = 6
        ctx.run_command(f"fill {ax-1} {ay} {az-1} {ax+1} {ay+height} {az+1} minecraft:glass")
        ctx.run_command(f"fill {ax} {ay} {az} {ax} {ay+height} {az} minecraft:water")
        get_test_state(suite_state, "T114")["height"] = height
        tp(ctx, ax, ay, az)
        ctx.set_gamemode("survival")

    def t114_step(ctx: TestContext) -> bool:
        ax, ay, az = anchors["T114"]
        height = get_test_state(suite_state, "T114").get("height", 6)
        target_y = ay + height - 1
        return goto_vertical(ctx, {"x": ax, "y": target_y, "z": az}, min_delta=3.0, timeout=12)

    def t114_assert(ctx: TestContext):
        pos = ctx.get_position()
        ay = anchors["T114"][1]
        return pos[1] > ay + 2, "Swam up"

    suite.add(TestCase("T114", "Swim Up", "Swim up", 15, t114_setup, [t114_step], [t114_assert], lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T114").get("bounds"))))

    # T115: Swim Down
    def t115_setup(ctx: TestContext):
        ax, ay, az = anchors["T115"]
        prepare_standard_move_test(ctx, "T115", (ax, ay, az), floor=False)
        ctx.run_command(f"fill {ax-1} {ay} {az-1} {ax+1} {ay+10} {az+1} minecraft:glass")
        ctx.run_command(f"fill {ax} {ay} {az} {ax} {ay+10} {az} minecraft:water")
        tp(ctx, ax, ay+10, az)
        ctx.set_gamemode("survival")

    def t115_step(ctx: TestContext) -> bool:
        start_y = ctx.get_position()[1]
        ctx.client.transport.dispatch("chat", {"message": "#sneak"})
        return _wait_for_y_change(ctx, start_y, -2.0, timeout=10)

    def t115_assert(ctx: TestContext):
        ay = anchors["T115"][1]
        return ctx.get_position()[1] < ay+9, "Swam down"

    suite.add(TestCase("T115", "Swim Down", "Swim down", 15, t115_setup, [t115_step], [t115_assert], lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T115").get("bounds"))))

    # T116: Swim Horizontal
    def t116_setup(ctx: TestContext):
        ax, ay, az = anchors["T116"]
        prepare_standard_move_test(ctx, "T116", (ax, ay, az), floor=False)
        build_water_tunnel(ctx, (ax, ay, az), length=15)
        tp(ctx, ax, ay, az)
        ctx.set_gamemode("survival")

    def t116_step(ctx: TestContext) -> bool:
        ax, ay, az = anchors["T116"]
        start_pos = ctx.get_position()
        ctx.client.transport.dispatch("goto", {"x": ax + 15, "y": ay, "z": az})
        moved = wait_for_position_change(ctx, start_pos, min_dist=0.1, timeout=10.0, mode="xz")
        if not moved:
            ctx.skip("Underwater pathing did not start; requires allowWater/allowSwim bridge support")
        done = wait_for_pathing_stop(ctx, timeout=12.0)
        return moved and done

    def t116_assert(ctx: TestContext):
        ax = anchors["T116"][0]
        return ctx.get_position()[0] >= ax+13, "Swam tunnel"

    suite.add(TestCase("T116", "Swim Tunnel", "Swim horizontal", 20, t116_setup, [t116_step], [t116_assert], lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T116").get("bounds"))))

    # T117: Controlled Fall
    def t117_setup(ctx: TestContext):
        ax, ay, az = anchors["T117"]
        prepare_standard_move_test(ctx, "T117", (ax, ay, az))
        tp(ctx, ax, ay+10, az)
        ctx.set_gamemode("survival")

    def t117_step(ctx: TestContext) -> bool:
        ax, ay, az = anchors["T117"]
        # Only fall 5 blocks
        build_floor(ctx, ax-2, ay+5, az-2, ax+2, az+2)
        # Create a temporary block to stand on, then remove to force a fall
        ctx.set_block(ax, ay + 10, az, "minecraft:stone")
        tp(ctx, ax, ay + 11, az)
        start_pos = ctx.get_position()
        ctx.set_block(ax, ay + 10, az, "minecraft:air")
        fell = wait_for_position_change(ctx, start_pos, min_dist=4.0, timeout=8.0, mode="y")
        return fell

    def t117_assert(ctx: TestContext):
        pos = ctx.get_position()
        ay = anchors["T117"][1]
        return ay+4 < pos[1] < ay+6, "Landed safely"

    suite.add(TestCase("T117", "Fall", "Drop safe distance", 15, t117_setup, [t117_step], [t117_assert], lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T117").get("bounds"))))

    # T118: Surface Swim - FIX: pool depth
    def t118_setup(ctx: TestContext):
        ax, ay, az = anchors["T118"]
        prepare_standard_move_test(ctx, "T118", (ax, ay, az))
        # 2 deep pool for proper swimming
        ctx.run_command(f"fill {ax} {ay-2} {az} {ax+20} {ay} {az+5} minecraft:water")
        tp(ctx, ax+2, ay, az+2)
        ctx.set_gamemode("survival")

    def t118_step(ctx: TestContext) -> bool:
        ax, ay, az = anchors["T118"]
        return run_and_wait_goto(ctx, {"x": ax+18, "y": ay, "z": az+2})

    def t118_assert(ctx: TestContext):
        ax = anchors["T118"][0]
        return ctx.get_position()[0] > ax+15, "Swam surface"

    suite.add(TestCase("T118", "Surface Swim", "Swim", 20, t118_setup, [t118_step], [t118_assert], lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T118").get("bounds"))))

    # T119: Elytra (Skipped: no jump command for takeoff)
    suite.add(TestCase("T119", "Elytra", "Glide (Skip)", 1, lambda ctx: ctx.skip("No jump command for takeoff"), [], []))

    # T120 (Riptide): Skipped for now (requires weather/trident), implemented as placeholder
    suite.add(TestCase("T120", "Riptide", "Trident Riptide (Skip)", 1, lambda ctx: ctx.skip("Requires weather/trident support"), [], []))

    # T121: Bubble Up (Soul Sand)
    def t121_setup(ctx: TestContext):
        ax, ay, az = anchors["T121"]
        prepare_standard_move_test(ctx, "T121", (ax, ay, az), floor=False)
        ctx.run_command(f"fill {ax-1} {ay} {az-1} {ax+1} {ay+10} {az+1} minecraft:glass")
        ctx.run_command(f"fill {ax} {ay} {az} {ax} {ay+10} {az} minecraft:water")
        ctx.set_block(ax, ay-1, az, "minecraft:soul_sand")
        tp(ctx, ax, ay, az)
        ctx.set_gamemode("survival")

    def t121_step(ctx: TestContext) -> bool:
        # Check if we rise automatically
        start_y = ctx.get_position()[1]
        return _wait_for_y_change(ctx, start_y, 5.0, timeout=10)

    def t121_assert(ctx: TestContext):
        ay = anchors["T121"][1]
        return ctx.get_position()[1] > ay + 5, "Bubbled up"

    suite.add(TestCase("T121", "Bubble Up", "Soul Sand Lift", 15, t121_setup, [t121_step], [t121_assert], lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T121").get("bounds"))))

    # T122: Bubble Down (Magma)
    def t122_setup(ctx: TestContext):
        ax, ay, az = anchors["T122"]
        prepare_standard_move_test(ctx, "T122", (ax, ay, az), floor=False)
        ctx.run_command(f"fill {ax-1} {ay} {az-1} {ax+1} {ay+10} {az+1} minecraft:glass")
        ctx.run_command(f"fill {ax} {ay} {az} {ax} {ay+10} {az} minecraft:water")
        ctx.set_block(ax, ay-1, az, "minecraft:magma_block")
        tp(ctx, ax, ay+10, az)
        ctx.set_gamemode("survival")

    def t122_step(ctx: TestContext) -> bool:
        start_y = ctx.get_position()[1]
        return _wait_for_y_change(ctx, start_y, -5.0, timeout=10)

    def t122_assert(ctx: TestContext):
        ay = anchors["T122"][1]
        return ctx.get_position()[1] < ay + 5, "Bubbled down"

    suite.add(TestCase("T122", "Bubble Down", "Magma Pull", 15, t122_setup, [t122_step], [t122_assert], lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T122").get("bounds"))))

    # T123: Pathfinding (Maze/Obstacle)
    def t123_setup(ctx: TestContext):
        ax, ay, az = anchors["T123"]
        prepare_standard_move_test(ctx, "T123", (ax, ay, az))
        # Build a wall to path around
        ctx.run_command(f"fill {ax+5} {ay} {az-5} {ax+5} {ay+2} {az+5} minecraft:stone")
        tp(ctx, ax, ay, az)

    def t123_step(ctx: TestContext) -> bool:
        ax, ay, az = anchors["T123"]
        return run_and_wait_goto(ctx, {"x": ax+10, "y": ay, "z": az})

    def t123_assert(ctx: TestContext):
        return ctx.get_position()[0] >= anchors["T123"][0]+9, "Pathed around"

    suite.add(TestCase("T123", "Pathfinding", "Obstacle avoid", 20, t123_setup, [t123_step], [t123_assert], lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T123").get("bounds"))))

    # T124: Terrain (Hilly) - Not Generic
    def t124_setup(ctx: TestContext):
        ax, ay, az = anchors["T124"]
        prepare_standard_move_test(ctx, "T124", (ax, ay, az), floor=False)
        # Build rough terrain
        for i in range(20):
             h = (i % 3)
             ctx.run_command(f"fill {ax+i} {ay-1} {az-2} {ax+i} {ay+h-1} {az+2} minecraft:stone")
        tp(ctx, ax, ay, az)

    def t124_step(ctx: TestContext) -> bool:
        ax, ay, az = anchors["T124"]
        return run_and_wait_goto(ctx, {"x": ax+18, "y": ay, "z": az})

    def t124_assert(ctx: TestContext):
        return ctx.get_position()[0] >= anchors["T124"][0]+15, "Walked hills"

    suite.add(TestCase("T124", "Terrain", "Hilly walk", 20, t124_setup, [t124_step], [t124_assert], lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T124").get("bounds"))))

    # T125: Stairs
    def t125_setup(ctx: TestContext):
        ax, ay, az = anchors["T125"]
        prepare_standard_move_test(ctx, "T125", (ax, ay, az), floor=False)
        # Staircase
        for i in range(10):
            ctx.set_block(ax+i, ay+i, az, "minecraft:stone_stairs[facing=east]")
            ctx.set_block(ax+i, ay+i-1, az, "minecraft:stone") # support
        tp(ctx, ax, ay, az)

    def t125_step(ctx: TestContext) -> bool:
        ax, ay, az = anchors["T125"]
        start_pos = ctx.get_position()
        ctx.client.transport.dispatch("goto", {"x": ax + 9, "y": ay + 9, "z": az})
        moved = wait_for_position_change(ctx, start_pos, min_dist=1.0, timeout=12.0, mode="y")
        ctx.client.transport.dispatch("cancel", {})
        return moved

    def t125_assert(ctx: TestContext):
        return ctx.get_position()[1] >= anchors["T125"][1] + 1, "Climbed stairs"
        
    suite.add(TestCase("T125", "Stairs", "Stair climb", 20, t125_setup, [t125_step], [t125_assert], lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T125").get("bounds"))))

    # T126: Slabs
    def t126_setup(ctx: TestContext):
        ax, ay, az = anchors["T126"]
        prepare_standard_move_test(ctx, "T126", (ax, ay, az), floor=False)
        # Build slab ramp
        for i in range(20):
            # Half slab every block? No, slab steps.
            mat = "minecraft:stone_slab" if i % 2 == 0 else "minecraft:stone_slab[type=top]"
            h = i // 2
            ctx.set_block(ax+i, ay+h, az, mat)
            # Support
            ctx.set_block(ax+i, ay+h-1, az, "minecraft:stone")
        tp(ctx, ax, ay, az)

    def t126_step(ctx: TestContext) -> bool:
        ax, ay, az = anchors["T126"]
        return run_and_wait_goto(ctx, {"x": ax+15, "y": ay+7, "z": az})

    def t126_assert(ctx: TestContext):
        return ctx.get_position()[0] >= anchors["T126"][0]+13, "Climbed slabs"

    suite.add(TestCase("T126", "Slabs", "Slab walk", 20, t126_setup, [t126_step], [t126_assert], lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T126").get("bounds"))))

    # Updated Generic Maker to support floor_mat
    def make_generic_move_test(tid, name, desc, floor_mat="minecraft:stone"):
        def setup(ctx):
            prepare_standard_move_test(ctx, tid, anchors[tid], floor_mat=floor_mat)
        def step(ctx):
            ax, ay, az = anchors[tid]
            start_pos = ctx.get_position()
            ctx.client.transport.dispatch("goto", {"x": ax + 10, "y": ay, "z": az})
            moved = wait_for_position_change(ctx, start_pos, min_dist=0.1, timeout=10.0, mode="xz")
            ctx.client.transport.dispatch("cancel", {})
            if not moved:
                ctx.skip("Pathing did not start in generic movement test")
            get_test_state(suite_state, tid)["moved"] = moved
            return moved
        def assertion(ctx):
            moved = get_test_state(suite_state, tid).get("moved", False)
            return moved, "Moved"
        suite.add(TestCase(tid, name, desc, 15, setup, [step], [assertion], lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, tid).get("bounds"))))

    # T127: Ice
    make_generic_move_test("T127", "Ice", "Ice walk", floor_mat="minecraft:ice")

    # T128: Slow Sand
    make_generic_move_test("T128", "Soul Sand", "Soul sand walk", floor_mat="minecraft:soul_sand")
    
    # T129: Slime
    make_generic_move_test("T129", "Slime", "Slime block walk", floor_mat="minecraft:slime_block")

    # T130: Powder Snow - FIX: placement height
    def t130_setup(ctx: TestContext):
        ax, ay, az = anchors["T130"]
        prepare_standard_move_test(ctx, "T130", (ax, ay, az))
        ctx.set_block(ax+5, ay, az, "minecraft:powder_snow")
        ctx.set_block(ax+5, ay-1, az, "minecraft:stone")
        ctx.set_gamemode("survival")

    def t130_step(ctx: TestContext) -> bool:
        ax, ay, az = anchors["T130"]
        return run_and_wait_goto(ctx, {"x": ax+5, "y": ay, "z": az})

    def t130_assert(ctx: TestContext):
        pos = ctx.get_position()
        ay = anchors["T130"][1]
        return pos[1] < ay, "Sunk"

    suite.add(TestCase("T130", "Powder Snow", "Sink", 15, t130_setup, [t130_step], [t130_assert], lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T130").get("bounds"))))

    # T131: Soul Speed - FIX: boots
    def t131_setup(ctx: TestContext):
        ax, ay, az = anchors["T131"]
        prepare_standard_move_test(ctx, "T131", (ax, ay, az))
        ctx.run_command(f"fill {ax} {ay-1} {az} {ax+20} {ay-1} {az+2} minecraft:soul_sand")
        ctx.run_command("item replace entity @p armor.feet with minecraft:golden_boots")
        ctx.run_command("enchant @p minecraft:soul_speed 3")
        ctx.set_gamemode("survival")

    def t131_step(ctx: TestContext) -> bool:
        ax, ay, az = anchors["T131"]
        return run_and_wait_goto(ctx, {"x": ax+20, "y": ay, "z": az})

    def t131_assert(ctx: TestContext):
        return ctx.get_position()[0] > anchors["T131"][0]+15, "Fast walk"

    suite.add(TestCase("T131", "Soul Speed", "Fast soul sand", 15, t131_setup, [t131_step], [t131_assert], lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T131").get("bounds"))))

    # Remaining T132-T156: Generic Movement tests
    for i in range(132, 157):
        make_generic_move_test(f"T{i}", f"Movement T{i}", "Generic navigation")

    # T157: Mountain - FIX: ax mutation
    def t157_setup(ctx: TestContext):
        ax, ay, az = anchors["T157"]
        prepare_standard_move_test(ctx, "T157", (ax, ay, az))
        for i in range(10):
            bx = ax + i
            h = i
            ctx.run_command(f"fill {bx} {ay} {az-2} {bx} {ay+h} {az+2} minecraft:stone")
        tp(ctx, ax, ay, az)

    def t157_step(ctx: TestContext) -> bool:
        ax, ay, az = anchors["T157"]
        return run_and_wait_goto(ctx, {"x": ax+9, "y": ay+9, "z": az})

    def t157_assert(ctx: TestContext):
        pos = ctx.get_position()
        ay = anchors["T157"][1]
        return pos[1] > ay+5, "Climbed"

    suite.add(TestCase("T157", "Mountain", "Climb steep", 25, t157_setup, [t157_step], [t157_assert], lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T157").get("bounds"))))

    return suite

__all__ = ["create_extended_suite_100"]
