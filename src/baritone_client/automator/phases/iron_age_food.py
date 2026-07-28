"""Durable renewable-food evidence for the food-and-iron phase."""

from __future__ import annotations

from typing import Optional, Tuple


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
    source = (
        state.custom_data.get("structures", {}).get("food_source", {})
    )
    if not isinstance(source, dict) or not source.get("verified"):
        return {}
    location = source.get("location")
    if not isinstance(location, (list, tuple)) or len(location) != 3:
        return {}
    return source


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
