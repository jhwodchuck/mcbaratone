"""Checkpoint-backed coordination for renewable food recovery."""

from typing import Callable, Mapping, Optional

from ..common.combat import eat_until_hunger
from ..common.farming import harvest_wheat_farm
from ..common.husbandry import visit_known_herd_for_loot
from ..common.inventory import withdraw_required_from_catalog


_STORED_FOOD_TARGETS = {
    "minecraft:bread": 8,
    "minecraft:baked_potato": 8,
    "minecraft:cooked_beef": 8,
    "minecraft:cooked_chicken": 8,
    "minecraft:cooked_cod": 8,
    "minecraft:cooked_mutton": 8,
    "minecraft:cooked_porkchop": 8,
    "minecraft:cooked_rabbit": 8,
    "minecraft:cooked_salmon": 8,
    "minecraft:golden_carrot": 8,
}


def get_food_search_center(
    client,
    state,
    read_state: Callable,
) -> tuple[float, float]:
    """Return one stable exploration origin across repeated phase retries."""
    if state is None:
        live = read_state(client, "Food recovery origin") or {}
        position = live.get("block_position", live.get("position", {}))
        return (
            float(position.get("x", 0) or 0),
            float(position.get("z", 0) or 0),
        )
    recovery = state.custom_data.setdefault("survival_recovery", {})
    center = recovery.get("food_search_center")
    if isinstance(center, (list, tuple)) and len(center) == 2:
        live = read_state(client, "Food recovery center validation") or {}
        position = live.get("block_position", live.get("position", {}))
        if all(axis in position for axis in ("x", "z")):
            current = (
                float(position["x"]),
                float(position["z"]),
            )
            distance = (
                (current[0] - float(center[0])) ** 2
                + (current[1] - float(center[1])) ** 2
            ) ** 0.5
            if distance > 384.0:
                print(
                    "RECOVERY: rebasing stale food-search center "
                    f"after {distance:.1f} blocks"
                )
                recovery["food_search_center"] = [current[0], current[1]]
                return current
        return (float(center[0]), float(center[1]))
    live = read_state(client, "Food recovery origin") or {}
    position = live.get("block_position", live.get("position", {}))
    center = [
        float(position.get("x", 0) or 0),
        float(position.get("z", 0) or 0),
    ]
    recovery["food_search_center"] = center
    return (center[0], center[1])


def remember_renewable_food_source(
    state,
    food_animals: Mapping[str, tuple[str, str]],
    animal_type: str,
    location: tuple[int, int, int],
) -> Optional[dict]:
    """Persist a live-observed herd large enough to preserve a pair."""
    if state is None or animal_type not in food_animals:
        return None
    raw_item, cooked_item = food_animals[animal_type]
    x, y, z = (int(value) for value in location)
    source = {
        "type": "observed_animal_herd",
        "animal_type": animal_type,
        "location": [x, y, z],
        "raw_item": raw_item,
        "cooked_item": cooked_item,
        "verified": True,
        "renewal": "preserve_breeding_pair",
    }
    state.custom_data.setdefault("structures", {})["food_source"] = source
    state.add_location(
        "farm",
        x,
        y,
        z,
        dimension="overworld",
        tags=["food", f"{animal_type}_herd", "renewable", "observed"],
    )
    return source


def record_failed_food_source(source: dict, threshold: int = 2) -> int:
    """Count failed visits and retire a repeatedly unharvestable source."""
    failures = int(source.get("failed_visits", 0)) + 1
    source["failed_visits"] = failures
    if failures >= threshold:
        source["verified"] = False
    return failures


def recover_food_from_known_sources(
    client,
    state,
    food_animals: Mapping[str, tuple[str, str]],
    *,
    allow_hunting: bool = True,
    withdraw_fn: Callable = withdraw_required_from_catalog,
    eat_fn: Callable = eat_until_hunger,
    harvest_fn: Callable = harvest_wheat_farm,
    visit_herd_fn: Callable = visit_known_herd_for_loot,
) -> bool:
    """Recover from storage/farm first, then an optional verified herd."""
    if state is not None and getattr(state, "checkpoint_dir", None):
        moved = withdraw_fn(
            client,
            _STORED_FOOD_TARGETS,
            state=state,
            max_travel_distance=96.0,
        )
        if moved >= 0 and eat_fn(client, minimum_food=12):
            return True

    farm = (state.custom_data.get("wheat_farm") if state else None) or {}
    origin = farm.get("origin")
    if isinstance(origin, (list, tuple)) and len(origin) == 3:
        farm_x, farm_y, farm_z = (int(value) for value in origin)
        if harvest_fn(
            client, farm_x, farm_y, farm_z
        ) and eat_fn(client, minimum_food=12):
            return True

    if not allow_hunting:
        return False
    source = (
        state.custom_data.get("structures", {}).get("food_source", {})
        if state
        else {}
    )
    location = source.get("location") if isinstance(source, dict) else None
    if not (
        isinstance(location, (list, tuple))
        and len(location) == 3
        and source.get("verified")
    ):
        return False
    animal_type = str(source.get("animal_type") or "cow")
    fallback = food_animals.get(animal_type, food_animals["cow"])
    raw_item = str(source.get("raw_item") or fallback[0])
    recovered = visit_herd_fn(
        client,
        {raw_item: 3},
        animal_type,
        preserve_breeding_pair=True,
        location=location,
    ) and eat_fn(client, minimum_food=12)
    if recovered:
        source.pop("failed_visits", None)
        return True

    failures = record_failed_food_source(source)
    if not source.get("verified", False):
        print(
            "  Persisted food herd is no longer harvestable; retiring "
            f"the source after {failures} failed visits."
        )
    return False
