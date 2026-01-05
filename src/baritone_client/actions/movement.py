"""
Movement action implementation.
"""

import time
import math
from typing import Callable, Optional, Tuple, Iterable

from baritone_client.core.interfaces import ActionContext, ActionResult
from baritone_client.actions.base import BaseAction

class MovementAction(BaseAction):
    """Handles navigation and movement logic."""
    
    def execute(self, context: ActionContext) -> ActionResult:
        """Default execute - placeholder."""
        return ActionResult.fail("MovementAction requires a specific method call")

    def goto(
        self,
        context: ActionContext,
        x: int,
        y: int,
        z: int,
        timeout: int = 120,
        check_interval: float = 2.0,
        tolerance: float = 3.0,
        on_tick: Optional[Callable[[], None]] = None,
    ) -> bool:
        """Navigate to specific coordinates."""
        try:
            self.run_command(context, "goto", {"x": x, "y": y, "z": z})
            
            start = time.time()
            while time.time() - start < timeout:
                if on_tick:
                    on_tick()
                    
                state = context.state.refresh()
                # Use refresh() to get latest state object
                
                pos = state.position
                px, py, pz = pos[0], pos[1], pos[2]
                
                distance = ((px - x)**2 + (py - y)**2 + (pz - z)**2) ** 0.5
                if distance <= tolerance:
                    self.run_command(context, "cancel", {})
                    return True
                
                time.sleep(check_interval)
            
            self.run_command(context, "cancel", {})
            return False
            
        except Exception as e:
            print(f"Navigation error: {e}")
            return False

    def explore_until(
        self,
        context: ActionContext,
        condition: Callable[[], bool],
        max_distance: int = 1000,
        timeout: int = 300,
        on_tick: Optional[Callable[[], None]] = None,
    ) -> bool:
        """Explore until a condition is met."""
        try:
            # Get current position as origin
            state = context.state.refresh()
            origin = state.position
            origin_x, origin_z = int(origin[0]), int(origin[2])
            
            # Start exploration
            self.run_command(context, "explore", {"x": origin_x, "z": origin_z})
            
            start = time.time()
            while time.time() - start < timeout:
                if condition():
                    self.run_command(context, "cancel", {})
                    return True
                
                if on_tick:
                    on_tick()
                
                # Check distance from origin
                state = context.state.refresh()
                pos = state.position
                distance = ((pos[0] - origin_x)**2 + (pos[2] - origin_z)**2) ** 0.5
                
                if distance > max_distance:
                    self.run_command(context, "cancel", {})
                    return False
                
                # Check if pathing stopped
                # context.client.transport.dispatch("get_state") returns raw dict
                # context.state.refresh() returns PlayerState object which doesn't expose 'is_pathing' currently
                # We need to access raw state or update WorldState. 
                # For now using safe direct access if possible or assume refreshing works.
                # Actually BaseAction.run_command wraps dispatch.
                
                # Let's check raw state for is_pathing
                raw_state = self.run_command(context, "get_state", {})
                is_pathing = raw_state.get("is_pathing", True)
                
                if not is_pathing:
                     # Simple debounce logic similar to original
                     time.sleep(2)
                     raw_state = self.run_command(context, "get_state", {})
                     if not raw_state.get("is_pathing", True):
                         return False
                
                time.sleep(0.5)
            
            self.run_command(context, "cancel", {})
            return False
            
        except Exception as e:
            print(f"Exploration error: {e}")
            return False

    def find_nearby_block(
        self,
        context: ActionContext,
        block_types: list,
        radius: int = 50,
    ) -> Optional[Tuple[int, int, int]]:
        """Find nearest block of specified type."""
        try:
            data = self.run_command(context, "find_blocks", {
                "blocks": block_types,
                "radius": radius,
                "limit": 1,
            })
            
            found = data.get("found", [])
            if found:
                block = found[0]
                return (block["x"], block["y"], block["z"])
            
            return None
            
        except Exception:
            return None

    def goto_block(
        self,
        context: ActionContext,
        block_types: list,
        radius: int = 50,
        timeout: int = 120,
    ) -> bool:
        """Navigate to nearest block of specified type."""
        coords = self.find_nearby_block(context, block_types, radius)
        if coords:
            return self.goto(context, *coords, timeout=timeout)
        return False
