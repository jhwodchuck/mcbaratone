"""Static item groups and value parsers shared by phase verification."""

from typing import Any, Mapping, Tuple


FOOD_ITEMS = {
    "minecraft:bread", "minecraft:baked_potato", "minecraft:cooked_beef",
    "minecraft:cooked_chicken", "minecraft:cooked_cod", "minecraft:cooked_mutton",
    "minecraft:cooked_porkchop", "minecraft:cooked_rabbit",
    "minecraft:cooked_salmon", "minecraft:golden_carrot",
}
STONE_TOOLS = {
    "minecraft:stone_axe", "minecraft:stone_hoe", "minecraft:stone_pickaxe",
    "minecraft:stone_shovel", "minecraft:stone_sword",
}
IRON_ARMOR = {
    "minecraft:iron_boots", "minecraft:iron_chestplate",
    "minecraft:iron_helmet", "minecraft:iron_leggings",
}
SHULKER_BOXES = {"minecraft:shulker_box"} | {
    f"minecraft:{color}_shulker_box"
    for color in (
        "white", "orange", "magenta", "light_blue", "yellow", "lime", "pink",
        "gray", "light_gray", "cyan", "purple", "blue", "brown", "green",
        "red", "black",
    )
}


def position(value: Any) -> Tuple[int, int, int] | None:
    """Normalize bridge mapping and sequence coordinate shapes."""
    try:
        if isinstance(value, Mapping):
            return int(value["x"]), int(value["y"]), int(value["z"])
        if isinstance(value, (list, tuple)) and len(value) >= 3:
            return int(value[0]), int(value[1]), int(value[2])
    except (KeyError, TypeError, ValueError):
        return None
    return None
