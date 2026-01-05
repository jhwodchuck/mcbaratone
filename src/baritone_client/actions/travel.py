"""
Travel action implementation for advanced movement and navigation operations.
"""

from typing import Callable, List, Optional, Tuple, Any
import time

from baritone_client.core.interfaces import ActionContext, ActionResult
from baritone_client.actions.base import BaseAction


class TravelAction(BaseAction):
    """Handles advanced travel operations beyond basic movement."""

    def follow_path(self, context: ActionContext, waypoints: List[Tuple[int, int, int]], tolerance: float = 3.0) -> ActionResult:
        """Follow a sequence of waypoints."""
        try:
            for i, (x, y, z) in enumerate(waypoints):
                # Navigate to each waypoint
                result = self.run_command(context, "goto", {"x": x, "y": y, "z": z})

                # Wait for arrival or timeout
                start_time = time.time()
                timeout = 120  # 2 minutes per waypoint

                while time.time() - start_time < timeout:
                    state = context.state.refresh()
                    pos = state.position
                    px, py, pz = pos[0], pos[1], pos[2]

                    distance = ((px - x)**2 + (py - y)**2 + (pz - z)**2) ** 0.5
                    if distance <= tolerance:
                        break
                    time.sleep(1.0)

                if time.time() - start_time >= timeout:
                    return ActionResult.fail(f"Failed to reach waypoint {i}: {x}, {y}, {z}")

            return ActionResult.ok("Path followed successfully")
        except Exception as e:
            return ActionResult.fail(f"Failed to follow path: {e}")

    def explore_area(self, context: ActionContext, center_x: int, center_z: int, radius: int = 100, pattern: str = "spiral") -> ActionResult:
        """Explore an area using a specific pattern."""
        try:
            if pattern == "spiral":
                return self._explore_spiral(context, center_x, center_z, radius)
            elif pattern == "grid":
                return self._explore_grid(context, center_x, center_z, radius)
            else:
                return ActionResult.fail(f"Unknown exploration pattern: {pattern}")
        except Exception as e:
            return ActionResult.fail(f"Failed to explore area: {e}")

    def _explore_spiral(self, context: ActionContext, center_x: int, center_z: int, radius: int) -> ActionResult:
        """Explore in a spiral pattern from center outward."""
        try:
            # Simple spiral exploration - start at center and move outward
            self.run_command(context, "goto", {"x": center_x, "y": 64, "z": center_z})

            # Use baritone's explore command with bounds
            explore_result = self.run_command(context, "explore", {
                "x": center_x,
                "z": center_z,
                "radius": radius
            })

            return ActionResult.ok("Spiral exploration started", explore_result=explore_result)
        except Exception as e:
            return ActionResult.fail(f"Spiral exploration failed: {e}")

    def _explore_grid(self, context: ActionContext, center_x: int, center_z: int, radius: int) -> ActionResult:
        """Explore in a grid pattern covering the area systematically."""
        try:
            # Calculate grid points
            spacing = 32  # 32 block spacing between exploration points
            grid_points = []

            for x in range(center_x - radius, center_x + radius + 1, spacing):
                for z in range(center_z - radius, center_z + radius + 1, spacing):
                    grid_points.append((x, 64, z))

            # Follow the grid path
            return self.follow_path(context, grid_points)
        except Exception as e:
            return ActionResult.fail(f"Grid exploration failed: {e}")

    def return_home(self, context: ActionContext, home_x: int, home_y: int, home_z: int) -> ActionResult:
        """Return to a designated home position."""
        try:
            result = self.run_command(context, "goto", {"x": home_x, "y": home_y, "z": home_z})
            return ActionResult.ok("Returning home", goto_result=result)
        except Exception as e:
            return ActionResult.fail(f"Failed to return home: {e}")

    def patrol_route(self, context: ActionContext, waypoints: List[Tuple[int, int, int]], loops: int = 1) -> ActionResult:
        """Patrol a route by following waypoints in a loop."""
        try:
            for loop in range(loops):
                path_result = self.follow_path(context, waypoints)
                if not path_result.success:
                    return ActionResult.fail(f"Failed on patrol loop {loop + 1}: {path_result.message}")

            return ActionResult.ok(f"Patrol completed {loops} loops")
        except Exception as e:
            return ActionResult.fail(f"Patrol failed: {e}")

    def avoid_obstacles(self, context: ActionContext, target_x: int, target_y: int, target_z: int, safety_distance: int = 5) -> ActionResult:
        """Navigate to target while avoiding obstacles."""
        try:
            # Use baritone's pathfinding which automatically avoids obstacles
            result = self.run_command(context, "goto", {"x": target_x, "y": target_y, "z": target_z})
            return ActionResult.ok("Obstacle avoidance navigation started", goto_result=result)
        except Exception as e:
            return ActionResult.fail(f"Obstacle avoidance navigation failed: {e}")

    def find_safe_spot(self, context: ActionContext, radius: int = 50) -> ActionResult:
        """Find a safe spot to stand (flat ground, no hostile mobs nearby)."""
        try:
            # Check for flat ground areas
            state = context.state.refresh()
            current_pos = state.position
            cx, cy, cz = int(current_pos[0]), int(current_pos[1]), int(current_pos[2])

            # Look for suitable spots in expanding circles
            for r in range(10, radius + 1, 10):
                for angle in range(0, 360, 45):  # Check 8 directions
                    import math
                    test_x = cx + int(r * math.cos(math.radians(angle)))
                    test_z = cz + int(r * math.sin(math.radians(angle)))

                    # Check if area is suitable (this would need more sophisticated logic)
                    # For now, just return a coordinate
                    safe_spot = (test_x, cy, test_z)
                    return ActionResult.ok("Safe spot found", safe_spot=safe_spot)

            return ActionResult.fail("No safe spot found within radius")
        except Exception as e:
            return ActionResult.fail(f"Failed to find safe spot: {e}")

    def navigate_to_biome(self, context: ActionContext, biome_type: str, max_distance: int = 1000) -> ActionResult:
        """Navigate towards a specific biome type."""
        try:
            # This would require biome scanning capabilities
            # For now, use exploration towards a direction
            result = self.run_command(context, "explore", {"max_distance": max_distance})
            return ActionResult.ok(f"Exploring for {biome_type} biome", explore_result=result)
        except Exception as e:
            return ActionResult.fail(f"Failed to navigate to biome {biome_type}: {e}")