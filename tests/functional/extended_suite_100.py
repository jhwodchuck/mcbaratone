"""
Extended Suite 100: Movement & Positioning (Granular Action Tests)
T100-T104: Walking, Sprinting, Climbing, Swimming, Sneaking
"""

import time
from test_base import TestCase, TestSuite, TestContext

from tests.utils.mc_harness import (
    prepare_test_world,
    teardown_test_world,
    clear_box,
    build_floor,
    tp,
    assert_block,
    wait_for_pathing_stop,
    wait_for_position_change,
    get_block_id,
    build_gap_course,
    build_ladder_wall,
    build_water_tunnel,
    build_narrow_ledge,
)


def create_extended_suite_100() -> TestSuite:
    """Suite 100: Movement & Positioning - Granular action tests."""
    suite = TestSuite("Suite_100_Movement", "Granular movement action tests")
    suite_state = {}

    anchors = {
        "T100": (0, 80, 0),
        "T101": (200, 80, 0),
        "T102": (400, 80, 0),
        "T103": (600, 80, 0),
        "T104": (800, 80, 0),
    }

    def _state(test_id: str) -> dict:
        return suite_state.setdefault(test_id, {})

    # T100: Basic Walking
    def t100_setup(ctx: TestContext):
        prepare_test_world(ctx)
        ax, ay, az = anchors["T100"]
        bounds = {
            "min_x": ax - 30,
            "min_y": ay - 5,
            "min_z": az - 30,
            "max_x": ax + 30,
            "max_y": ay + 15,
            "max_z": az + 30,
        }
        _state("T100")["bounds"] = bounds
        clear_box(ctx, bounds)
        tp(ctx, ax, ay, az)

        floor_min_x = ax - 20
        floor_max_x = ax + 19
        floor_min_z = az - 5
        floor_max_z = az + 4
        build_floor(ctx, floor_min_x, ay - 1, floor_min_z, floor_max_x, floor_max_z)

        build_ok = all([
            assert_block(ctx, floor_min_x, ay - 1, floor_min_z, "minecraft:stone"),
            assert_block(ctx, floor_max_x, ay - 1, floor_min_z, "minecraft:stone"),
            assert_block(ctx, floor_min_x, ay - 1, floor_max_z, "minecraft:stone"),
            assert_block(ctx, floor_max_x, ay - 1, floor_max_z, "minecraft:stone"),
            assert_block(ctx, ax, ay - 1, az, "minecraft:stone"),
        ])
        _state("T100")["build_ok"] = build_ok
        ctx.snapshot("start")

    def t100_step_walk(ctx: TestContext) -> bool:
        if not _state("T100").get("build_ok"):
            ctx.log_event("Build verification failed in setup")
            return False
        start_pos = ctx.get_position()
        ctx.client.transport.dispatch("goto", {
            "x": int(start_pos[0]) + 10,
            "y": int(start_pos[1]),
            "z": int(start_pos[2])
        })
        moved = wait_for_position_change(ctx, start_pos, min_dist=8.0, timeout=12.0)
        stopped = wait_for_pathing_stop(ctx, timeout=8.0)
        ctx.snapshot("end")
        return moved and stopped

    def t100_assert_moved(ctx: TestContext):
        start = next((s for s in ctx.snapshots if s.get("label") == "start"), None)
        end = next((s for s in ctx.snapshots if s.get("label") == "end"), None)
        if not start or not end:
            return False, "Missing snapshots"
        start_pos = start.get("position", (0, 0, 0))
        end_pos = end.get("position", (0, 0, 0))
        dx = end_pos[0] - start_pos[0]
        return dx >= 8, f"Moved {dx:.1f} blocks"

    def t100_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T100").get("bounds"))

    suite.add(TestCase(
        id="T100",
        name="Basic Walking",
        description="Walk forward 10 blocks on flat ground",
        timeout_seconds=20,
        setup=t100_setup,
        steps=[t100_step_walk],
        assertions=[t100_assert_moved],
        teardown=t100_teardown
    ))

    # T101: Sprint Jump
    def t101_setup(ctx: TestContext):
        prepare_test_world(ctx)
        ax, ay, az = anchors["T101"]
        bounds = {
            "min_x": ax - 20,
            "min_y": ay - 25,
            "min_z": az - 10,
            "max_x": ax + 30,
            "max_y": ay + 15,
            "max_z": az + 10,
        }
        _state("T101")["bounds"] = bounds
        clear_box(ctx, bounds)
        tp(ctx, ax, ay, az)

        course = build_gap_course(ctx, (ax, ay, az), gap_len=2, platform_len_a=5, platform_len_b=5, width=5)
        gap_start = course["gap_start"]
        gap_end = course["gap_end"]
        build_ok = all([
            assert_block(ctx, gap_start[0], gap_start[1], gap_start[2], "minecraft:air"),
            assert_block(ctx, gap_end[0], gap_end[1], gap_end[2], "minecraft:air"),
        ])
        state = _state("T101")
        state["build_ok"] = build_ok
        state["takeoff"] = course["takeoff"]
        state["landing"] = course["landing"]
        ctx.snapshot("start")

    def t101_step_sprint_jump(ctx: TestContext) -> bool:
        state = _state("T101")
        if not state.get("build_ok"):
            ctx.log_event("Build verification failed in setup")
            return False
        takeoff = state["takeoff"]
        landing = state["landing"]
        tp(ctx, takeoff[0], takeoff[1], takeoff[2])
        ctx.client.transport.dispatch("look_at", {
            "x": landing[0] + 0.5,
            "y": landing[1],
            "z": landing[2] + 0.5
        })
        ctx.client.transport.dispatch("chat", {"message": "#sprint"})
        time.sleep(0.2)
        ctx.client.transport.dispatch("goto", {"x": landing[0], "y": landing[1], "z": landing[2]})
        time.sleep(0.2)
        ctx.client.transport.dispatch("chat", {"message": "#jump"})
        moved = wait_for_position_change(ctx, takeoff, min_dist=3.0, timeout=6.0)
        wait_for_pathing_stop(ctx, timeout=6.0)
        ctx.snapshot("end")
        return moved

    def t101_assert_crossed(ctx: TestContext):
        pos = ctx.get_position()
        ax, ay, _ = anchors["T101"]
        crossed = pos[0] >= ax + 7
        safe_y = pos[1] >= ay - 5
        health = ctx.get_state().get("health", 0)
        return crossed and safe_y and health > 0, f"x={pos[0]:.1f} y={pos[1]:.1f} health={health}"

    def t101_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T101").get("bounds"))

    suite.add(TestCase(
        id="T101",
        name="Sprint Jump",
        description="Sprint and jump to clear a 2-block gap",
        timeout_seconds=15,
        setup=t101_setup,
        steps=[t101_step_sprint_jump],
        assertions=[t101_assert_crossed],
        teardown=t101_teardown
    ))

    # T102: Ladder Climbing
    def t102_setup(ctx: TestContext):
        prepare_test_world(ctx)
        ax, ay, az = anchors["T102"]
        bounds = {
            "min_x": ax - 10,
            "min_y": ay - 5,
            "min_z": az - 10,
            "max_x": ax + 10,
            "max_y": ay + 15,
            "max_z": az + 10,
        }
        _state("T102")["bounds"] = bounds
        clear_box(ctx, bounds)
        tp(ctx, ax, ay, az)

        ladder = build_ladder_wall(ctx, (ax, ay, az), height=6, facing="west")
        build_ok = assert_block(ctx, ladder["ladder_base"][0], ladder["ladder_base"][1], ladder["ladder_base"][2], "minecraft:ladder")
        state = _state("T102")
        state["build_ok"] = build_ok
        state["target"] = ladder["top"]
        ctx.snapshot("start")

    def t102_step_climb(ctx: TestContext) -> bool:
        state = _state("T102")
        if not state.get("build_ok"):
            ctx.log_event("Build verification failed in setup")
            return False
        start_y = ctx.get_position()[1]
        target = state["target"]
        tp(ctx, target[0], target[1] - 6, target[2])
        ctx.client.transport.dispatch("goto", {"x": target[0], "y": target[1], "z": target[2]})
        start = time.time()
        climbed = False
        while time.time() - start < 8.0:
            end_y = ctx.get_position()[1]
            if end_y - start_y >= 4:
                climbed = True
                break
            time.sleep(0.3)
        if not climbed:
            ctx.client.transport.dispatch("chat", {"message": f"#goto {target[0]} {target[1]} {target[2]}"})
            start = time.time()
            while time.time() - start < 6.0:
                end_y = ctx.get_position()[1]
                if end_y - start_y >= 4:
                    climbed = True
                    break
                time.sleep(0.3)
        wait_for_pathing_stop(ctx, timeout=8.0)
        return climbed

    def t102_assert_vertical_movement(ctx: TestContext):
        start = next((s for s in ctx.snapshots if s.get("label") == "start"), None)
        if not start:
            return False, "Missing start snapshot"
        start_y = start.get("position", (0, 0, 0))[1]
        current_y = ctx.get_position()[1]
        climbed = current_y - start_y
        health = ctx.get_state().get("health", 20.0)
        return climbed >= 4 and health >= 20.0, f"Climbed {climbed:.1f}, health {health}"

    def t102_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T102").get("bounds"))

    suite.add(TestCase(
        id="T102",
        name="Ladder Climbing",
        description="Climb a ladder to a raised platform",
        timeout_seconds=20,
        setup=t102_setup,
        steps=[t102_step_climb],
        assertions=[t102_assert_vertical_movement],
        teardown=t102_teardown
    ))

    # T103: Swimming
    def t103_setup(ctx: TestContext):
        prepare_test_world(ctx)
        ax, ay, az = anchors["T103"]
        bounds = {
            "min_x": ax - 5,
            "min_y": ay - 10,
            "min_z": az - 6,
            "max_x": ax + 30,
            "max_y": ay + 10,
            "max_z": az + 6,
        }
        _state("T103")["bounds"] = bounds
        clear_box(ctx, bounds)
        tp(ctx, ax, ay, az)

        tunnel = build_water_tunnel(ctx, (ax, ay, az), length=20, width=3, height=3, add_exit_air=True)
        build_ok = assert_block(ctx, tunnel["start"][0], tunnel["start"][1], tunnel["start"][2], "minecraft:water")
        ctx.run_command("effect give @p minecraft:water_breathing 20 0 true")
        state = _state("T103")
        state["build_ok"] = build_ok
        state["start"] = tunnel["start"]
        state["end"] = tunnel["end"]
        tp(ctx, tunnel["start"][0], tunnel["start"][1], tunnel["start"][2])
        ctx.snapshot("start")

    def t103_step_swim(ctx: TestContext) -> bool:
        state = _state("T103")
        if not state.get("build_ok"):
            ctx.log_event("Build verification failed in setup")
            return False
        start_pos = ctx.get_position()
        target = state["end"]
        ctx.client.transport.dispatch("chat", {"message": "#set allowWater true"})
        ctx.client.transport.dispatch("goto", {"x": target[0], "y": target[1], "z": target[2], "radius": 1})
        moved = wait_for_position_change(ctx, start_pos, min_dist=8.0, timeout=6.0)
        if not moved:
            ctx.client.transport.dispatch("chat", {"message": f"#goto {target[0]} {target[1]} {target[2]}"})
            moved = wait_for_position_change(ctx, start_pos, min_dist=8.0, timeout=6.0)
        in_water = False
        start = time.time()
        while time.time() - start < 10.0:
            pos = ctx.get_position()
            block = get_block_id(ctx, int(pos[0]), int(pos[1]), int(pos[2]))
            if block == "minecraft:water":
                in_water = True
            if abs(pos[0] - start_pos[0]) >= 10 and not ctx.get_state().get("is_pathing", False):
                break
            time.sleep(0.3)
        state["in_water"] = in_water
        ctx.snapshot("end")
        return moved and in_water

    def t103_assert_swim(ctx: TestContext):
        start = next((s for s in ctx.snapshots if s.get("label") == "start"), None)
        end = next((s for s in ctx.snapshots if s.get("label") == "end"), None)
        if not start or not end:
            return False, "Missing snapshots"
        start_pos = start.get("position", (0, 0, 0))
        end_pos = end.get("position", (0, 0, 0))
        dx = end_pos[0] - start_pos[0]
        health = ctx.get_state().get("health", 0)
        return dx >= 10 and _state("T103").get("in_water") and health > 0, f"dx={dx:.1f} health={health}"

    def t103_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T103").get("bounds"))

    suite.add(TestCase(
        id="T103",
        name="Swim Navigation",
        description="Swim through a water tunnel without drowning",
        timeout_seconds=25,
        setup=t103_setup,
        steps=[t103_step_swim],
        assertions=[t103_assert_swim],
        teardown=t103_teardown
    ))

    # T104: Sneak Movement
    def t104_setup(ctx: TestContext):
        prepare_test_world(ctx)
        ax, ay, az = anchors["T104"]
        bounds = {
            "min_x": ax - 10,
            "min_y": ay - 25,
            "min_z": az - 5,
            "max_x": ax + 20,
            "max_y": ay + 10,
            "max_z": az + 5,
        }
        _state("T104")["bounds"] = bounds
        clear_box(ctx, bounds)
        tp(ctx, ax, ay, az)

        ledge = build_narrow_ledge(ctx, (ax, ay, az), length=6, drop=20)
        build_ok = assert_block(ctx, ax, ledge["floor_y"], az, "minecraft:stone")
        state = _state("T104")
        state["build_ok"] = build_ok
        state["start"] = ledge["start"]
        tp(ctx, ledge["start"][0], ledge["start"][1], ledge["start"][2])
        ctx.snapshot("start")

    def t104_step_sneak(ctx: TestContext) -> bool:
        state = _state("T104")
        if not state.get("build_ok"):
            ctx.log_event("Build verification failed in setup")
            return False
        ctx.client.transport.dispatch("chat", {"message": "#sneak"})
        time.sleep(0.2)
        pos = ctx.get_position()
        ctx.client.transport.dispatch("goto", {
            "x": int(pos[0]) + 5,
            "y": int(pos[1]),
            "z": int(pos[2])
        })
        moved = wait_for_position_change(ctx, pos, min_dist=4.0, timeout=8.0)
        wait_for_pathing_stop(ctx, timeout=6.0)
        ctx.client.transport.dispatch("chat", {"message": "#sneak"})
        ctx.snapshot("end")
        return moved

    def t104_assert_safe(ctx: TestContext):
        start = next((s for s in ctx.snapshots if s.get("label") == "start"), None)
        end = next((s for s in ctx.snapshots if s.get("label") == "end"), None)
        if not start or not end:
            return False, "Missing snapshots"
        start_pos = start.get("position", (0, 0, 0))
        end_pos = end.get("position", (0, 0, 0))
        dx = end_pos[0] - start_pos[0]
        y_ok = end_pos[1] >= start_pos[1] - 3
        health = ctx.get_state().get("health", 20.0)
        return dx >= 4 and y_ok and health >= 20.0, f"dx={dx:.1f} y={end_pos[1]:.1f} health={health}"

    def t104_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T104").get("bounds"))

    suite.add(TestCase(
        id="T104",
        name="Sneak Movement",
        description="Move across a narrow ledge while sneaking",
        timeout_seconds=20,
        setup=t104_setup,
        steps=[t104_step_sneak],
        assertions=[t104_assert_safe],
        teardown=t104_teardown
    ))

    return suite


__all__ = ["create_extended_suite_100"]
