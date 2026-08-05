"""World/admin helpers for Minecraft integration tests."""

from typing import Dict, Optional

from .common import safe_run_command
from .waits import cancel_pathing, close_screen


def normalize_bounds(bounds: Dict[str, int]) -> Dict[str, int]:
    """Normalize bounds dict to min/max ordering."""
    return {
        "min_x": min(bounds["min_x"], bounds["max_x"]),
        "min_y": min(bounds["min_y"], bounds["max_y"]),
        "min_z": min(bounds["min_z"], bounds["max_z"]),
        "max_x": max(bounds["min_x"], bounds["max_x"]),
        "max_y": max(bounds["min_y"], bounds["max_y"]),
        "max_z": max(bounds["min_z"], bounds["max_z"]),
    }


def fill(ctx, x1: int, y1: int, z1: int, x2: int, y2: int, z2: int, block: str) -> None:
    safe_run_command(ctx, f"fill {x1} {y1} {z1} {x2} {y2} {z2} {block}")


def clear_box(ctx, bounds: Dict[str, int]) -> None:
    b = normalize_bounds(bounds)
    fill(ctx, b["min_x"], b["min_y"], b["min_z"], b["max_x"], b["max_y"], b["max_z"], "air")


def build_floor(ctx, min_x: int, y: int, min_z: int, max_x: int, max_z: int,
                mat: str = "minecraft:stone") -> None:
    fill(ctx, min_x, y, min_z, max_x, y, max_z, mat)


def set_gamerules_for_test(ctx, mob_spawning: bool = False, daylight_cycle: bool = False,
                           weather_cycle: bool = False) -> None:
    safe_run_command(ctx, f"gamerule doMobSpawning {'true' if mob_spawning else 'false'}")
    safe_run_command(ctx, f"gamerule doDaylightCycle {'true' if daylight_cycle else 'false'}")
    safe_run_command(ctx, f"gamerule doWeatherCycle {'true' if weather_cycle else 'false'}")


def restore_gamerules(ctx) -> None:
    safe_run_command(ctx, "gamerule doMobSpawning true")
    safe_run_command(ctx, "gamerule doDaylightCycle true")
    safe_run_command(ctx, "gamerule doWeatherCycle true")


def set_time_day(ctx) -> None:
    safe_run_command(ctx, "time set day")


def set_weather_clear(ctx) -> None:
    safe_run_command(ctx, "weather clear")


def set_difficulty(ctx, difficulty: str) -> None:
    safe_run_command(ctx, f"difficulty {difficulty}")


def clear_effects(ctx) -> None:
    safe_run_command(ctx, "effect clear @p")


def set_health_full(ctx) -> None:
    try:
        ctx.set_health(20)
    except Exception:
        safe_run_command(ctx, "effect give @p minecraft:instant_health 10")


def kill_nearby_entities(ctx, radius: int = 64) -> None:
    safe_run_command(ctx, f"kill @e[type=!player,distance=..{radius}]")


def tp(ctx, x: int, y: int, z: int, settle: float = 0.2) -> None:
    safe_run_command(ctx, f"tp @p {x} {y} {z}")
    # Wait for chunk loading to prevent void_air errors
    wait_for_chunk_loading(ctx)
    if settle:
        import time
        time.sleep(settle)


def prepare_test_world(
    ctx,
    *,
    gamemode: str = "survival",
    peaceful: bool = True,
    kill_radius: int = 64,
    freeze_time: bool = True,
    clear_weather: bool = True,
) -> None:
    """Standardized setup sequence for a deterministic test environment."""
    cancel_pathing(ctx)
    close_screen(ctx)
    
    # Use spectator during setup to avoid physics/death
    safe_run_command(ctx, "gamemode spectator @p")

    if peaceful:
        set_difficulty(ctx, "peaceful")
    set_gamerules_for_test(ctx, mob_spawning=False, daylight_cycle=not freeze_time, weather_cycle=not clear_weather)
    if clear_weather:
        set_weather_clear(ctx)
        set_time_day(ctx)
    
    # TP to safe area high up to let chunks load without interference
    # Create a safety platform to prevent falling when switching to survival
    # Bedrock at 0, 99, 0
    safe_run_command(ctx, "fill -1 99 -1 1 99 1 minecraft:bedrock")
    tp(ctx, 0, 100, 0)
    wait_for_chunk_loading(ctx)
    wait_for_tick_stabilization(ctx, ticks=60)
    
    # Reset player state
    clear_effects(ctx)
    set_health_full(ctx)
    if not peaceful:
        set_difficulty(ctx, "normal")

    # Finally set the requested gamemode
    try:
        ctx.set_gamemode(gamemode)
    except Exception:
        safe_run_command(ctx, f"gamemode {gamemode} @p")

    wait_for_tick_stabilization(ctx, ticks=60)


def wait_for_chunk_loading(ctx, timeout: float = 10.0) -> None:
    """Wait for the chunk at player position to be loaded (non-void)."""
    import time
    from .waits import get_block_id
    
    start = time.time()
    while time.time() - start < timeout:
        pos = ctx.get_position()
        # Check block below player
        block = get_block_id(ctx, int(pos[0]), int(pos[1]) - 1, int(pos[2]))
        if "void_air" not in block:
            return
        time.sleep(0.5)
    ctx.log_event("Warning: Chunk loading timed out (still void_air)")


def wait_for_tick_stabilization(ctx, ticks: int = 60) -> None:
    """Wait for server ticks to stabilize state."""
    import time
    # Approximate tick wait since we can't count true server ticks easily without bridge support
    # 20 ticks = 1 second
    time.sleep(ticks / 20.0)




def teardown_test_world(
    ctx,
    *,
    bounds: Optional[Dict[str, int]] = None,
    restore_rules: bool = True,
    kill_radius: int = 64,
) -> None:
    """Defensive teardown to avoid cascading failures across tests."""
    cancel_pathing(ctx)
    close_screen(ctx)
    clear_effects(ctx)
    if kill_radius:
        kill_nearby_entities(ctx, radius=kill_radius)
    if bounds:
        clear_box(ctx, bounds)
    if restore_rules:
        restore_gamerules(ctx)
        # prepare_test_world defaults to Peaceful.  Restore the disposable
        # server profile's Normal difficulty as part of the same cleanup so
        # a failed or partial selection cannot poison the next suite run.
        set_difficulty(ctx, "normal")
