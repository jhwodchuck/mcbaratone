"""
Extended Suite 200: Block Interaction (Granular Action Tests)
T200-T204: Breaking, Placing, Doors, Containers, Buckets
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
    wait_for_block,
    wait_for_item_count,
    wait_for_item_decrease,
    select_hotbar_item,
    safe_inventory_click,
    quick_move_slot,
    get_screen,
)


def create_extended_suite_200() -> TestSuite:
    """Suite 200: Block Interaction - Granular action tests."""
    suite = TestSuite("Suite_200_Blocks", "Granular block interaction tests")
    suite_state = {}

    anchors = {
        "T200": (0, 80, 200),
        "T201": (200, 80, 200),
        "T202": (400, 80, 200),
        "T203": (600, 80, 200),
        "T204": (800, 80, 200),
    }

    def _state(test_id: str) -> dict:
        return suite_state.setdefault(test_id, {})

    def _door_is_open(block_data: dict):
        if not block_data:
            return None
        props = block_data.get("properties") or {}
        if "open" in props:
            return str(props["open"]).lower() == "true"
        block_id = block_data.get("id", "")
        if "open=true" in block_id:
            return True
        if "open=false" in block_id:
            return False
        return None

    # T200: Block Breaking
    def t200_setup(ctx: TestContext):
        prepare_test_world(ctx)
        ax, ay, az = anchors["T200"]
        bounds = {
            "min_x": ax - 12,
            "min_y": ay - 5,
            "min_z": az - 12,
            "max_x": ax + 12,
            "max_y": ay + 10,
            "max_z": az + 12,
        }
        _state("T200")["bounds"] = bounds
        clear_box(ctx, bounds)
        tp(ctx, ax, ay, az)

        ctx.clear_inventory()
        ctx.give_item("minecraft:stone_pickaxe", 1)
        build_floor(ctx, ax - 6, ay - 1, az - 6, ax + 6, az + 6)
        target = (ax + 1, ay + 1, az)
        ctx.set_block(target[0], target[1], target[2], "minecraft:stone")

        build_ok = all([
            assert_block(ctx, ax, ay - 1, az, "minecraft:stone"),
            assert_block(ctx, target[0], target[1], target[2], "minecraft:stone"),
        ])
        state = _state("T200")
        state["build_ok"] = build_ok
        state["target"] = target
        state["start_cobble"] = ctx.count_item("minecraft:cobblestone")

    def t200_step_break(ctx: TestContext) -> bool:
        state = _state("T200")
        if not state.get("build_ok"):
            ctx.log_event("Build verification failed in setup")
            return False
        target = state.get("target")
        if not target:
            return False
        ctx.client.transport.dispatch("break_block", {
            "x": target[0],
            "y": target[1],
            "z": target[2],
        })
        broken, _ = wait_for_block(ctx, target[0], target[1], target[2], "minecraft:air", timeout=10.0)
        state["broken"] = broken
        return broken

    def t200_assert_mined(ctx: TestContext):
        state = _state("T200")
        target = state.get("target")
        if not target:
            return False, "Missing target block"
        ok_block = assert_block(ctx, target[0], target[1], target[2], "minecraft:air")
        start_cobble = state.get("start_cobble", 0)
        got_cobble = wait_for_item_count(ctx, "minecraft:cobblestone", start_cobble + 1, timeout=4.0)
        count = ctx.count_item("minecraft:cobblestone")
        return ok_block and got_cobble, f"Cobblestone {count} (start {start_cobble})"

    def t200_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T200").get("bounds"))

    suite.add(TestCase(
        id="T200",
        name="Block Breaking",
        description="Break stone with pickaxe, receive cobblestone",
        timeout_seconds=20,
        setup=t200_setup,
        steps=[t200_step_break],
        assertions=[t200_assert_mined],
        teardown=t200_teardown
    ))

    # T201: Block Placement
    def t201_setup(ctx: TestContext):
        prepare_test_world(ctx)
        ax, ay, az = anchors["T201"]
        bounds = {
            "min_x": ax - 12,
            "min_y": ay - 5,
            "min_z": az - 12,
            "max_x": ax + 12,
            "max_y": ay + 10,
            "max_z": az + 12,
        }
        _state("T201")["bounds"] = bounds
        clear_box(ctx, bounds)
        tp(ctx, ax, ay, az)

        ctx.clear_inventory()
        ctx.give_item("minecraft:cobblestone", 64)
        ctx.client.transport.dispatch("select_slot", {"slot": 0})
        build_floor(ctx, ax - 6, ay - 1, az - 6, ax + 6, az + 6)
        target = (ax + 2, ay, az)
        ctx.set_block(target[0], target[1] - 1, target[2], "minecraft:stone")
        ctx.set_block(target[0], target[1], target[2], "minecraft:air")

        build_ok = all([
            assert_block(ctx, target[0], target[1] - 1, target[2], "minecraft:stone"),
            assert_block(ctx, target[0], target[1], target[2], "minecraft:air"),
        ])
        state = _state("T201")
        state["build_ok"] = build_ok
        state["target"] = target
        state["start_pos"] = ctx.get_position()
        state["start_cobble"] = ctx.count_item("minecraft:cobblestone")

    def t201_step_place(ctx: TestContext) -> bool:
        state = _state("T201")
        if not state.get("build_ok"):
            ctx.log_event("Build verification failed in setup")
            return False
        target = state.get("target")
        if not target:
            return False
        ctx.log_event(f"Placing block at ({target[0]}, {target[1]}, {target[2]})...")
        result = ctx.client.transport.dispatch("place_block", {
            "x": target[0],
            "y": target[1],
            "z": target[2],
        })
        if isinstance(result, dict) and result.get("error"):
            ctx.log_event(f"Place error: {result.get('error')}")
            return False
        placed, _ = wait_for_block(ctx, target[0], target[1], target[2], "minecraft:cobblestone", timeout=3.0)
        state["placed"] = placed
        return placed

    def t201_assert_placed(ctx: TestContext):
        state = _state("T201")
        target = state.get("target")
        start_pos = state.get("start_pos")
        start_cobble = state.get("start_cobble", 0)
        if not target or not start_pos:
            return False, "Missing target or start position"
        ok_block = assert_block(ctx, target[0], target[1], target[2], "minecraft:cobblestone")
        end_cobble = ctx.count_item("minecraft:cobblestone")
        if end_cobble >= start_cobble:
            wait_for_item_decrease(ctx, "minecraft:cobblestone", start_cobble, timeout=2.0)
            end_cobble = ctx.count_item("minecraft:cobblestone")
        delta = start_cobble - end_cobble
        moved = ((ctx.get_position()[0] - start_pos[0]) ** 2 +
                 (ctx.get_position()[1] - start_pos[1]) ** 2 +
                 (ctx.get_position()[2] - start_pos[2]) ** 2) ** 0.5
        return ok_block and delta >= 1 and moved < 1.0, f"delta={delta} moved={moved:.2f}"

    def t201_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T201").get("bounds"))

    suite.add(TestCase(
        id="T201",
        name="Block Placement",
        description="Place cobblestone at specific coordinates",
        timeout_seconds=15,
        setup=t201_setup,
        steps=[t201_step_place],
        assertions=[t201_assert_placed],
        teardown=t201_teardown
    ))

    # T202: Door Operation
    def t202_setup(ctx: TestContext):
        prepare_test_world(ctx)
        ax, ay, az = anchors["T202"]
        bounds = {
            "min_x": ax - 10,
            "min_y": ay - 5,
            "min_z": az - 8,
            "max_x": ax + 10,
            "max_y": ay + 10,
            "max_z": az + 8,
        }
        _state("T202")["bounds"] = bounds
        clear_box(ctx, bounds)
        tp(ctx, ax, ay, az)

        build_floor(ctx, ax - 4, ay - 1, az - 3, ax + 4, az + 3)
        door_pos = (ax + 1, ay, az)
        ctx.run_command(f"setblock {ax + 1} {ay} {az - 1} minecraft:stone")
        ctx.run_command(f"setblock {ax + 1} {ay + 1} {az - 1} minecraft:stone")
        ctx.run_command(f"setblock {ax + 1} {ay} {az + 1} minecraft:stone")
        ctx.run_command(f"setblock {ax + 1} {ay + 1} {az + 1} minecraft:stone")
        ctx.run_command(f"setblock {door_pos[0]} {door_pos[1]} {door_pos[2]} minecraft:oak_door[facing=east,half=lower]")
        ctx.run_command(f"setblock {door_pos[0]} {door_pos[1] + 1} {door_pos[2]} minecraft:oak_door[facing=east,half=upper]")

        build_ok = assert_block(ctx, door_pos[0], door_pos[1], door_pos[2], "minecraft:oak_door")
        state = _state("T202")
        state["build_ok"] = build_ok
        state["door_pos"] = door_pos
        state["pass_target"] = (ax + 2, ay, az)

    def t202_step_door(ctx: TestContext) -> bool:
        state = _state("T202")
        if not state.get("build_ok"):
            ctx.log_event("Build verification failed in setup")
            return False
        door_pos = state.get("door_pos")
        target = state.get("pass_target")
        if not door_pos or not target:
            return False

        before = _door_is_open(ctx.get_block(door_pos[0], door_pos[1], door_pos[2]))
        ctx.client.transport.dispatch("interact_block", {
            "x": door_pos[0],
            "y": door_pos[1],
            "z": door_pos[2],
        })
        time.sleep(0.4)
        after = _door_is_open(ctx.get_block(door_pos[0], door_pos[1], door_pos[2]))

        start_pos = ctx.get_position()
        ctx.client.transport.dispatch("goto", {"x": target[0], "y": target[1], "z": target[2]})
        moved = wait_for_position_change(ctx, start_pos, min_dist=1.5, timeout=5.0)
        wait_for_pathing_stop(ctx, timeout=6.0)
        ctx.client.transport.dispatch("cancel", {})

        ctx.client.transport.dispatch("interact_block", {
            "x": door_pos[0],
            "y": door_pos[1],
            "z": door_pos[2],
        })
        time.sleep(0.4)
        closed = _door_is_open(ctx.get_block(door_pos[0], door_pos[1], door_pos[2]))

        state["open_state_before"] = before
        state["open_state_after"] = after
        state["closed_state"] = closed
        state["moved_through"] = moved

        if before is not None and after is not None:
            return before != after
        return moved

    def t202_assert_toggle(ctx: TestContext):
        state = _state("T202")
        before = state.get("open_state_before")
        after = state.get("open_state_after")
        closed = state.get("closed_state")
        moved = state.get("moved_through", False)
        if before is not None and after is not None and closed is not None:
            toggled = (before != after) and (after != closed)
            return toggled, f"Door state before={before} open={after} closed={closed}"
        return moved, f"Moved through door: {moved}"

    def t202_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T202").get("bounds"))

    suite.add(TestCase(
        id="T202",
        name="Door Operation",
        description="Place door, open and close it",
        timeout_seconds=20,
        setup=t202_setup,
        steps=[t202_step_door],
        assertions=[t202_assert_toggle],
        teardown=t202_teardown
    ))

    # T203: Container Interaction
    def t203_setup(ctx: TestContext):
        prepare_test_world(ctx)
        ax, ay, az = anchors["T203"]
        bounds = {
            "min_x": ax - 10,
            "min_y": ay - 5,
            "min_z": az - 10,
            "max_x": ax + 10,
            "max_y": ay + 10,
            "max_z": az + 10,
        }
        _state("T203")["bounds"] = bounds
        clear_box(ctx, bounds)
        tp(ctx, ax, ay, az)

        build_floor(ctx, ax - 5, ay - 1, az - 5, ax + 5, az + 5)
        chest_pos = (ax + 1, ay, az)
        ctx.set_block(chest_pos[0], chest_pos[1], chest_pos[2], "minecraft:chest")
        build_ok = assert_block(ctx, chest_pos[0], chest_pos[1], chest_pos[2], "minecraft:chest")

        ctx.clear_inventory()
        ctx.give_item("minecraft:cobblestone", 64)

        state = _state("T203")
        state["build_ok"] = build_ok
        state["chest_pos"] = chest_pos
        state["start_cobble"] = ctx.count_item("minecraft:cobblestone")

    def t203_step_container(ctx: TestContext) -> bool:
        state = _state("T203")
        if not state.get("build_ok"):
            ctx.log_event("Build verification failed in setup")
            return False
        chest_pos = state.get("chest_pos")
        if not chest_pos:
            return False

        ctx.client.transport.dispatch("interact_block", {
            "x": chest_pos[0],
            "y": chest_pos[1],
            "z": chest_pos[2],
        })
        opened = False
        start = time.time()
        while time.time() - start < 2.5:
            if ctx.get_state().get("has_gui") and ctx.get_state().get("screen") != "none":
                opened = True
                break
            time.sleep(0.1)
        state["opened"] = opened
        if not opened:
            ctx.log_event("Chest did not open")
            return False

        screen = get_screen(ctx)
        screen_type = screen.get("type", "")
        screen_has_cobble = None
        slots = screen.get("slots")
        if isinstance(slots, list):
            screen_has_cobble = any(
                slot.get("id") == "minecraft:cobblestone" and slot.get("count", 0) > 0
                for slot in slots
            )

        inv = ctx.client.transport.dispatch("get_inventory", {})
        for item in inv.get("inventory", []):
            if item.get("id") == "minecraft:cobblestone" and item.get("count", 0) > 0:
                quick_move_slot(ctx, item["slot"])
                time.sleep(0.2)
                break

        screen_after = get_screen(ctx)
        slots = screen_after.get("slots")
        if isinstance(slots, list):
            screen_has_cobble = any(
                slot.get("id") == "minecraft:cobblestone" and slot.get("count", 0) > 0
                for slot in slots
            )

        ctx.client.transport.dispatch("close_screen", {})
        state["screen_type"] = screen_type
        state["screen_has_cobble"] = screen_has_cobble
        return True

    def t203_assert_container(ctx: TestContext):
        state = _state("T203")
        start_cobble = state.get("start_cobble", 0)
        end_cobble = ctx.count_item("minecraft:cobblestone")
        decreased = end_cobble < start_cobble
        screen_has_cobble = state.get("screen_has_cobble")
        screen_type = state.get("screen_type", "")
        if screen_has_cobble is not None:
            return decreased and screen_has_cobble, (
                f"cobble {end_cobble} (start {start_cobble}) screen_has_cobble={screen_has_cobble}"
            )
        opened = "chest" in screen_type.lower()
        return decreased and opened, (
            f"cobble {end_cobble} (start {start_cobble}) screen_type={screen_type}"
        )

    def t203_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T203").get("bounds"))

    suite.add(TestCase(
        id="T203",
        name="Container Interaction",
        description="Place and open chest, transfer items",
        timeout_seconds=20,
        setup=t203_setup,
        steps=[t203_step_container],
        assertions=[t203_assert_container],
        teardown=t203_teardown
    ))

    # T204: Bucket Operations
    def t204_setup(ctx: TestContext):
        prepare_test_world(ctx)
        ax, ay, az = anchors["T204"]
        bounds = {
            "min_x": ax - 8,
            "min_y": ay - 5,
            "min_z": az - 8,
            "max_x": ax + 12,
            "max_y": ay + 10,
            "max_z": az + 12,
        }
        _state("T204")["bounds"] = bounds
        clear_box(ctx, bounds)
        tp(ctx, ax, ay, az)

        build_floor(ctx, ax, ay - 1, az, ax + 4, az + 4)
        ctx.run_command(f"fill {ax} {ay} {az} {ax + 4} {ay + 2} {az} minecraft:stone")
        ctx.run_command(f"fill {ax} {ay} {az + 4} {ax + 4} {ay + 2} {az + 4} minecraft:stone")
        ctx.run_command(f"fill {ax} {ay} {az} {ax} {ay + 2} {az + 4} minecraft:stone")
        ctx.run_command(f"fill {ax + 4} {ay} {az} {ax + 4} {ay + 2} {az + 4} minecraft:stone")
        ctx.run_command(f"fill {ax + 1} {ay} {az + 1} {ax + 3} {ay + 1} {az + 3} air")

        water_blocks = [
            (ax + 2, ay, az + 2),
            (ax + 2, ay, az + 3),
            (ax + 3, ay, az + 2),
            (ax + 3, ay, az + 3),
        ]
        for wx, wy, wz in water_blocks:
            ctx.set_block(wx, wy, wz, "minecraft:water")

        build_ok = assert_block(ctx, water_blocks[0][0], water_blocks[0][1], water_blocks[0][2], "minecraft:water")

        ctx.clear_inventory()
        ctx.give_item("minecraft:bucket", 1)
        if not select_hotbar_item(ctx, "minecraft:bucket"):
            ctx.client.transport.dispatch("select_slot", {"slot": 0})

        state = _state("T204")
        state["build_ok"] = build_ok
        state["water_pos"] = water_blocks[0]

    def t204_step_fill(ctx: TestContext) -> bool:
        state = _state("T204")
        if not state.get("build_ok"):
            ctx.log_event("Build verification failed in setup")
            return False
        water_pos = state.get("water_pos")
        if not water_pos:
            return False
        if ctx.has_item("minecraft:water_bucket"):
            return True
        ctx.client.transport.dispatch("look_at", {
            "x": water_pos[0] + 0.5,
            "y": water_pos[1] + 0.5,
            "z": water_pos[2] + 0.5,
        })
        for _ in range(3):
            ctx.client.transport.dispatch("interact_block", {
                "x": water_pos[0],
                "y": water_pos[1],
                "z": water_pos[2],
            })
            time.sleep(0.4)
            if ctx.has_item("minecraft:water_bucket"):
                return True
            ctx.client.transport.dispatch("use_item", {"duration_ms": 250})
            time.sleep(0.6)
            if ctx.has_item("minecraft:water_bucket"):
                return True
        return False

    def t204_assert_bucket(ctx: TestContext):
        has = wait_for_item_count(ctx, "minecraft:water_bucket", 1, timeout=3.0)
        return has, "Has water bucket" if has else "No water bucket"

    def t204_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T204").get("bounds"))

    suite.add(TestCase(
        id="T204",
        name="Bucket Operations",
        description="Fill bucket from water source",
        timeout_seconds=20,
        setup=t204_setup,
        steps=[t204_step_fill],
        assertions=[t204_assert_bucket],
        teardown=t204_teardown
    ))

    return suite


__all__ = ["create_extended_suite_200"]
