"""
Test Base - Foundation for functional tests.
Provides test harness, action contracts, and utilities.
"""

import sys
import os
import json
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Any, Optional, Tuple, Union
from enum import Enum

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from baritone_client import Client, TcpTransport
from baritone_client.transport.enums import TransportEvent


def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _print_status(message: str) -> None:
    print(f"[{_ts()}] {message}", flush=True)


class DummyTransport:
    """Mock transport for functional tests."""
    def __init__(self):
        self.events = None
        self.dispatched = []
        self.state = {
            "block_position": {"x": 0, "y": 64, "z": 0},
            "position": {"x": 0.0, "y": 64.0, "z": 0.0},
            "health": 20.0,
            "hunger": 20,
            "dimension": "overworld",
            "world_time": 1000,
            "game_mode": "creative"
        }
        self.inventory = [
            {"id": "minecraft:oak_log", "count": 64, "slot": 0},
            {"id": "minecraft:oak_planks", "count": 64, "slot": 1},
            {"id": "minecraft:stick", "count": 64, "slot": 2},
            {"id": "minecraft:crafting_table", "count": 1, "slot": 3},
            {"id": "minecraft:wooden_pickaxe", "count": 1, "slot": 4},
            {"id": "minecraft:stone_pickaxe", "count": 1, "slot": 5},
            {"id": "minecraft:furnace", "count": 1, "slot": 6},
            {"id": "minecraft:cooked_beef", "count": 64, "slot": 7},
            {"id": "minecraft:diamond", "count": 1, "slot": 8},
            {"id": "minecraft:iron_ingot", "count": 64, "slot": 9},
            {"id": "minecraft:water_bucket", "count": 1, "slot": 10},
            {"id": "minecraft:shield", "count": 1, "slot": 11},
            {"id": "minecraft:bow", "count": 1, "slot": 12},
            {"id": "minecraft:arrow", "count": 64, "slot": 13},
            {"id": "minecraft:obsidian", "count": 10, "slot": 14},
            {"id": "minecraft:flint_and_steel", "count": 1, "slot": 15},
            {"id": "minecraft:ender_pearl", "count": 12, "slot": 16},
            {"id": "minecraft:blaze_powder", "count": 12, "slot": 17},
            {"id": "minecraft:eye_of_ender", "count": 12, "slot": 18},
            {"id": "minecraft:bed", "count": 1, "slot": 19},
            {"id": "minecraft:stone", "count": 64, "slot": 20},
        ]
        self.world_blocks = {}  # (x,y,z) -> block_id

    def _simulate_movement(self, direction, distance):
        """Simulate player movement."""
        pos = self.state["position"]
        if "forward" in direction.lower():
            pos["z"] += distance
        elif "backward" in direction.lower():
            pos["z"] -= distance
        elif "left" in direction.lower():
            pos["x"] -= distance
        elif "right" in direction.lower():
            pos["x"] += distance
        elif "up" in direction.lower():
            pos["y"] += distance
        elif "down" in direction.lower():
            pos["y"] -= distance

        # Update block position
        self.state["block_position"] = {
            "x": int(pos["x"]),
            "y": int(pos["y"]),
            "z": int(pos["z"])
        }

    def _consume_item(self, item_id, count=1):
        """Simulate consuming items from inventory."""
        for slot in self.inventory:
            if slot["id"] == item_id and slot["count"] >= count:
                slot["count"] -= count
                if slot["count"] <= 0:
                    slot["count"] = 0
                return True
        return False

    def dispatch(self, route, payload, **kwargs):
        """Mock dispatch that returns appropriate responses and updates state."""
        self.dispatched.append((route, payload))

        if route == "get_state":
            return {"status": "ok", "data": self.state.copy()}
        elif route == "get_inventory":
            return {"status": "ok", "data": {"inventory": self.inventory.copy()}}
        elif route == "goto":
            # Direct movement to coordinates
            try:
                x = float(payload.get("x", 0))
                y = float(payload.get("y", 64))
                z = float(payload.get("z", 0))
                self.state["position"] = {"x": x, "y": y, "z": z}
                self.state["block_position"] = {"x": int(x), "y": int(y), "z": int(z)}
                return {"status": "ok", "data": "Moving"}
            except ValueError:
                return {"status": "error", "message": "Invalid coordinates"}
        elif route == "chat":
            message = payload.get("message", "")
            # Simulate movement commands
            if message.startswith("goto "):
                # Simulate moving to coordinates
                parts = message.split()
                if len(parts) >= 4:
                    try:
                        x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
                        self.state["position"] = {"x": x, "y": y, "z": z}
                        self.state["block_position"] = {"x": int(x), "y": int(y), "z": int(z)}
                    except ValueError:
                        pass
            elif message.startswith("/tp"):
                # Teleport
                parts = message.split()
                if len(parts) >= 4:
                    try:
                        x, y, z = float(parts[2]), float(parts[3]), float(parts[4])
                        self.state["position"] = {"x": x, "y": y, "z": z}
                        self.state["block_position"] = {"x": int(x), "y": int(y), "z": int(z)}
                    except (ValueError, IndexError):
                        pass
            elif message.startswith("/give"):
                # Add items
                parts = message.split()
                if len(parts) >= 3:
                    try:
                        item_id = f"minecraft:{parts[2]}"
                        count = int(parts[3]) if len(parts) > 3 else 1
                        # Add to inventory
                        for slot in self.inventory:
                            if slot["id"] == item_id:
                                slot["count"] += count
                                break
                        else:
                            self.inventory.append({"id": item_id, "count": count, "slot": len(self.inventory)})
                    except (ValueError, IndexError):
                        pass
            elif message.startswith("/clear"):
                # Clear inventory
                self.inventory = []
            elif message.startswith("/effect give"):
                # Simulate health effects
                if "instant_health" in message:
                    self.state["health"] = min(20.0, self.state["health"] + 10)
            elif message.startswith("/time set"):
                self.state["world_time"] = 1000  # Reset to day
            return {"status": "ok", "message": message}
        elif route == "craft":
            # Simulate crafting consuming materials
            recipe = payload.get("recipe", "")
            if "planks" in recipe:
                self._consume_item("minecraft:oak_log", 1)
            elif "stick" in recipe:
                self._consume_item("minecraft:oak_planks", 2)
            elif "crafting_table" in recipe:
                self._consume_item("minecraft:oak_planks", 4)
            elif "wooden_pickaxe" in recipe:
                self._consume_item("minecraft:oak_planks", 3)
                self._consume_item("minecraft:stick", 2)
            elif "stone_pickaxe" in recipe:
                self._consume_item("minecraft:cobblestone", 3)
                self._consume_item("minecraft:stick", 2)
            elif "furnace" in recipe:
                self._consume_item("minecraft:cobblestone", 8)
            elif "iron_pickaxe" in recipe:
                self._consume_item("minecraft:iron_ingot", 3)
                self._consume_item("minecraft:stick", 2)
            return {"status": "ok", "data": {"crafted": True, "output_slot": 0}}
        elif route == "smelt":
            # Simulate smelting consuming fuel
            self._consume_item("minecraft:oak_log", 1)  # Fuel
            return {"status": "ok", "data": {"smelted": True, "output_count": 1}}
        elif route == "check_craft":
            return {"status": "ok", "data": {"can_craft": True, "missing": [], "recipe_id": "test"}}
        elif route == "get_block":
            # Dummy block response
            return {"status": "ok", "data": {"id": "minecraft:air", "properties": {}}}
        elif route == "find_blocks":
            return {"status": "ok", "data": {"found": []}}
        else:
            return {"status": "ok", "route": route, "payload": payload}

    def subscribe(self, event, callback):
        pass

    def shutdown(self):
        pass


class FunctionalResult(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"
    TIMEOUT = "TIMEOUT"


@dataclass
class ActionContract:
    """Defines expected behavior for an action."""
    name: str
    preconditions: List[Callable[['TestContext'], bool]] = field(default_factory=list)
    timeout_seconds: int = 30
    fail_reasons: List[str] = field(default_factory=list)
    
    def check_preconditions(self, ctx: 'TestContext') -> Tuple[bool, str]:
        """Check all preconditions. Returns (passed, reason)."""
        for i, check in enumerate(self.preconditions):
            try:
                if not check(ctx):
                    return False, f"Precondition {i} failed"
            except Exception as e:
                return False, f"Precondition {i} error: {e}"
        return True, "OK"


# Import TestContext from utils (assuming tests/ is in path)
# If not, we rely on the runner setting pythonpath
# ...
try:
    from tests.utils.mc_harness.context import TestContext, SkipTest
except ImportError:
    # Fallback for direct execution when the repository root is not importable.
    sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
    from utils.mc_harness.context import TestContext, SkipTest


@dataclass
class FunctionalCase:
    """A single functional test case."""
    id: str
    name: str
    description: str
    timeout_seconds: int = 120
    
    # Setup
    setup: Optional[Callable[['TestContext'], None]] = None
    
    # Test steps (ordered list of actions returning bool)
    steps: List[Callable[['TestContext'], bool]] = field(default_factory=list)
    
    # Assertions (list of checks returning (bool, message))
    assertions: List[Callable[['TestContext'], Tuple[bool, str]]] = field(default_factory=list)
    
    # Teardown (optional cleanup)
    teardown: Optional[Callable[['TestContext'], None]] = None
    
    def run(self, ctx: TestContext) -> Tuple[FunctionalResult, str, List[str]]:
        """
        Run the test case.
        Returns (result, message, events).
        """
        ctx.start_time = time.time()
        ctx.events = []
        ctx.snapshots = []

        try:
            _print_status(f"TEST START {self.id}: {self.name}")
            # ... (prep code)

            # Setup
            if self.setup:
                _print_status(f"SETUP START {self.id}")
                ctx.log_event(f"SETUP: {self.id}")
                self.setup(ctx)
                time.sleep(0.5)
                _print_status(f"SETUP END {self.id}")

            ctx.snapshot("after_setup")

            # Execute steps
            for i, step in enumerate(self.steps):
                if time.time() - ctx.start_time > self.timeout_seconds:
                    _print_status(f"TIMEOUT {self.id} at step {i}")
                    return TestResult.TIMEOUT, f"Timeout at step {i}", ctx.events
                
                # Check dead
                if ctx.get_state().get("is_dead"):
                    # ...
                    pass

                _print_status(f"STEP {i} START {self.id}")
                ctx.log_event(f"STEP {i}: executing")
                try:
                    step_start = time.time()
                    result = step(ctx)
                    if not result:
                        ctx.snapshot(f"step_{i}_failed")
                        state_dump = str(ctx.get_state())[:200]
                        ctx.log_event(f"FAILURE STATE: {state_dump}...")
                        _print_status(f"STEP {i} FAIL {self.id}")
                        return TestResult.FAIL, f"Step {i} returned False", ctx.events
                    step_elapsed = time.time() - step_start
                    _print_status(f"STEP {i} OK {self.id} ({step_elapsed:.1f}s)")
                except SkipTest as e:
                    ctx.log_event(f"SKIPPED: {e}")
                    _print_status(f"STEP {i} SKIP {self.id}: {e}")
                    return TestResult.SKIP, str(e), ctx.events
                except Exception as e:
                    ctx.log_event(f"STEP {i} ERROR: {e}")
                    import traceback
                    ctx.log_event(f"TRACE: {traceback.format_exc()}")
                    _print_status(f"STEP {i} ERROR {self.id}: {e}")
                    return TestResult.FAIL, f"Step {i} error: {e}", ctx.events
            
            ctx.snapshot("after_steps")
            
            # Check assertions
            for i, assertion in enumerate(self.assertions):
                _print_status(f"ASSERT {i} START {self.id}")
                try:
                    passed, msg = assertion(ctx)
                except SkipTest as e:
                    ctx.log_event(f"ASSERT {i} SKIPPED: {e}")
                    _print_status(f"ASSERT {i} SKIP {self.id}: {e}")
                    return TestResult.SKIP, str(e), ctx.events
                except Exception as e:
                    ctx.snapshot(f"assertion_{i}_error")
                    ctx.log_event(f"ASSERT {i} ERROR: {e}")
                    _print_status(f"ASSERT {i} ERROR {self.id}: {e}")
                    return TestResult.FAIL, f"Assertion {i} error: {e}", ctx.events

                if not passed:
                    ctx.snapshot(f"assertion_{i}_failed")
                    message = msg or f"Assertion {i} returned False"
                    ctx.log_event(f"ASSERT {i} FAILED: {message}")
                    _print_status(f"ASSERT {i} FAIL {self.id}: {message}")
                    return TestResult.FAIL, message, ctx.events

                ctx.log_event(f"ASSERT {i} PASSED: {msg or 'OK'}")
                _print_status(f"ASSERT {i} OK {self.id}")
            
            _print_status(f"TEST PASS {self.id}")
            return TestResult.PASS, "All steps and assertions passed", ctx.events
            
        except SkipTest as e:
            ctx.log_event(f"SKIPPED: {e}")
            _print_status(f"TEST SKIP {self.id}: {e}")
            return TestResult.SKIP, str(e), ctx.events
        except Exception as e:
            import traceback
            ctx.log_event(f"CRASH: {e}")
            ctx.log_event(f"TRACE: {traceback.format_exc()}")
            _print_status(f"TEST ERROR {self.id}: {e}")
            return TestResult.FAIL, f"Unexpected error: {e}", ctx.events
        
        finally:
            # Teardown
            if self.teardown:
                try:
                    _print_status(f"TEARDOWN START {self.id}")
                    self.teardown(ctx)
                    _print_status(f"TEARDOWN END {self.id}")
                except:
                    pass
            # Avoid admin-only commands in shared environments.


class FunctionalSuite:
    """Collection of related functional test cases."""
    
    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.tests: List[FunctionalCase] = []
    
    def add(self, test: FunctionalCase):
        self.tests.append(test)
    
    def run_all(self, ctx: TestContext) -> Dict[str, Tuple[FunctionalResult, str]]:
        """Run all tests in suite. Returns dict of test_id -> (result, message)."""
        results = {}
        _print_status(f"SUITE START {self.name}")
        for test in self.tests:
            print(f"\n>>> Running {test.id}: {test.name}")
            result, msg, events = test.run(ctx)
            results[test.id] = (result, msg)
            
            status = "PASS" if result == FunctionalResult.PASS else "FAIL" if result == FunctionalResult.FAIL else "SKIP"
            print(f"{status} {test.id}: {result.value} - {msg}")
            
            if result != FunctionalResult.PASS and events:
                print("  Events:")
                for e in events[-5:]:  # Last 5 events
                    print(f"    {e}")

        _print_status(f"SUITE END {self.name}")
        return results


# Aliases for backward compatibility
TestCase = FunctionalCase
TestSuite = FunctionalSuite
TestResult = FunctionalResult

class FunctionalHarness:
    """Main test harness for running functional tests."""

    def __init__(self, host: str = "localhost", port: int = 5555):
        self.host = host
        self.port = port
        self.client: Optional[Client] = None
        self.ctx: Optional[TestContext] = None
        self.suites: Dict[str, FunctionalSuite] = {}
    
    def connect(self) -> bool:
        """Connect to live bridge."""
        try:
            transport = TcpTransport(host=self.host, port=self.port, timeout=15.0)
            self.client = Client(transport)
            self.ctx = TestContext(client=self.client)
            def _chat_callback(message):
                text = message.get("text") or message.get("message")
                if text and self.ctx:
                    self.ctx.log_event(f"CHAT: {text}")
            try:
                self.client.on(TransportEvent.CHAT, _chat_callback)
            except Exception as e:
                print(f"Warning: failed to subscribe to chat events: {e}")
            return True
        except Exception as e:
            print(f"Connection failed: {e}")
            return False

    def _reset_circuit_breaker(self):
        if not self.client:
            return
        try:
            self.client.transport.dispatch("debug_reset_circuit", {})
        except Exception as e:
            print(f"Warning: failed to reset circuit breaker: {e}")
    
    def disconnect(self):
        """Disconnect from bridge."""
        if self.client:
            self.client.shutdown()
    
    def register_suite(self, suite: FunctionalSuite):
        """Register a test suite."""
        self.suites[suite.name] = suite
    
    def run_suite(self, name: str) -> Dict[str, Tuple[FunctionalResult, str]]:
        """Run a specific suite."""
        if name not in self.suites:
            print(f"Suite '{name}' not found")
            return {}

        suite = self.suites[name]
        _print_status(f"RUN SUITE {suite.name}")
        print(f"\n{'='*60}")
        print(f"SUITE: {suite.name}")
        print(f"{'='*60}")

        self._reset_circuit_breaker()
        
        return suite.run_all(self.ctx)
    
    def run_all(self) -> Dict[str, Dict[str, Tuple[FunctionalResult, str]]]:
        """Run all registered suites."""
        all_results = {}
        for name in self.suites:
            all_results[name] = self.run_suite(name)
        return all_results
    
    def print_summary(self, results: Dict[str, Dict[str, Tuple[FunctionalResult, str]]]):
        """Print summary of all results."""
        print(f"\n{'='*60}")
        print("TEST SUMMARY")
        print(f"{'='*60}")
        
        total_pass = 0
        total_fail = 0
        total_skip = 0
        
        for suite_name, suite_results in results.items():
            print(f"\n{suite_name}:")
            for test_id, (result, msg) in suite_results.items():
                status = "PASS" if result == TestResult.PASS else "FAIL" if result == TestResult.FAIL else "SKIP"
                print(f"  {status} {test_id}: {result.value}")
                
                if result == TestResult.PASS:
                    total_pass += 1
                elif result == TestResult.FAIL:
                    total_fail += 1
                else:
                    total_skip += 1
        
        print(f"\nTOTAL: {total_pass} passed, {total_fail} failed, {total_skip} skipped")

    def save_json_report(self, results: Dict[str, Dict[str, Tuple[FunctionalResult, str]]], filename: str = "test_report.json"):
        """Save results to JSON file."""
        report = {
            "suites": {},
            "summary": {"pass": 0, "fail": 0, "skip": 0, "total": 0}
        }
        
        for suite_name, suite_results in results.items():
            report["suites"][suite_name] = {}
            for test_id, (result, msg) in suite_results.items():
                status = result.value
                report["suites"][suite_name][test_id] = {
                    "result": status,
                    "message": msg
                }
                
                report["summary"]["total"] += 1
                if result == TestResult.PASS:
                    report["summary"]["pass"] += 1
                elif result == TestResult.FAIL:
                    report["summary"]["fail"] += 1
                else:
                    report["summary"]["skip"] += 1
                    
        try:
            with open(filename, "w") as f:
                json.dump(report, f, indent=2)
            print(f"\nSaved JSON report to {filename}")
        except Exception as e:
            print(f"\nFailed to save JSON report: {e}")
