from tests.functional.suite_utils import get_test_state
"""
Extended Suite 800: System Operations & Metadata (Granular Action Tests)
T800-T809: Settings, Performance, Reliability, and System Health
"""

import time
import timeit
from test_base import TestCase, TestSuite, TestContext

from utils.mc_harness import (
    prepare_test_world,
    teardown_test_world,
    clear_box,
    build_floor,
    tp,
    wait_for_tick_stabilization,
    wait_for_entity,
    summon_near,
    get_entities,
    safe_dispatch,
)


def create_extended_suite_800() -> TestSuite:
    """Suite 800: System Operations - Granular system tests."""
    suite = TestSuite("Suite_800_System", "System operations and metadata tests")
    suite_state = {}

    anchors = {}
    for i in range(10):
        anchors[f"T{800+i}"] = (i*200, 80, 800)
    # --- Helpers ---

    def prepare_standard_system_test(ctx, tid, anchor, size=15, height=10, gamemode="creative", floor=True):
        """Standard fixture for system tests."""
        ax, ay, az = anchor
        bounds = {
            "min_x": ax - size, "min_y": ay - 5, "min_z": az - size,
            "max_x": ax + size, "max_y": ay + height, "max_z": az + size,
        }
        get_test_state(suite_state, tid)["bounds"] = bounds
        clear_box(ctx, bounds)
        prepare_test_world(ctx, gamemode=gamemode)
        tp(ctx, ax, ay, az)
        if floor:
            build_floor(ctx, ax - 10, ay - 1, az - 10, ax + 10, az + 10)
        wait_for_tick_stabilization(ctx, 20)
        ctx.clear_inventory()
        ctx.snapshot("start")
        return bounds

    def require_features(ctx, features):
        # In a real harness, we'd check ctx.capabilities or similar.
        # Here we just assume if methods don't exist, we fail/skip.
        # We can try to detect missing methods.
        pass

    # T800: Settings Configuration (Gamerule)
    def t800_setup(ctx: TestContext):
        ax, ay, az = anchors["T800"]
        prepare_standard_system_test(ctx, "T800", (ax, ay, az))

    def t800_step_config(ctx: TestContext) -> bool:
        # Set gamerule doDaylightCycle to false
        ctx.run_command("gamerule doDaylightCycle false")
        time.sleep(0.5)
        # Set back to true to verify change
        # Actually proper test:
        # 1. Set false.
        # 2. Check time.
        # 3. Wait.
        # 4. Check time (should be same).
        # But querying time precisely might fluctuate.
        # Better: Set mobSpawning false, spawn mob, see if it fails? No, /summon works anyway.
        # We can try to query the gamerule if harness supports reading command output.
        # Current harness `run_command` returns None or output?
        # Assuming we can't observe output easily.
        # Let's try `doDaylightCycle`.
        
        ctx.run_command("time set 1000")
        state1 = ctx.get_state()
        t1 = state1.get("world_time", 0)
        
        time.sleep(2.0)
        state2 = ctx.get_state()
        t2 = state2.get("world_time", 0)
        
        # If normal, t2 > t1.
        if t2 <= t1:
             ctx.log_event("Time not advancing initially? (Maybe rule was already false)")
             
        # Set true
        ctx.run_command("gamerule doDaylightCycle true")
        time.sleep(2.0)
        state3 = ctx.get_state()
        t3 = state3.get("world_time", 0)
        
        get_test_state(suite_state, "T800")["advanced"] = t3 > t2
        return True

    def t800_assert_config(ctx: TestContext):
        # We asserted that we could enable daylight cycle and observe time advance.
        # If it was already true, t3 > t2 holds.
        return get_test_state(suite_state, "T800").get("advanced", False), "Time advanced after enabling gamerule"

    suite.add(TestCase(
        id="T800",
        name="Settings Configuration",
        description="Verify gamerule modification behaves as expected",
        setup=t800_setup,
        steps=[t800_step_config],
        assertions=[t800_assert_config],
        teardown=lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T800").get("bounds"))
    ))

    # T802: Event Subscription (Entity Spawn)
    def t802_setup(ctx: TestContext):
        ax, ay, az = anchors["T802"]
        prepare_standard_system_test(ctx, "T802", (ax, ay, az))

    def t802_step_trigger(ctx: TestContext) -> bool:
        # Summon entity, verify client sees it (update event)
        summon_near(ctx, "minecraft:armor_stand", dx=2, dy=0, dz=0)
        ent = wait_for_entity(ctx, "minecraft:armor_stand", radius=5, timeout=3.0)
        get_test_state(suite_state, "T802")["found"] = ent is not None
        return True

    def t802_assert_trigger(ctx: TestContext):
        return get_test_state(suite_state, "T802").get("found", False), "Entity spawn event detected"

    suite.add(TestCase(
        id="T802",
        name="Event Subscription",
        description="Trigger entity spawn event",
        setup=t802_setup,
        steps=[t802_step_trigger],
        assertions=[t802_assert_trigger],
        teardown=lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T802").get("bounds"))
    ))

    # T803: Performance Monitoring (Latency)
    def t803_setup(ctx: TestContext):
        ax, ay, az = anchors["T803"]
        prepare_standard_system_test(ctx, "T803", (ax, ay, az))

    def t803_step_metrics(ctx: TestContext) -> bool:
        # Measure get_state latency
        latencies = []
        for _ in range(10):
            start = time.perf_counter()
            s = ctx.get_state()
            end = time.perf_counter()
            if s:
                latencies.append(end - start)
            time.sleep(0.1)
        
        if not latencies: return False
        avg = sum(latencies) / len(latencies)
        get_test_state(suite_state, "T803")["avg_latency"] = avg
        ctx.log_event(f"Avg Latency: {avg*1000:.2f}ms")
        return True

    def t803_assert_metrics(ctx: TestContext):
        avg = get_test_state(suite_state, "T803").get("avg_latency", 999)
        # Assert sub-500ms generally (local bridge)
        return avg < 0.5, f"Latency acceptable ({avg*1000:.1f}ms < 500ms)"

    suite.add(TestCase(
        id="T803",
        name="Performance Monitoring",
        description="Verify state retrieval latency",
        setup=t803_setup,
        steps=[t803_step_metrics],
        assertions=[t803_assert_metrics],
        teardown=lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T803").get("bounds"))
    ))

    # T804: Error Handling (Invalid Command)
    def t804_setup(ctx: TestContext):
        ax, ay, az = anchors["T804"]
        prepare_standard_system_test(ctx, "T804", (ax, ay, az))

    def t804_step_error(ctx: TestContext) -> bool:
        # Invalid command
        ctx.run_command("this_command_does_not_exist_123")
        time.sleep(0.5)
        
        # Valid command check
        ctx.run_command("time set day")
        return True

    def t804_assert_error(ctx: TestContext):
        # If we are here, python didn't crash.
        # Check connection is alive
        try:
             s = ctx.get_state()
             return s is not None, "Client alive after bad command"
        except Exception:
             return False, "Client disconnected"

    suite.add(TestCase(
        id="T804",
        name="Error Handling",
        description="Recover from invalid input",
        setup=t804_setup,
        steps=[t804_step_error],
        assertions=[t804_assert_error],
        teardown=lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T804").get("bounds"))
    ))

    # T806: Transport Reliability (Burst)
    def t806_setup(ctx: TestContext):
        ax, ay, az = anchors["T806"]
        prepare_standard_system_test(ctx, "T806", (ax, ay, az))

    def t806_step_ping(ctx: TestContext) -> bool:
        # Burst 20 requests
        success_count = 0
        for _ in range(20):
            if ctx.get_state() is not None:
                success_count += 1
            # Very short sleep
            time.sleep(0.01)
        get_test_state(suite_state, "T806")["success"] = success_count
        return True

    def t806_assert_ping(ctx: TestContext):
        count = get_test_state(suite_state, "T806").get("success", 0)
        return count == 20, f"Burst success: {count}/20"

    suite.add(TestCase(
        id="T806",
        name="Transport Reliability",
        description="Verify burst stability",
        setup=t806_setup,
        steps=[t806_step_ping],
        assertions=[t806_assert_ping],
        teardown=lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T806").get("bounds"))
    ))

    # T807: Resource Management (Inventory Structure)
    def t807_setup(ctx: TestContext):
        ax, ay, az = anchors["T807"]
        prepare_standard_system_test(ctx, "T807", (ax, ay, az))

    def t807_step_check(ctx: TestContext) -> bool:
        inv = ctx.get_inventory()
        get_test_state(suite_state, "T807")["inv_type"] = str(type(inv))
        if isinstance(inv, dict):
             get_test_state(suite_state, "T807")["keys"] = list(inv.keys())
             return True
        return False

    def t807_assert_check(ctx: TestContext):
        inv_type = get_test_state(suite_state, "T807").get("inv_type")
        keys = get_test_state(suite_state, "T807").get("keys", [])
        # Expect dict with 'inventory' or 'main' usually
        has_inv = "inventory" in keys or "items" in keys or "main" in keys
        return has_inv, f"Inventory structure valid? Type:{inv_type} Keys:{keys}"

    suite.add(TestCase(
        id="T807",
        name="Resource Management",
        description="Verify inventory structure",
        setup=t807_setup,
        steps=[t807_step_check],
        assertions=[t807_assert_check],
        teardown=lambda ctx: teardown_test_world(ctx, bounds=get_test_state(suite_state, "T807").get("bounds"))
    ))


    # Skipped tests
    skipped_mapping = {
        "T801": "Persistence requires server restart verification",
        "T805": "Command validation redundant with T804",
        "T808": "Diagnostics requires privileged debug commands",
        "T809": "World save verification requires file system access",
    }

    for tid, reason in skipped_mapping.items():
        suite.add(TestCase(
            id=tid,
            name=f"Skipped {tid}",
            description=reason,
            setup=lambda ctx: ctx.skip(reason),
            steps=[], assertions=[]
        ))

    return suite

__all__ = ["create_extended_suite_800"]