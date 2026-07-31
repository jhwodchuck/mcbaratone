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


def recover_known_food(client, state) -> bool:
    """Run the shared checkpointed-food recovery policy."""
    from .phases.iron_age_food import FOOD_ANIMALS

    return recover_food_from_known_sources(client, state, FOOD_ANIMALS)


def _durable_food_search_anchor(state) -> Optional[tuple[float, float, float]]:
    """Prefer the re-anchored spawn-bootstrap home for survival searches."""
    if state is None:
        return None
    custom_data = state.custom_data
    phase_payloads = custom_data.get("phase_payloads", {})
    spawn_payload = phase_payloads.get("SPAWN_BOOTSTRAP", {})
    return_home = spawn_payload.get("return_home", {})
    candidates = (
        return_home.get("origin"),
        custom_data.get("base_location"),
        custom_data.get("homestead_anchor"),
    )
    for candidate in candidates:
        if isinstance(candidate, (list, tuple)) and len(candidate) == 3:
            try:
                return tuple(float(value) for value in candidate)
            except (TypeError, ValueError):
                continue
    return None


def get_food_search_anchor(
    client,
    state,
    read_state: Callable,
) -> tuple[float, float, float]:
    """Return one stable 3D home anchor across repeated phase retries."""
    if state is None:
        live = read_state(client, "Food recovery origin") or {}
        position = live.get("block_position", live.get("position", {}))
        return (
            float(position.get("x", 0) or 0),
            float(position.get("y", 64) or 64),
            float(position.get("z", 0) or 0),
        )
    recovery = state.custom_data.setdefault("survival_recovery", {})
    anchor = recovery.get("food_search_anchor")
    if isinstance(anchor, (list, tuple)) and len(anchor) == 3:
        live = read_state(client, "Food recovery center validation") or {}
        position = live.get("block_position", live.get("position", {}))
        if all(axis in position for axis in ("x", "z")):
            current = (
                float(position["x"]),
                float(position["z"]),
            )
            distance = (
                (current[0] - float(anchor[0])) ** 2
                + (current[1] - float(anchor[2])) ** 2
            ) ** 0.5
            if distance > 384.0:
                print(
                    "RECOVERY: rebasing stale food-search anchor "
                    f"after {distance:.1f} blocks"
                )
                rebased = (
                    current[0],
                    float(position.get("y", 64) or 64),
                    current[1],
                )
                recovery["food_search_anchor"] = list(rebased)
                return rebased
        return tuple(float(value) for value in anchor)

    live = read_state(client, "Food recovery origin") or {}
    position = live.get("block_position", live.get("position", {}))
    anchor = _durable_food_search_anchor(state) or (
        float(position.get("x", 0) or 0),
        float(position.get("y", 64) or 64),
        float(position.get("z", 0) or 0),
    )
    recovery["food_search_anchor"] = list(anchor)
    return anchor


def get_food_search_center(
    client,
    state,
    read_state: Callable,
) -> tuple[float, float]:
    """Compatibility wrapper returning the stable anchor's horizontal center."""
    anchor = get_food_search_anchor(client, state, read_state)
    return (anchor[0], anchor[2])


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
    if not (isinstance(source, dict) and source.get("verified")) and state:
        live = client.transport.dispatch("get_state", {})
        position = live.get("block_position", live.get("position", {}))
        source = _nearest_verified_food_location(
            state,
            anchor=(position.get("x", 0), position.get("y", 0), position.get("z", 0)),
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
        # Survival recovery is a last-resort meal, not a farm-maintenance
        # pass. Requiring two survivors can turn a usable observed herd into
        # a false failure when only one loaded animal remains.
        preserve_breeding_pair=False,
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


def _nearest_verified_food_location(state, *, anchor=None) -> dict:
    """Return the closest persisted food landmark that is not retired."""
    locations = state.custom_data.get("locations", {})
    farms = locations.get("farm", []) if isinstance(locations, dict) else []
    if not isinstance(farms, list):
        return {}
    anchor = anchor or state.custom_data.get("homestead_anchor")
    if not isinstance(anchor, (list, tuple)) or len(anchor) < 3:
        anchor = state.custom_data.get("base_location")
    if not isinstance(anchor, (list, tuple)) or len(anchor) < 3:
        anchor = (0, 0, 0)
    candidates = []
    for record in farms:
        if not isinstance(record, dict) or "food" not in record.get("tags", []):
            continue
        if record.get("verified") is False:
            continue
        if not all(key in record for key in ("x", "y", "z")):
            continue
        distance = ((float(record["x"]) - float(anchor[0])) ** 2 +
                    (float(record["z"]) - float(anchor[2])) ** 2) ** 0.5
        tags = record.get("tags", [])
        animal_type = next(
            (tag[:-5] for tag in tags if tag.endswith("_herd")), "cow"
        )
        raw_items = {
            "cow": "beef", "mooshroom": "beef", "pig": "porkchop",
            "chicken": "chicken", "sheep": "mutton", "rabbit": "rabbit",
        }
        candidates.append((distance, {
            "location": [int(record["x"]), int(record["y"]), int(record["z"])],
            "animal_type": animal_type,
            "verified": True,
            "raw_item": f"minecraft:{raw_items.get(animal_type, 'beef')}",
        }))
    return min(candidates, key=lambda item: item[0])[1] if candidates else {}
