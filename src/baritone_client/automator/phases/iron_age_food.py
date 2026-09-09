"""Durable renewable-food evidence for the food-and-iron phase."""

from __future__ import annotations

from typing import Optional, Tuple

from ...common.combat import eat_until_hunger
from ...common.farming import harvest_wheat_farm
from ...common.husbandry import visit_known_herd_for_loot
from ...common.inventory import withdraw_required_from_catalog
from ..food_recovery_state import (
    checkpointed_wheat_farm_origin,
    verified_food_herd_source,
)


def recover_food_from_known_sources(client, state, *, minimum_food: int = 12) -> bool:
    """Harvest the established wheat farm, or hunt the operator-known
    distant herd, then eat -- the fallback for a biome with nothing
    huntable near base. Cheapest option (the nearby farm) first; the herd
    trip is a genuine expedition and only worth it once the farm can't (or
    doesn't yet) supply enough.

    Shared by FOOD_AND_IRON and NETHER_AND_BLAZE: both phases can run a
    bounded local hunt to zero success in an animal-sparse biome and retry
    that same empty search forever without ever reaching for a location
    already proven to work. Live A1 2026-09-06: NETHER_AND_BLAZE's rearm
    step only called the bounded local search (acquire_emergency_food),
    with no equivalent fallback, and rotated through a dozen empty
    64-block sweeps -- across several failed rearm attempts and two
    deaths -- while a verified farm or herd location sat unused in the
    same checkpoint FOOD_AND_IRON had already written.
    """
    if state is not None:
        moved = withdraw_required_from_catalog(
            client,
            {
                "minecraft:bread": 8,
                "minecraft:cooked_beef": 8,
                "minecraft:cooked_porkchop": 8,
                "minecraft:cooked_chicken": 8,
                "minecraft:baked_potato": 8,
            },
            state=state,
            max_travel_distance=96.0,
        )
        if moved > 0 and eat_until_hunger(client, minimum_food=minimum_food):
            return True

    origin = checkpointed_wheat_farm_origin(state)
    if origin is not None:
        fx, fy, fz = origin
        if harvest_wheat_farm(client, fx, fy, fz) and eat_until_hunger(
            client, minimum_food=minimum_food
        ):
            return True

    source = persisted_food_source(state)
    if not source:
        return False
    animal_type = str(source.get("animal_type", "cow"))
    return visit_known_herd_for_loot(
        client,
        {"minecraft:beef": 3},
        animal_type,
        preserve_breeding_pair=True,
        location=source["location"],
    ) and eat_until_hunger(client, minimum_food=minimum_food)


FOOD_ANIMALS = {
    "cow": ("minecraft:beef", "minecraft:cooked_beef"),
    "mooshroom": ("minecraft:beef", "minecraft:cooked_beef"),
    "pig": ("minecraft:porkchop", "minecraft:cooked_porkchop"),
    "chicken": ("minecraft:chicken", "minecraft:cooked_chicken"),
    "sheep": ("minecraft:mutton", "minecraft:cooked_mutton"),
    "rabbit": ("minecraft:rabbit", "minecraft:cooked_rabbit"),
}


def persisted_food_source(state) -> dict:
    """Return a verified renewable source from checkpoint data."""
    if state is None:
        return {}
    return verified_food_herd_source(state, FOOD_ANIMALS)


def remember_food_source(
    state,
    animal_type: str,
    location: Tuple[int, int, int],
) -> Optional[dict]:
    """Persist a source only after the caller has verified a renewable herd."""
    if state is None:
        return None
    normalized_type = str(animal_type).lower()
    raw_item, cooked_item = FOOD_ANIMALS.get(
        normalized_type,
        (f"minecraft:{normalized_type}", f"minecraft:cooked_{normalized_type}"),
    )
    x, y, z = (int(value) for value in location)
    source = {
        "animal_type": normalized_type,
        "location": [x, y, z],
        "raw_item": raw_item,
        "cooked_item": cooked_item,
        "verified": True,
    }
    state.custom_data.setdefault("structures", {})["food_source"] = source
    state.add_location(
        "farm",
        x,
        y,
        z,
        tags=["renewable", "observed", normalized_type],
    )
    return source
