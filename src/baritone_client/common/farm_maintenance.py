"""Fresh-world postconditions and scheduling for surface crop maintenance."""

import time
from collections.abc import Mapping

from .home_surface import below_home_surface


def plot_on_cooldown(state, plot) -> bool:
    custom = getattr(state, "custom_data", {})
    holds = custom.get("crop_site_cooldowns", {}) if isinstance(custom, Mapping) else {}
    key = ",".join(str(int(v)) for v in plot)
    try:
        return isinstance(holds, Mapping) and float(holds.get(key, 0) or 0) > time.time()
    except (TypeError, ValueError):
        return True


def farm_surface_safe(client) -> bool:
    """Stop native crop work on an observed drop or unverifiable safety."""
    guard = getattr(client, "_protected_surface_work", None)
    if guard is None:
        return True
    try:
        live = client.transport.dispatch("get_state", {})
        return (not live.get("is_dead") and float(live["health"]) >= 14
                and int(live["air_supply"]) > 0
                and not below_home_surface(guard, live["block_position"]))
    except (KeyError, TypeError, ValueError, RuntimeError):
        return False


def replant_empty_wheat_tiles(client, x: int, y: int, z: int, size: int = 5) -> int:
    """Repair only freshly observed bare soil in the existing plot."""
    from . import farming

    half = size // 2
    tiles = [
        (x + dx, y, z + dz)
        for dx in range(-half, half + 1)
        for dz in range(-half, half + 1)
        if (dx or dz)
        and farming._block_id(client, x + dx, y, z + dz)
        in {"minecraft:farmland", "minecraft:dirt", "minecraft:grass_block"}
        and farming._block_id(client, x + dx, y + 1, z + dz)
        in {"minecraft:air", "minecraft:cave_air"}
    ]
    return farming.plant_farm_tiles(client, tiles) if tiles else 0
