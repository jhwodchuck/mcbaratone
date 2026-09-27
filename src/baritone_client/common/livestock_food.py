"""Turn a nearby herd into cooked food: hunt a bounded batch, cook it at home.

Wheat alone refills a prepared-food reserve at roughly one loaf per cycle.
A herd beside base is far faster: a cow drops up to three beef and every
piece cooks into prepared food. Each cycle keeps a breeding pair of every
family, caps its kills, hunts only while survival allows, tries to breed the
pair while standing beside it, and cooks everything it carries at the home
furnace with fuel that never requires a cave trip.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

#: Hunted families in preference order: cows drop the most meat per kill.
LIVESTOCK: Tuple[Tuple[str, str, str], ...] = (
    ("cow", "minecraft:beef", "minecraft:cooked_beef"),
    ("chicken", "minecraft:chicken", "minecraft:cooked_chicken"),
)
#: Any carried raw meat is cooked, whatever produced it.
RAW_TO_COOKED = {
    "minecraft:beef": "minecraft:cooked_beef",
    "minecraft:chicken": "minecraft:cooked_chicken",
    "minecraft:porkchop": "minecraft:cooked_porkchop",
    "minecraft:mutton": "minecraft:cooked_mutton",
    "minecraft:rabbit": "minecraft:cooked_rabbit",
}
HERD_RADIUS = 64
KEEP_PER_FAMILY = 2
MAX_KILLS_PER_FAMILY = 6
HUNT_TIMEOUT = 240
MIN_HEALTH = 14.0
MIN_FOOD = 10
_FURNACES = ("minecraft:furnace", "minecraft:smoker")


@dataclass(frozen=True)
class LivestockResult:
    success: bool
    detail: str
    hunted: int = 0
    cooked: int = 0


def _xyz(value: Any) -> Optional[Tuple[int, int, int]]:
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return (int(value[0]), int(value[1]), int(value[2]))
        except (TypeError, ValueError):
            return None
    return None


def _home_furnace(client: Any, state: Any) -> Optional[Tuple[int, int, int]]:
    """The starter-house furnace when it still stands, else one nearby."""
    from .navigation import find_nearby_block

    custom = getattr(state, "custom_data", {}) or {}
    structures = custom.get("structures", {}) if isinstance(custom, Mapping) else {}
    house = structures.get("starter_house", {}) if isinstance(structures, Mapping) else {}
    recorded = _xyz(house.get("furnace")) if isinstance(house, Mapping) else None
    if recorded is not None:
        try:
            block = client.transport.dispatch(
                "get_block", {"x": recorded[0], "y": recorded[1], "z": recorded[2]}
            ).get("id", "")
        except Exception:
            block = ""
        if block in _FURNACES:
            return recorded
    found = find_nearby_block(client, list(_FURNACES), radius=24)
    return tuple(found) if found is not None else None


def _cook_carried(client: Any, state: Any) -> int:
    """Cook every carried raw meat stack; return cooked items gained."""
    from . import harness_ops
    from .inventory import count_item
    from .resources import _prepare_safe_furnace_fuel
    from .tasks import PlayerDeathDetected

    gained = 0
    for raw, cooked in RAW_TO_COOKED.items():
        count = count_item(client, raw)
        if count <= 0:
            continue
        furnace = _home_furnace(client, state)
        if furnace is None:
            print("LIVESTOCK: no furnace near home to cook raw meat")
            return gained
        fuel = _prepare_safe_furnace_fuel(client, count)
        if fuel is None:
            print("LIVESTOCK: no safe furnace fuel; raw meat stays raw for now")
            return gained
        before = count_item(client, cooked)
        try:
            harness_ops.smelt_in_furnace(client, furnace, raw, fuel, cooked, count)
        except PlayerDeathDetected:
            raise
        except Exception as exc:
            # A refused furnace click must not abort the whole food cycle;
            # whatever did cook is still counted below.
            print(f"LIVESTOCK: cooking {raw} failed ({exc})")
        gained += max(0, count_item(client, cooked) - before)
    return gained


def _survival_allows_hunting(live: Mapping[str, Any]) -> bool:
    try:
        return (
            not bool(live.get("is_dead"))
            and float(live.get("health", 0) or 0) >= MIN_HEALTH
            and int(live.get("food_level", live.get("food", 0)) or 0) >= MIN_FOOD
        )
    except (TypeError, ValueError):
        return False


def run_livestock_food_cycle(
    client: Any,
    state: Any,
    *,
    prepared_now: int,
    target: int,
) -> LivestockResult:
    """Hunt a bounded batch from nearby herds and cook it toward ``target``."""
    from .combat import hunt_mobs
    from .husbandry import breed_pair, count_herd
    from .inventory import count_item

    cooked = _cook_carried(client, state)
    needed = int(target) - int(prepared_now) - cooked
    hunted = 0
    if needed > 0:
        live = client.transport.dispatch("get_state", {})
        if not isinstance(live, Mapping) or not _survival_allows_hunting(live):
            return LivestockResult(
                bool(cooked), f"cooked {cooked}; survival margin too low to hunt", 0, cooked
            )
        for family, raw, _cooked_id in LIVESTOCK:
            herd = count_herd(client, family, radius=HERD_RADIUS)
            spare = herd - KEEP_PER_FAMILY
            if spare <= 0:
                continue
            kills = min(spare, MAX_KILLS_PER_FAMILY, needed)
            before = count_item(client, raw)
            try:
                hunt_mobs(
                    client,
                    mob_types=[family],
                    required_loot={raw: kills},
                    search_radius=HERD_RADIUS,
                    timeout=HUNT_TIMEOUT,
                    heal_threshold=12.0,
                    abort_on_other_hostiles=True,
                    max_distance_from_origin=96.0,
                    max_kills=kills,
                    explore_when_empty=False,
                )
            finally:
                try:
                    client.transport.dispatch("cancel", {})
                except Exception:
                    pass
            got = max(0, count_item(client, raw) - before)
            hunted += got
            needed -= got
            # Standing beside the herd now: keep it renewable when food allows.
            try:
                breed_pair(client, family)
            except Exception:
                pass
            if needed <= 0:
                break
        cooked += _cook_carried(client, state)
    detail = f"hunted {hunted} raw meat, cooked {cooked} into prepared food"
    return LivestockResult(bool(hunted or cooked), detail, hunted, cooked)


__all__ = ["LIVESTOCK", "LivestockResult", "run_livestock_food_cycle"]
