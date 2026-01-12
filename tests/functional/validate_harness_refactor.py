"""
Validation script for harness refactor.
Tests new robust primitives and failure modes.
"""

import time
from test_base import TestCase, TestSuite, TestContext
from utils.mc_harness import (
    prepare_test_world, teardown_test_world, do_goto, robust_interact_block,
    wait_for_gui_open, get_block_id, tp, build_floor, robust_place_block
)

def create_harness_validation_suite() -> TestSuite:
    suite = TestSuite("Harness_Validation", "Verify harness refactor primitives")
    suite_state = {}

    def _state(tid): return suite_state.setdefault(tid, {})

    # Scenario A: Pathing Timeout (No Premature Stop)
    def test_pathing_timeout_setup(ctx):
        prepare_test_world(ctx)
        tp(ctx, 0, 80, 0)
        build_floor(ctx, -10, 79, -10, 50, 80) # Floor to run on

    def test_pathing_timeout_step(ctx):
        # Go far away with short timeout
        # Expected: Returns False because timeout < time needed
        result = do_goto(ctx, {"x": 40, "y": 80, "z": 0}, timeout=1.0)
        _state("VAL01")["result"] = result
        return True # Step itself "passes" if it runs, assertion checks result

    def test_pathing_timeout_assert(ctx):
        result = _state("VAL01").get("result")
        return result is False, f"Expected do_goto to fail (timeout), got {result}"

    suite.add(TestCase("VAL01", "Pathing Timeout", "Verify do_goto fails on timeout", 
                       setup=test_pathing_timeout_setup, 
                       steps=[test_pathing_timeout_step], 
                       assertions=[test_pathing_timeout_assert]))

    # Scenario B: Arrival Failure (Blocked Path)
    def test_blocked_setup(ctx):
        prepare_test_world(ctx)
        tp(ctx, 100, 80, 0)
        build_floor(ctx, 90, 79, -10, 150, 80)
        # Wall at 110
        ctx.run_command("fill 110 80 -10 110 85 10 minecraft:stone")

    def test_blocked_step(ctx):
        # Try to go through wall
        result = do_goto(ctx, {"x": 120, "y": 80, "z": 0}, timeout=5.0)
        _state("VAL02")["result"] = result
        return True

    def test_blocked_assert(ctx):
        result = _state("VAL02").get("result")
        return result is False, f"Expected do_goto to fail (blocked), got {result}"

    suite.add(TestCase("VAL02", "Blocked Path", "Verify do_goto fails if blocked",
                       setup=test_blocked_setup,
                       steps=[test_blocked_step],
                       assertions=[test_blocked_assert]))

    # Scenario C: Robust Interaction (GUI)
    def test_gui_setup(ctx):
        prepare_test_world(ctx)
        tp(ctx, 200, 80, 0)
        build_floor(ctx, 195, 79, -5, 205, 80)
        robust_place_block(ctx, 202, 80, 0, "minecraft:chest")

    def test_gui_step(ctx):
        # Robust interact block wrapper
        return robust_interact_block(ctx, 202, 80, 0)

    def test_gui_assert(ctx):
        return wait_for_gui_open(ctx, timeout=1.0), "GUI loaded"

    suite.add(TestCase("VAL03", "Robust GUI", "Verify robust_interact_block detects GUI",
                       setup=test_gui_setup,
                       steps=[test_gui_step],
                       assertions=[test_gui_assert]))

    return suite
