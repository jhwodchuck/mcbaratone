"""Grow food at the farm a starving, wounded player is already standing beside.

Health only regenerates at food 18, and survival recovery refuses blind
exploration while health is critical. Where no passive animal is in range
that is a closed loop: no food, so no health, so no search for food. A
checkpointed farm a short walk away is the one food source that needs
neither: bread from carried wheat, mature wheat from the plot, and seeds
replanted from where the player stands.
"""

from __future__ import annotations

import time
from math import hypot
from typing import Any, Mapping, Optional, Tuple

#: Only tend a farm the player can reach without a real journey.
SURVIVAL_FARM_REACH = 16.0
#: The survival loop calls this every few seconds; the farm needs minutes.
SURVIVAL_FARM_INTERVAL = 60.0
#: Plots are 5x5 around a water center (see ``establish_wheat_farm``).
_HALF = 2
_MATURE_WHEAT_AGE = "7"


def _position(live: Mapping[str, Any]) -> Optional[Tuple[float, float, float]]:
    position = live.get("block_position", live.get("position", {}))
    try:
        return (float(position["x"]), float(position["y"]), float(position["z"]))
    except (KeyError, TypeError, ValueError):
        return None


def _nearest_plot(live: Mapping[str, Any], state: Any):
    """Return the closest checkpointed plot center and its horizontal distance."""
    from .food_supply import _plots

    here = _position(live)
    plots = _plots(state)
    if here is None or not plots:
        return None
    plot = min(plots, key=lambda p: hypot(p[0] - here[0], p[2] - here[2]))
    return plot, hypot(plot[0] - here[0], plot[2] - here[2])


def _hostile_close(client: Any, live: Mapping[str, Any]) -> bool:
    from .combat import _threat_can_reach_player, scan_for_threats

    try:
        threats = scan_for_threats(client, radius=16, player_state=dict(live))
    except Exception:
        return True
    return any(
        threat.get("distance", 999) <= 12 and _threat_can_reach_player(threat, live)
        for threat in threats
    )


def _bake_and_eat(client: Any) -> bool:
    """Eat carried food, baking bread from wheat first when there is none."""
    from .combat import eat_until_hunger
    from .emergency_food import (
        craft_emergency_bread_from_carried_wheat,
        emergency_food_count,
    )
    from .inventory import count_item

    if emergency_food_count(client) == 0:
        if count_item(client, "minecraft:wheat") < 3:
            return False
        craft_emergency_bread_from_carried_wheat(client)
        if emergency_food_count(client) == 0:
            return False
    return bool(eat_until_hunger(client, minimum_food=20))


def tend_local_farm_for_food(
    client: Any,
    state: Any,
    *,
    now: Optional[float] = None,
) -> bool:
    """Bake, harvest and replant at a nearby farm; True once food was eaten."""
    from .farming import _block_data, _till_and_plant_tile, harvest_wheat_farm
    from .inventory import count_item
    from .navigation import goto

    current = time.monotonic() if now is None else float(now)
    last = getattr(client, "_survival_farm_last", None)
    if isinstance(last, (int, float)) and current - last < SURVIVAL_FARM_INTERVAL:
        return False
    client._survival_farm_last = current

    live = client.transport.dispatch("get_state", {})
    if not isinstance(live, Mapping):
        return False
    if int(live.get("food_level", live.get("food", 0)) or 0) >= 18:
        return False  # health is already regenerating; nothing to grow for
    found = _nearest_plot(live, state)
    if found is None or found[1] > SURVIVAL_FARM_REACH:
        return False
    if _hostile_close(client, live):
        return False
    if _bake_and_eat(client):
        print("RECOVERY: ate bread baked from carried wheat")
        return True

    (cx, cy, cz), distance = found
    if distance > 3.0:
        goto(client, cx, cy, cz, timeout=45, tolerance=3.5, radius=2)
    tiles = [
        (cx + dx, cy, cz + dz)
        for dx in range(-_HALF, _HALF + 1)
        for dz in range(-_HALF, _HALF + 1)
        if dx or dz
    ]
    crops = {tile: _block_data(client, tile[0], cy + 1, tile[2]) for tile in tiles}
    wheat_before = count_item(client, "minecraft:wheat")
    if any(
        "wheat" in str(data.get("id", ""))
        and str((data.get("state") or {}).get("age")) == _MATURE_WHEAT_AGE
        for data in crops.values()
    ):
        harvest_wheat_farm(client, cx, cy, cz, range_=_HALF + 1)
    harvested = max(0, count_item(client, "minecraft:wheat") - wheat_before)

    planted = 0
    for tile, data in crops.items():
        if count_item(client, "minecraft:wheat_seeds") < 1:
            break
        if "wheat" in str(data.get("id", "")):
            continue
        if _till_and_plant_tile(client, *tile):
            planted += 1
    print(
        f"RECOVERY: tended local farm at {(cx, cy, cz)}: "
        f"harvested {harvested} wheat, planted {planted} tile(s)"
    )
    if _bake_and_eat(client):
        print("RECOVERY: ate bread baked from farm wheat")
        return True
    return False


__all__ = [
    "SURVIVAL_FARM_INTERVAL",
    "SURVIVAL_FARM_REACH",
    "tend_local_farm_for_food",
]
