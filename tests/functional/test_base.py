"""
Test Base - Foundation for functional tests.
Provides test harness, action contracts, and utilities.
"""

import sys
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Any, Optional, Tuple, Union
from enum import Enum

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from baritone_client import Client, TcpTransport


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
            return self.state.copy()
        elif route == "get_inventory":
            return {"inventory": self.inventory.copy()}
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
            return {"crafted": True, "output_slot": 0}
        elif route == "smelt":
            # Simulate smelting consuming fuel
            self._consume_item("minecraft:oak_log", 1)  # Fuel
            return {"smelted": True, "output_count": 1}
        elif route == "check_craft":
            return {"can_craft": True, "missing": [], "recipe_id": "test"}
        elif route == "command":
            return {"status": "ok"}
        elif route == "goal/apply":
            return {"status": "ok", "goal": payload}
        elif route == "settings/set":
            return {"status": "ok", "name": payload.get("name"), "value": payload.get("value")}
        elif route == "process/status":
            return {"status": "ok"}
        elif route == "process/path_result":
            return {"status": "ok"}
        elif route == "process/builder/start":
            return {"status": "ok"}
        else:
            return {"status": "ok", "route": route, "payload": payload}

    def subscribe(self, event, callback):
        pass

    def shutdown(self):
        pass


class TestResult(Enum):
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


@dataclass
class TestContext:
    """Shared context for test execution."""
    client: Client
    start_time: float = 0.0
    snapshots: List[Dict] = field(default_factory=list)
    events: List[str] = field(default_factory=list)
    
    def get_state(self) -> Dict:
        """Get current game state."""
        return self.client.transport.dispatch("get_state", {})
    
    def get_inventory(self, timeout: float = 1.0) -> Dict:
        """Get inventory snapshot."""
        return self.client.transport.dispatch("get_inventory", {}, timeout=timeout)
    
    def get_position(self) -> Tuple[float, float, float]:
        """Get current position as tuple."""
        state = self.get_state()
        pos = state.get("block_position", state.get("position", {}))
        if isinstance(pos, dict):
            return (pos.get("x", 0), pos.get("y", 64), pos.get("z", 0))
        return (0, 64, 0)
    
    def has_item(self, item_id: str, count: int = 1) -> bool:
        """Check if inventory has item."""
        inv = self.get_inventory()
        total = 0
        for slot in inv.get("inventory", []):
            if slot and slot.get("id") == item_id:
                total += slot.get("count", 0)
        return total >= count
    
    def count_item(self, item_id: str) -> int:
        """Count total of item in inventory."""
        inv = self.get_inventory()
        total = 0
        for slot in inv.get("inventory", []):
            if slot and slot.get("id") == item_id:
                total += slot.get("count", 0)
        return total

    def wait_for_item(self, item_id: str, count: int, timeout: float = 3.0) -> bool:
        """Wait until inventory has at least count of item_id."""
        start = time.time()
        while time.time() - start < timeout:
            if self.count_item(item_id) >= count:
                return True
            time.sleep(0.1)
        return False

    def wait_for_inventory_clear(self, timeout: float = 3.0) -> bool:
        """Wait until inventory is empty."""
        start = time.time()
        while time.time() - start < timeout:
            inv = self.get_inventory()
            has_items = any(
                slot and slot.get("id") != "minecraft:air" and slot.get("count", 0) > 0
                for slot in inv.get("inventory", [])
            )
            if not has_items:
                return True
            time.sleep(0.1)
        return False
    
    def give_item(self, item_id: str, count: int = 1):
        """Give item via command (requires cheats)."""
        before = self.count_item(item_id)
        self.client.transport.dispatch("chat", {"message": f"/give @p {item_id} {count}"})
        self.wait_for_item(item_id, before + count, timeout=3.0)

    def run_command(self, command: str):
        """Run a server command (requires cheats)."""
        cmd = command.strip()
        if not cmd.startswith("/"):
            cmd = f"/{cmd}"
        self.client.transport.dispatch("chat", {"message": cmd})
        time.sleep(0.1)
    
    def clear_inventory(self):
        """Clear player inventory."""
        self.client.transport.dispatch("chat", {"message": "/clear @p"})
        self.wait_for_inventory_clear(timeout=3.0)
    
    def teleport(self, x: int, y: int, z: int):
        """Teleport player."""
        self.client.transport.dispatch("chat", {"message": f"/tp @p {x} {y} {z}"})
        time.sleep(0.5)

    def set_gamemode(self, mode: str):
        """Set player gamemode."""
        self.run_command(f"gamemode {mode}")

    def set_block(self, x: int, y: int, z: int, block_id: str):
        """Set block at position."""
        self.run_command(f"setblock {x} {y} {z} {block_id}")

    def set_time(self, time_val: str):
        """Set world time (day/night/noon/midnight or ticks)."""
        self.client.transport.dispatch("chat", {"message": f"/time set {time_val}"})
        time.sleep(0.1)

    def set_health(self, health: float):
        """Set player health approximately by healing to full."""
        # Use instant health effect to restore health (approximate)
        self.client.transport.dispatch("chat", {"message": "/effect give @p minecraft:instant_health 10"})
        time.sleep(0.1)
    
    def snapshot(self, label: str = ""):
        """Take a state snapshot."""
        self.snapshots.append({
            "label": label,
            "time": time.time() - self.start_time,
            "state": self.get_state(),
            "inventory": self.get_inventory(),
            "position": self.get_position()
        })

    def rollback_to_snapshot(self, identifier: Union[int, str]):
        """Rollback state to a specific snapshot by index or label."""
        if isinstance(identifier, str):
            for i, snap in enumerate(self.snapshots):
                if snap["label"] == identifier:
                    index = i
                    break
            else:
                raise ValueError(f"Snapshot with label '{identifier}' not found")
        elif isinstance(identifier, int):
            if 0 <= identifier < len(self.snapshots):
                index = identifier
            else:
                raise ValueError(f"Snapshot index {identifier} out of range")
        else:
            raise TypeError("Identifier must be int or str")

        snap = self.snapshots[index]

        # Restore position
        pos = snap["position"]
        self.teleport(int(pos[0]), int(pos[1]), int(pos[2]))

        # Restore inventory
        self.clear_inventory()
        for slot in snap["inventory"].get("inventory", []):
            if slot and "id" in slot and "count" in slot and slot["count"] > 0:
                self.give_item(slot["id"], slot["count"])

        # Restore health if present
        state = snap["state"]
        if "health" in state:
            self.set_health(state["health"])

        # Restore time if present
        if "world_time" in state:
            self.set_time(str(state["world_time"]))

        self.log_event(f"Rolled back to snapshot {index} ({snap['label']})")

    def log_event(self, event: str):
        """Log a test event."""
        self.events.append(f"[{time.time() - self.start_time:.2f}s] {event}")


@dataclass
class TestCase:
    """A single functional test case."""
    id: str
    name: str
    description: str
    timeout_seconds: int = 120
    
    # Setup/teardown
    setup: Optional[Callable[['TestContext'], None]] = None
    teardown: Optional[Callable[['TestContext'], None]] = None
    
    # Test steps
    steps: List[Callable[['TestContext'], bool]] = field(default_factory=list)
    
    # Assertions
    assertions: List[Callable[['TestContext'], Tuple[bool, str]]] = field(default_factory=list)
    
    def run(self, ctx: TestContext) -> Tuple[TestResult, str, List[str]]:
        """
        Run the test case.
        Returns (result, message, events).
        """
        ctx.start_time = time.time()
        ctx.events = []
        ctx.snapshots = []
        
        try:
            # Setup
            if self.setup:
                ctx.log_event(f"SETUP: {self.id}")
                self.setup(ctx)
                time.sleep(0.5)
            
            ctx.snapshot("after_setup")
            
            # Execute steps
            for i, step in enumerate(self.steps):
                if time.time() - ctx.start_time > self.timeout_seconds:
                    return TestResult.TIMEOUT, f"Timeout at step {i}", ctx.events
                
                ctx.log_event(f"STEP {i}: executing")
                try:
                    result = step(ctx)
                    if not result:
                        ctx.snapshot(f"step_{i}_failed")
                        return TestResult.FAIL, f"Step {i} returned False", ctx.events
                except Exception as e:
                    ctx.log_event(f"STEP {i} ERROR: {e}")
                    return TestResult.FAIL, f"Step {i} error: {e}", ctx.events
            
            ctx.snapshot("after_steps")
            
            # Check assertions
            for i, assertion in enumerate(self.assertions):
                passed, msg = assertion(ctx)
                if not passed:
                    ctx.log_event(f"ASSERTION {i} FAILED: {msg}")
                    return TestResult.FAIL, f"Assertion {i} failed: {msg}", ctx.events
            
            return TestResult.PASS, "All steps and assertions passed", ctx.events
            
        except Exception as e:
            return TestResult.FAIL, f"Unexpected error: {e}", ctx.events
        
        finally:
            # Teardown
            if self.teardown:
                try:
                    self.teardown(ctx)
                except:
                    pass


class TestSuite:
    """Collection of related test cases."""
    
    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.tests: List[TestCase] = []
    
    def add(self, test: TestCase):
        self.tests.append(test)
    
    def run_all(self, ctx: TestContext) -> Dict[str, Tuple[TestResult, str]]:
        """Run all tests in suite. Returns dict of test_id -> (result, message)."""
        results = {}
        for test in self.tests:
            print(f"\n>>> Running {test.id}: {test.name}")
            result, msg, events = test.run(ctx)
            results[test.id] = (result, msg)
            
            status = "PASS" if result == TestResult.PASS else "FAIL"
            print(f"{status} {test.id}: {result.value} - {msg}")
            
            if result != TestResult.PASS and events:
                print("  Events:")
                for e in events[-5:]:  # Last 5 events
                    print(f"    {e}")
        
        return results


class TestHarness:
    """Main test harness for running functional tests."""
    
    def __init__(self, host: str = "localhost", port: int = 5555):
        self.host = host
        self.port = port
        self.client: Optional[Client] = None
        self.ctx: Optional[TestContext] = None
        self.suites: Dict[str, TestSuite] = {}
    
    def connect(self) -> bool:
        """Connect to live bridge."""
        try:
            transport = TcpTransport(host=self.host, port=self.port, timeout=15.0)
            self.client = Client(transport)
            self.ctx = TestContext(client=self.client)
            return True
        except Exception as e:
            print(f"Connection failed: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from bridge."""
        if self.client:
            self.client.shutdown()
    
    def register_suite(self, suite: TestSuite):
        """Register a test suite."""
        self.suites[suite.name] = suite
    
    def run_suite(self, name: str) -> Dict[str, Tuple[TestResult, str]]:
        """Run a specific suite."""
        if name not in self.suites:
            print(f"Suite '{name}' not found")
            return {}
        
        suite = self.suites[name]
        print(f"\n{'='*60}")
        print(f"SUITE: {suite.name}")
        print(f"{'='*60}")
        
        return suite.run_all(self.ctx)
    
    def run_all(self) -> Dict[str, Dict[str, Tuple[TestResult, str]]]:
        """Run all registered suites."""
        all_results = {}
        for name in self.suites:
            all_results[name] = self.run_suite(name)
        return all_results
    
    def print_summary(self, results: Dict[str, Dict[str, Tuple[TestResult, str]]]):
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
