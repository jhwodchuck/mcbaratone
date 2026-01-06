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
                block: str = "minecraft:stone") -> None:
    fill(ctx, min_x, y, min_z, max_x, y, max_z, block)


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
    try:
        ctx.set_gamemode(gamemode)
    except Exception:
        safe_run_command(ctx, f"gamemode {gamemode} @p")
    if peaceful:
        set_difficulty(ctx, "peaceful")
    set_gamerules_for_test(ctx, mob_spawning=False, daylight_cycle=not freeze_time, weather_cycle=not clear_weather)
    if clear_weather:
        set_weather_clear(ctx)
    if freeze_time:
        set_time_day(ctx)
    clear_effects(ctx)
    set_health_full(ctx)
    kill_nearby_entities(ctx, radius=kill_radius)


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
