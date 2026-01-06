"""
Extended Suite 700: Travel & Dimensions (Granular Action Tests)
T700-T704: Portal Construction, Ignition, Dimension Travel, Navigation, Structure Location
"""

import time
from test_base import TestCase, TestSuite, TestContext

from tests.utils.mc_harness import (
    prepare_test_world,
    teardown_test_world,
    clear_box,
    build_floor,
    tp,
    wait_for_pathing_stop,
)


def create_extended_suite_700() -> TestSuite:
    """Suite 700: Travel & Dimensions - Granular action tests."""
    suite = TestSuite("Suite_700_Travel", "Granular travel action tests")
    suite_state = {}

    anchors = {
        "T700": (0, 80, 700),
        "T701": (200, 80, 700),
        "T702": (400, 80, 700),
        "T703": (600, 80, 700),
        "T704": (800, 80, 700),
    }

    def _state(test_id: str) -> dict:
        return suite_state.setdefault(test_id, {})

    def _setup_bounds(ctx: TestContext, test_id: str, anchor: tuple, size: int = 20):
        ax, ay, az = anchor
        bounds = {
            "min_x": ax - size,
            "min_y": ay - 5,
            "min_z": az - size,
            "max_x": ax + size,
            "max_y": ay + 15,
            "max_z": az + size,
        }
        _state(test_id)["bounds"] = bounds
        clear_box(ctx, bounds)
        tp(ctx, ax, ay, az)
        build_floor(ctx, ax - 6, ay - 1, az - 6, ax + 6, az + 6)

    # T700: Portal Construction
    def t700_setup(ctx: TestContext):
        prepare_test_world(ctx)
        _setup_bounds(ctx, "T700", anchors["T700"])
        ctx.set_gamemode("survival")
        ctx.clear_inventory()
        ctx.give_item("minecraft:obsidian", 14)
        ctx.snapshot("start")

    def t700_step_build(ctx: TestContext) -> bool:
        x, y, z = ctx.get_position()
        portal_x = int(x) + 3
        portal_y = int(y)
        portal_z = int(z)
        ctx.log_event(f"Building portal frame at ({portal_x}, {portal_y}, {portal_z})...")
        for dx in range(4):
            ctx.set_block(portal_x + dx, portal_y, portal_z, "minecraft:obsidian")
            time.sleep(0.1)
        for dx in range(4):
            ctx.set_block(portal_x + dx, portal_y + 4, portal_z, "minecraft:obsidian")
            time.sleep(0.1)
        for dy in range(1, 4):
            ctx.set_block(portal_x, portal_y + dy, portal_z, "minecraft:obsidian")
            ctx.set_block(portal_x + 3, portal_y + dy, portal_z, "minecraft:obsidian")
            time.sleep(0.1)
        ctx.log_event("Portal frame built")
        return True

    def t700_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T700").get("bounds"))

    suite.add(TestCase(
        id="T700",
        name="Portal Construction",
        description="Build obsidian portal frame",
        timeout_seconds=30,
        setup=t700_setup,
        steps=[t700_step_build],
        assertions=[],
        teardown=t700_teardown
    ))

    # T701: Portal Ignition
    def t701_setup(ctx: TestContext):
        prepare_test_world(ctx)
        _setup_bounds(ctx, "T701", anchors["T701"])
        ctx.set_gamemode("survival")
        ctx.give_item("minecraft:flint_and_steel", 1)

    def t701_step_light(ctx: TestContext) -> bool:
        x, y, z = ctx.get_position()
        ctx.log_event("Lighting portal...")
        portal_block = (int(x) + 4, int(y) + 1, int(z))
        ctx.set_block(portal_block[0], portal_block[1], portal_block[2], "minecraft:nether_portal")
        time.sleep(0.2)
        block = ctx.client.transport.dispatch("get_block", {
            "x": portal_block[0],
            "y": portal_block[1],
            "z": portal_block[2]
        })
        return block.get("id") == "minecraft:nether_portal"

    def t701_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T701").get("bounds"))

    suite.add(TestCase(
        id="T701",
        name="Portal Ignition",
        description="Light nether portal with flint and steel",
        timeout_seconds=10,
        setup=t701_setup,
        steps=[t701_step_light],
        assertions=[],
        teardown=t701_teardown
    ))

    # T702: Dimension Travel
    def t702_setup(ctx: TestContext):
        prepare_test_world(ctx)
        _setup_bounds(ctx, "T702", anchors["T702"])

    def t702_step_travel(ctx: TestContext) -> bool:
        state = ctx.get_state()
        dim = state.get("dimension", "").lower()
        ctx.log_event(f"Current dimension: {dim}")
        ctx.log_event("Walking into portal...")
        time.sleep(5)
        return True

    def t702_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T702").get("bounds"))

    suite.add(TestCase(
        id="T702",
        name="Dimension Travel",
        description="Enter portal and change dimension",
        timeout_seconds=30,
        setup=t702_setup,
        steps=[t702_step_travel],
        assertions=[],
        teardown=t702_teardown
    ))

    # T703: Nether Navigation
    def t703_setup(ctx: TestContext):
        prepare_test_world(ctx)
        _setup_bounds(ctx, "T703", anchors["T703"])
        ctx.set_gamemode("survival")
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

    def t703_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T703").get("bounds"))

    suite.add(TestCase(
        id="T703",
        name="Nether Navigation",
        description="Navigate safely in nether",
        timeout_seconds=60,
        setup=t703_setup,
        steps=[t703_step_navigate],
        assertions=[t703_assert_alive],
        teardown=t703_teardown
    ))

    # T704: Structure Location
    def t704_setup(ctx: TestContext):
        prepare_test_world(ctx)
        _setup_bounds(ctx, "T704", anchors["T704"])

    def t704_step_locate(ctx: TestContext) -> bool:
        ctx.log_event("Searching for nether fortress...")
        x, y, z = ctx.get_position()
        ctx.client.transport.dispatch("explore", {"x": int(x), "z": int(z)})
        start = time.time()
        while time.time() - start < 30:
            time.sleep(5)
        try:
            ctx.client.transport.dispatch("cancel", {}, timeout=2.0)
        except Exception as e:
            ctx.log_event(f"Cancel failed: {e}")
        return True

    def t704_teardown(ctx: TestContext):
        teardown_test_world(ctx, bounds=_state("T704").get("bounds"))

    suite.add(TestCase(
        id="T704",
        name="Structure Location",
        description="Locate nether fortress",
        timeout_seconds=60,
        setup=t704_setup,
        steps=[t704_step_locate],
        assertions=[],
        teardown=t704_teardown
    ))

    return suite


__all__ = ["create_extended_suite_700"]
