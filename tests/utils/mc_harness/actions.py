"""
Standard action wrappers for consistent test execution.
"""
from typing import Dict, Union, Tuple
from .context import TestContext
from .waits import (
    wait_for_position_change, 
    wait_for_pathing_stop, 
    wait_for_gui_open, 
    wait_for_arrival,   # New
    close_screen
)
from .interaction import robust_interact_block  # Updated from robust_interact
from .inventory import safe_inventory_click


import time

def do_goto(
    ctx: TestContext, 
    target: Dict[str, Union[int, float]], 
    timeout: float = 12.0,
    arrival_radius: float = 1.5,
    start_move_timeout: float = 8.0,
    start_move_min_dist: float = 0.5,
    require_arrival: bool = True,
    allow_incomplete: bool = False,
) -> bool:
    """
    Reliable movement to target with arrival verification.
    
    Args:
        ctx: Test context
        target: Target position dict with x, y, z keys
        timeout: Maximum time to wait for pathing to complete (default 12.0)
        arrival_radius: Maximum distance from target to consider "arrived" (default 1.5)
        start_move_timeout: Time to wait for movement to start (default 2.0)
        start_move_min_dist: Minimum distance to move to consider "started" (default 0.5)
        require_arrival: If True, return False if not within arrival_radius (default True)
    
    Returns:
        True if movement started, pathing stopped, AND player within arrival_radius
        (or require_arrival=False). False otherwise.
    """
    start_pos = ctx.get_position()
    # Ensure float coords
    x = float(target.get("x", 0))
    y = float(target.get("y", 64))
    z = float(target.get("z", 0))
    target_tuple = (x, y, z)
    
    # Check if already at destination
    dist = ((start_pos[0] - x) ** 2 + (start_pos[2] - z) ** 2) ** 0.5
    if dist <= arrival_radius:
        return True  # Already there
    
    ctx.client.transport.dispatch("goto", {"x": x, "y": y, "z": z})
    
    # Wait for start of movement
    moved = wait_for_position_change(ctx, start_pos, min_dist=start_move_min_dist, 
                                     timeout=start_move_timeout, mode="xz")
    if not moved:
        # Retry with chat command - recapture start position first
        start_pos = ctx.get_position()
        ctx.client.transport.dispatch("chat", {"message": f"#goto {x} {y} {z}"})
        moved = wait_for_position_change(ctx, start_pos, min_dist=start_move_min_dist, 
                                         timeout=start_move_timeout, mode="xz")
    
    if not moved:
        if hasattr(ctx, "log_event"):
            ctx.log_event("Movement fail: did not start moving")
        return False
    
    # Wait for pathing to complete (now implies stability too)
    stopped = wait_for_pathing_stop(ctx, timeout=timeout)
    if not stopped:
        # Live clients sometimes report a pathing state that never fully
        # transitions while the player has already reached a usable range.
        # Treat this as a soft success when we are sufficiently close.
        near_target = wait_for_arrival(
            ctx,
            target_tuple,
            radius=arrival_radius + 0.5,
            timeout=2.0,
        )
        if near_target and (allow_incomplete or not require_arrival):
            if hasattr(ctx, "log_event"):
                ctx.log_event("Movement incomplete but near destination")
            cancel_pathing(ctx)
            return True
        if hasattr(ctx, "log_event"):
            ctx.log_event("Movement fail: pathing did not stop")
        return False
    
    # Verify arrival within tolerance
    if require_arrival:
        arrived = wait_for_arrival(ctx, target_tuple, radius=arrival_radius, timeout=2.0)
        if not arrived and hasattr(ctx, "log_event"):
             dist_msg = f"distance > {arrival_radius}"
             ctx.log_event(f"Movement incomplete: {dist_msg}")
        return arrived
    
    return True

def do_interact(ctx: TestContext, block_pos: Tuple[int, int, int], hand: str = "main_hand") -> bool:
    """RMB interact with block."""
    # Look at block first
    ctx.client.transport.dispatch("look_at", {
        "x": block_pos[0] + 0.5,
        "y": block_pos[1] + 0.5,
        "z": block_pos[2] + 0.5
    })
    
    # Then interact robustly
    return robust_interact_block(ctx, block_pos[0], block_pos[1], block_pos[2])

def do_inventory_click(ctx: TestContext, slot: int, click_type: str = "pickup") -> bool:
    """inventory op."""
    safe_inventory_click(ctx, slot, button=0 if click_type == "pickup" else 1)
    return True

def do_open_container(ctx: TestContext, block_pos: Tuple[int, int, int], timeout: float = 3.0) -> Dict:
    """Open container and return content."""
    do_interact(ctx, block_pos)
    if wait_for_gui_open(ctx, timeout=timeout):
        return ctx.get_inventory()
    return {}

def do_close_container(ctx: TestContext):
    """Close any open container."""
    close_screen(ctx)

def do_goto_waypoint(
    ctx: TestContext, 
    waypoint_name: str,
    timeout: float = 12.0,
    start_move_timeout: float = 2.0
) -> bool:
    """
    Go to a Baritone waypoint by name.
    
    Args:
        ctx: Test context
        waypoint_name: Name of the waypoint (e.g., "crafttable")
        timeout: Max time for pathing
        start_move_timeout: Max time to wait for movement start
        
    Returns:
        True if pathing finished successfully.
    """
    start_pos = ctx.get_position()
    
    # Send commands via direct chat dispatch (no slash)
    ctx.client.transport.dispatch("chat", {"message": f"#wp goal {waypoint_name}"})
    time.sleep(0.5)
    ctx.client.transport.dispatch("chat", {"message": "#path"})
    
    # Wait for start
    moved = wait_for_position_change(ctx, start_pos, min_dist=0.5, 
                                     timeout=start_move_timeout, mode="xz")
    if not moved:
        if hasattr(ctx, "log_event"):
             ctx.log_event(f"Waypoint move '{waypoint_name}' fail: did not start moving")
        return False
        
    # Wait for completion
    return wait_for_pathing_stop(ctx, timeout=timeout)
