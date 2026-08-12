"""Strict live inventory admission shared by every dragon-fight controller."""

from __future__ import annotations

from typing import Any

from .combat_loadout import choose_best_weapon

_MIN_ARMOR_DURABILITY = 64
_MIN_WEAPON_DURABILITY = 16
_MIN_BOW_DURABILITY = 32
_MIN_SHIELD_DURABILITY = 16
_MIN_PREPARED_FOOD = 6
_MIN_ARROWS = 32
_ARMOR_RANK = {
    "leather": 0,
    "golden": 1,
    "chainmail": 2,
    "iron": 3,
    "diamond": 4,
    "netherite": 5,
}
_ARMOR_PIECES = ("helmet", "chestplate", "leggings", "boots")
_ARROWS = {"minecraft:arrow", "minecraft:spectral_arrow", "minecraft:tipped_arrow"}
_PREPARED_FOOD = {
    "minecraft:cooked_beef",
    "minecraft:cooked_porkchop",
    "minecraft:cooked_chicken",
    "minecraft:cooked_mutton",
    "minecraft:cooked_rabbit",
    "minecraft:baked_potato",
    "minecraft:bread",
    "minecraft:golden_carrot",
    "minecraft:golden_apple",
}


def _inventory_payload(response: Any) -> dict | None:
    if not isinstance(response, dict) or response.get("error"):
        return None
    data = response.get("data", response)
    return data if isinstance(data, dict) else None


def _live_items(data: dict, section: str) -> list[dict] | None:
    values = data.get(section)
    if not isinstance(values, list):
        return None
    items = []
    for value in values:
        if not isinstance(value, dict):
            return None
        count = value.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            return None
        if count:
            items.append(value)
    return items


def _durable(item: dict, minimum_remaining: int) -> bool:
    maximum = item.get("max_damage")
    damage = item.get("damage")
    if (
        isinstance(maximum, bool)
        or not isinstance(maximum, int)
        or maximum <= 0
        or isinstance(damage, bool)
        or not isinstance(damage, int)
        or not 0 <= damage <= maximum
    ):
        return False
    return maximum - damage >= minimum_remaining


def _armor_identity(item_id: object) -> tuple[str, str] | None:
    name = str(item_id).split(":", 1)[-1]
    for piece in _ARMOR_PIECES:
        suffix = f"_{piece}"
        if name.endswith(suffix):
            return name[: -len(suffix)], piece
    return None


def _durable_melee_items(items: list[dict]) -> list[dict]:
    return [
        item
        for item in items
        if (
            str(item.get("id", "")).endswith("_sword")
            or (
                str(item.get("id", "")).endswith("_axe")
                and not str(item.get("id", "")).endswith("_pickaxe")
            )
        )
        and _durable(item, _MIN_WEAPON_DURABILITY)
    ]


def _armor_piece_ready(armor: list[dict], piece: str) -> bool:
    for item in armor:
        identity = _armor_identity(item.get("id"))
        if (
            identity is not None
            and identity[1] == piece
            and identity[0] in _ARMOR_RANK
            and _ARMOR_RANK[identity[0]] >= _ARMOR_RANK["iron"]
            and _durable(item, _MIN_ARMOR_DURABILITY)
        ):
            return True
    return False


def _live_dragon_kit_ready(client: Any) -> bool:
    """Require one fresh, complete kit without crafting or moving inventory."""
    try:
        data = _inventory_payload(client.transport.dispatch("get_inventory", {}))
        if data is None:
            return False
        selected_slot = data.get("selected_slot")
        if (
            isinstance(selected_slot, bool)
            or not isinstance(selected_slot, int)
            or not 0 <= selected_slot <= 8
        ):
            return False
        main = _live_items(data, "inventory")
        armor = _live_items(data, "armor")
        offhand = _live_items(data, "offhand")
        if main is None or armor is None or offhand is None:
            return False

        equipped = {}
        for item in armor:
            identity = _armor_identity(item.get("id"))
            if identity is None or identity[0] not in _ARMOR_RANK:
                return False
            material, piece = identity
            if _ARMOR_RANK[material] < _ARMOR_RANK["iron"] or piece in equipped:
                return False
            if not _durable(item, _MIN_ARMOR_DURABILITY):
                return False
            equipped[piece] = item
        if set(equipped) != set(_ARMOR_PIECES):
            return False

        carried = main + offhand
        shields = [item for item in carried if item.get("id") == "minecraft:shield"]
        if not any(_durable(item, _MIN_SHIELD_DURABILITY) for item in shields):
            return False
        weapon = choose_best_weapon(_durable_melee_items(main), "minecraft:ender_dragon")
        if weapon is None:
            return False
        bows = [item for item in main if item.get("id") == "minecraft:bow"]
        if not any(_durable(item, _MIN_BOW_DURABILITY) for item in bows):
            return False
        arrows = sum(item["count"] for item in carried if item.get("id") in _ARROWS)
        prepared_food = sum(
            item["count"] for item in carried if item.get("id") in _PREPARED_FOOD
        )
        return arrows >= _MIN_ARROWS and prepared_food >= _MIN_PREPARED_FOOD
    except Exception:
        return False


def dragon_kit_ready(client: Any) -> bool:
    """Verify the exact shared dragon-fight inventory contract without mutation."""
    return _live_dragon_kit_ready(client)


def dragon_kit_provision_requirements(client: Any) -> dict[str, int] | None:
    """Return exact count targets that repair a fresh dragon-kit snapshot."""
    try:
        data = _inventory_payload(client.transport.dispatch("get_inventory", {}))
        if data is None:
            return None
        main = _live_items(data, "inventory")
        armor = _live_items(data, "armor")
        offhand = _live_items(data, "offhand")
        if main is None or armor is None or offhand is None:
            return None
        carried = main + offhand
        observed = carried + armor

        def count(item_id: str) -> int:
            return sum(item["count"] for item in observed if item.get("id") == item_id)

        requirements: dict[str, int] = {}
        for piece in _ARMOR_PIECES:
            if not _armor_piece_ready(armor, piece):
                item_id = f"minecraft:iron_{piece}"
                requirements[item_id] = count(item_id) + 1
        bows = [item for item in main if item.get("id") == "minecraft:bow"]
        if not any(_durable(item, _MIN_BOW_DURABILITY) for item in bows):
            requirements["minecraft:bow"] = count("minecraft:bow") + 1
        shields = [item for item in carried if item.get("id") == "minecraft:shield"]
        if not any(_durable(item, _MIN_SHIELD_DURABILITY) for item in shields):
            requirements["minecraft:shield"] = count("minecraft:shield") + 1
        weapon = choose_best_weapon(_durable_melee_items(main), "minecraft:ender_dragon")
        if weapon is None:
            requirements["minecraft:iron_sword"] = count("minecraft:iron_sword") + 1
        arrows = sum(item["count"] for item in carried if item.get("id") in _ARROWS)
        if arrows < _MIN_ARROWS:
            requirements["minecraft:arrow"] = count("minecraft:arrow") + _MIN_ARROWS - arrows
        food = sum(item["count"] for item in carried if item.get("id") in _PREPARED_FOOD)
        if food < _MIN_PREPARED_FOOD:
            requirements["minecraft:bread"] = count("minecraft:bread") + _MIN_PREPARED_FOOD - food
        return requirements
    except Exception:
        return None


__all__ = [
    "dragon_kit_provision_requirements",
    "dragon_kit_ready",
]
