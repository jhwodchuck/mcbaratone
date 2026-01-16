"""
World Operations Module

Shared helpers for world state, time, and environment checks.
"""

def get_world_time(ctx) -> int:
    """Get current world time in ticks."""
    try:
        state = ctx.get_state()
        return int(state.get("world_time", 0))
    except Exception:
        return 0

def is_near_night(world_time: int) -> bool:
    """Check if time is approaching night (dusk/night)."""
    # 12000 is dusk start, 13000 is night start
    return (world_time % 24000) >= 12000

def is_dead(ctx) -> bool:
    """Check if player is dead (health <= 0)."""
    try:
        state = ctx.get_state()
        health = state.get("health", 20.0)
        return health <= 0.0
    except Exception:
        return False

def wait_for_respawn(ctx, timeout: float = 30.0) -> bool:
    """Wait for player to respawn after death.
    
    Returns True if respawned within timeout, False otherwise.
    """
    import time
    start = time.time()
    while time.time() - start < timeout:
        if not is_dead(ctx):
            return True
        # Try clicking to trigger respawn screen action
        try:
            ctx.client.transport.dispatch("chat", {"message": ""})
        except Exception:
            pass
        time.sleep(1.0)
    return False
