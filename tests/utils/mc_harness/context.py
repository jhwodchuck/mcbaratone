"""
Shared test context for Minecraft integration tests.
Defines the TestContext class which holds client state and assertions.
"""


import sys
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Union, Optional

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'src'))

from baritone_client import Client

class SkipTest(Exception):
    """Exception raised to skip a test."""
    pass

@dataclass
class TestContext:
    """Shared context for test execution."""
    client: Client
    start_time: float = 0.0
    snapshots: List[Dict] = field(default_factory=list)
    events: List[str] = field(default_factory=list)
    log_file_path: Optional[str] = None

    def skip(self, reason: str):
        """Skip the current test."""
        self.log_event(f"SKIPPING: {reason}")
        raise SkipTest(reason)

    def require(self, condition: bool, message: str) -> None:
        """Fail fast if a setup condition is not met."""
        if not condition:
            self.log_event(f"SETUP FAILED: {message}")
            raise RuntimeError(f"Test Requirement Failed: {message}")
    
    # ... (rest of methods)

    def set_log_file(self, path: str) -> None:
        """Enable file logging for test events."""
        self.log_file_path = path
        log_dir = os.path.dirname(path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

    def _append_log_line(self, line: str) -> None:
        if not self.log_file_path:
            return
        try:
            with open(self.log_file_path, "a", encoding="utf-8") as handle:
                handle.write(f"{line}\n")
        except Exception:
            # Avoid breaking test flow on log IO errors.
            return

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

    def get_block(self, x: int, y: int, z: int) -> Dict:
        """Get block info at position."""
        return self.client.transport.dispatch("get_block", {"x": x, "y": y, "z": z})
    
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
        """
        Give item via command (requires cheats).
        Batches in 64 to ensure compatibility with all MC versions.
        """
        before_total = self.count_item(item_id)
        remaining = count
        
        while remaining > 0:
            batch = min(remaining, 64)
            self.client.transport.dispatch("chat", {"message": f"/give @p {item_id} {batch}"})
            # Brief wait for server processing
            time.sleep(0.1)
            remaining -= batch
            
        # Wait for full amount
        self.wait_for_item(item_id, before_total + count, timeout=5.0)

    def run_command(self, command: str):
        """Run a server command (requires cheats)."""
        cmd = command.strip()
        if not cmd.startswith("/"):
            cmd = f"/{cmd}"
        self.client.transport.dispatch("chat", {"message": cmd})
        time.sleep(0.1)
    
    def clear_inventory(self):
        """Clear player inventory, logging contents first."""
        try:
            inv = self.get_inventory()
            items = []
            for slot in inv.get("inventory", []):
                if slot and "id" in slot and slot["id"] != "minecraft:air":
                    items.append(f"{slot.get('count', 1)}x {slot['id']}")
            
            if items:
                self.log_event(f"Clearing inventory containing: {', '.join(items)}")
            else:
                self.log_event("Clearing empty inventory")
        except Exception as e:
            self.log_event(f"Error checking inventory before clear: {e}")

        self.client.transport.dispatch("chat", {"message": "/clear @p"})
        self.wait_for_inventory_clear(timeout=4.0)
    
    def teleport(self, x: int, y: int, z: int):
        """Teleport player."""
        self.client.transport.dispatch("chat", {"message": f"/tp @p {x} {y} {z}"})
        # Wait for position to settle
        from .waits import wait_for_position_stable
        wait_for_position_stable(self, timeout=2.0, stable_window=0.5)

    def set_gamemode(self, mode: str):
        """Set player gamemode."""
        self.run_command(f"gamemode {mode} @p")

    def close_open_screens(self, timeout: float = 2.0) -> bool:
        """Close any open GUI screen."""
        state = self.get_state()
        if not state.get("has_gui") or state.get("screen") == "none":
            return True
        self.client.transport.dispatch("close_screen", {})
        start = time.time()
        while time.time() - start < timeout:
            state = self.get_state()
            if not state.get("has_gui") or state.get("screen") == "none":
                return True
            time.sleep(0.1)
        return False

    def respawn_if_needed(self, timeout: float = 5.0) -> bool:
        """Respawn player if dead."""
        state = self.get_state()
        if not state.get("is_dead"):
            return True
        self.client.transport.dispatch("respawn", {})
        start = time.time()
        while time.time() - start < timeout:
            state = self.get_state()
            if not state.get("is_dead"):
                return True
            time.sleep(0.2)
        return False

    def move_out_of_water(self, radius: int = 12, attempts: int = 3) -> bool:
        """Try to move out of water without teleporting."""
        water_ids = {
            "minecraft:water",
            "minecraft:bubble_column",
            "minecraft:kelp",
            "minecraft:seagrass",
            "minecraft:tall_seagrass"
        }
        solid_ids = [
            "minecraft:stone",
            "minecraft:cobblestone",
            "minecraft:dirt",
            "minecraft:grass_block",
            "minecraft:sand",
            "minecraft:gravel",
            "minecraft:netherrack",
            "minecraft:end_stone",
            "minecraft:deepslate"
        ]
        for _ in range(attempts):
            x, y, z = self.get_position()
            block = self.get_block(int(x), int(y), int(z)).get("id")
            above = self.get_block(int(x), int(y) + 1, int(z)).get("id")
            if block not in water_ids and above not in water_ids:
                return True
            result = self.client.transport.dispatch("find_blocks", {
                "blocks": solid_ids,
                "radius": radius,
                "limit": 1
            })
            found = result.get("found", result.get("data", {}).get("found", []))
            if not found:
                return False
            pos = found[0]
            if isinstance(pos, dict) and {"x", "y", "z"}.issubset(pos.keys()):
                self.client.transport.dispatch("goto", {
                    "x": int(pos["x"]),
                    "y": int(pos["y"]) + 1,
                    "z": int(pos["z"])
                })
                start = time.time()
                while time.time() - start < 4.0:
                    state = self.get_state()
                    if not state.get("is_pathing", False):
                        break
                    time.sleep(0.2)
                self.client.transport.dispatch("cancel", {})
                time.sleep(0.2)
        return False

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
        line = f"[{time.time() - self.start_time:.2f}s] {event}"
        self.events.append(line)
        self._append_log_line(line)
