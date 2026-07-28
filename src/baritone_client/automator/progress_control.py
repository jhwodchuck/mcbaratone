"""Durable, strategy-level evidence for autonomous progression control."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Mapping


_CUSTOM_PROGRESS_KEYS = {
    "base_location",
    "bootstrap_base_location",
    "completed_objectives",
    "end_city",
    "end_portal",
    "farm_location",
    "locations",
    "megabase_location",
    "milestones",
    "nether_portal",
    "phase_payloads",
    "stronghold_coords",
    "structures",
    "terraform",
    "terraform_plan",
    "wheat_farm",
}

_EXACT_PROGRESSION_ITEMS = {
    "minecraft:beacon",
    "minecraft:blaze_powder",
    "minecraft:blaze_rod",
    "minecraft:bookshelf",
    "minecraft:cobblestone",
    "minecraft:elytra",
    "minecraft:ender_eye",
    "minecraft:ender_pearl",
    "minecraft:enchanting_table",
    "minecraft:iron_ingot",
    "minecraft:obsidian",
    "minecraft:shield",
    "minecraft:shulker_box",
}

_PROGRESSION_SUFFIXES = (
    "_axe",
    "_boots",
    "_chestplate",
    "_helmet",
    "_hoe",
    "_leggings",
    "_log",
    "_pickaxe",
    "_planks",
    "_shovel",
    "_sword",
)


def _json_safe(value: Any) -> Any:
    """Return deterministic JSON data without volatile timestamps."""
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, Mapping):
        return {
            str(_json_safe(key)): _json_safe(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key).lower() not in {"timestamp", "updated_at", "last_seen"}
        }
    if isinstance(value, set):
        return [_json_safe(item) for item in sorted(value, key=str)]
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _progression_inventory(observations: Mapping[str, Any]) -> dict[str, int]:
    result = {}
    for item_id, count in observations.items():
        path = str(item_id).split(":", 1)[-1]
        if (
            str(item_id) in _EXACT_PROGRESSION_ITEMS
            or path.startswith(("cooked_", "diamond_"))
            or path.endswith(_PROGRESSION_SUFFIXES)
        ):
            result[str(item_id)] = int(count or 0)
    return result


def progression_fingerprint(state: Any) -> str:
    """Hash durable capability evidence while ignoring movement/retry churn."""
    custom_data = getattr(state, "custom_data", {}) or {}
    meaningful_custom = {
        key: custom_data[key]
        for key in _CUSTOM_PROGRESS_KEYS
        if key in custom_data
    }
    evidence = {
        "custom": meaningful_custom,
        "inventory_high_water": _progression_inventory(
            getattr(state, "inventory_observations", {}) or {}
        ),
        "phase_progress": getattr(state, "phase_progress", {}) or {},
    }
    encoded = json.dumps(
        _json_safe(evidence),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["progression_fingerprint"]
