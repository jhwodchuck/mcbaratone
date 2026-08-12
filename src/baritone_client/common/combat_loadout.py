"""Strict, target-aware melee weapon selection.

Combat admission must use a fresh per-client inventory response.  The general
inventory cache intentionally has a last-known fallback for benign crafting
reads; using that fallback here could invent a weapon after death or during a
bridge outage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .combat_targeting import normalize_mob_type
from .combat_action import exclusive_combat_action


@dataclass(frozen=True)
class WeaponStats:
    damage: float
    speed: float
    kind: str


_WEAPON_STATS = {
    "minecraft:wooden_sword": WeaponStats(4.0, 1.6, "sword"),
    "minecraft:golden_sword": WeaponStats(4.0, 1.6, "sword"),
    "minecraft:stone_sword": WeaponStats(5.0, 1.6, "sword"),
    "minecraft:iron_sword": WeaponStats(6.0, 1.6, "sword"),
    "minecraft:diamond_sword": WeaponStats(7.0, 1.6, "sword"),
    "minecraft:netherite_sword": WeaponStats(8.0, 1.6, "sword"),
    "minecraft:wooden_axe": WeaponStats(7.0, 0.8, "axe"),
    "minecraft:golden_axe": WeaponStats(7.0, 1.0, "axe"),
    "minecraft:stone_axe": WeaponStats(9.0, 0.8, "axe"),
    "minecraft:iron_axe": WeaponStats(9.0, 0.9, "axe"),
    "minecraft:diamond_axe": WeaponStats(9.0, 1.0, "axe"),
    "minecraft:netherite_axe": WeaponStats(10.0, 1.0, "axe"),
    "minecraft:wooden_pickaxe": WeaponStats(2.0, 1.2, "tool"),
    "minecraft:stone_pickaxe": WeaponStats(3.0, 1.2, "tool"),
    "minecraft:iron_pickaxe": WeaponStats(4.0, 1.2, "tool"),
    "minecraft:diamond_pickaxe": WeaponStats(5.0, 1.2, "tool"),
    "minecraft:netherite_pickaxe": WeaponStats(6.0, 1.2, "tool"),
}

_UNDEAD = {
    "bogged",
    "drowned",
    "husk",
    "phantom",
    "skeleton",
    "skeleton_horse",
    "stray",
    "wither",
    "wither_skeleton",
    "zoglin",
    "zombie",
    "zombie_horse",
    "zombie_villager",
    "zombified_piglin",
}
_ARTHROPODS = {"bee", "cave_spider", "endermite", "silverfish", "spider"}


def _inventory_entries(response: Any) -> list[dict]:
    if not isinstance(response, dict) or response.get("error"):
        return []
    data = response.get("data", response)
    if not isinstance(data, dict):
        return []
    return [
        item
        for item in data.get("inventory", [])
        if isinstance(item, dict) and int(item.get("count", 0) or 0) > 0
    ]


def _enchantment_level(item: dict, enchantment: str) -> int:
    best = 0
    for entry in item.get("enchantments", []) or []:
        if not isinstance(entry, dict):
            continue
        if normalize_mob_type(entry.get("id")) == enchantment:
            best = max(best, int(entry.get("level", 0) or 0))
    return best


def remaining_durability(item: dict) -> int | None:
    """Return remaining uses, or None for unbreakable/legacy metadata."""
    maximum = int(item.get("max_damage", 0) or 0)
    if maximum <= 0:
        return None
    return maximum - int(item.get("damage", 0) or 0)


def weapon_score(item: dict, target_type: object = "") -> float:
    """Score sustained melee damage with target enchantments and durability."""
    stats = _WEAPON_STATS.get(str(item.get("id", "")))
    if stats is None:
        return float("-inf")
    remaining = remaining_durability(item)
    if remaining is not None and remaining <= 3:
        return float("-inf")

    target = normalize_mob_type(target_type)
    bonus = 0.0
    sharpness = _enchantment_level(item, "sharpness")
    if sharpness:
        bonus += 0.5 * sharpness + 0.5
    if target in _UNDEAD:
        bonus += 2.5 * _enchantment_level(item, "smite")
    if target in _ARTHROPODS:
        bonus += 2.5 * _enchantment_level(item, "bane_of_arthropods")

    sustained_damage = (stats.damage + bonus) * stats.speed
    durability_margin = 0.0 if remaining is None else min(0.5, remaining / 1280.0)
    # Swords retain a small crowd-control preference because their sweep can
    # reduce pressure when several melee mobs reach the bot at once.
    sweep_margin = 0.2 if stats.kind == "sword" else 0.0
    return sustained_damage + durability_margin + sweep_margin


def choose_best_weapon(
    entries: Iterable[dict], target_type: object = ""
) -> dict | None:
    """Return the best usable carried melee weapon."""
    candidates = [
        item for item in entries if weapon_score(item, target_type) != float("-inf")
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            weapon_score(item, target_type),
            remaining_durability(item) or 10**9,
            str(item.get("id", "")),
        ),
    )


def _same_weapon(candidate: dict, selected: dict) -> bool:
    """Match the exact scored stack after a selection or swap."""

    def enchantments(item: dict) -> tuple[tuple[str, int], ...]:
        return tuple(
            sorted(
                (
                    str(entry.get("id", "")),
                    int(entry.get("level", 0) or 0),
                )
                for entry in item.get("enchantments", []) or []
                if isinstance(entry, dict)
            )
        )

    return (
        candidate.get("id") == selected.get("id")
        and int(candidate.get("damage", 0) or 0) == int(selected.get("damage", 0) or 0)
        and enchantments(candidate) == enchantments(selected)
    )


def select_exact_item(client: Any, selected: dict) -> bool:
    """Select and verify the exact observed stack, preserving its metadata."""
    slot = int(selected.get("slot", -1))
    if slot < 0:
        return False
    before = client.transport.dispatch("get_inventory", {})
    before_entries = _inventory_entries(before)
    observed = next(
        (item for item in before_entries if int(item.get("slot", -1)) == slot), {}
    )
    if not _same_weapon(observed, selected):
        return False
    before_data = before.get("data", before) if isinstance(before, dict) else {}
    target_slot = slot
    expected = selected
    if slot > 8:
        client.transport.dispatch("close_screen", {})
        current_slot = int(before_data.get("selected_slot", 0) or 0)
        target_slot = current_slot if 0 <= current_slot <= 8 else 0
        client.transport.dispatch(
            "inventory_click", {"slot": slot, "type": "SWAP", "button": target_slot}
        )
        expected = dict(selected, slot=target_slot)
    client.transport.dispatch("select_slot", {"slot": target_slot})
    response = client.transport.dispatch("get_inventory", {})
    entries = _inventory_entries(response)
    equipped = next(
        (item for item in entries if int(item.get("slot", -1)) == target_slot), {}
    )
    data = response.get("data", response) if isinstance(response, dict) else {}
    selected_slot = data.get("selected_slot")
    slot_verified = selected_slot == target_slot
    return slot_verified and _same_weapon(equipped, expected)


def equip_best_weapon(client: Any, target_type: object = "") -> bool:
    """Select the best weapon from a fresh inventory read, failing closed."""
    try:
        with exclusive_combat_action(client) as acquired:
            if not acquired:
                return False
            response = client.transport.dispatch("get_inventory", {})
            selected = choose_best_weapon(_inventory_entries(response), target_type)
            if selected is None:
                return False
            return select_exact_item(client, selected)
    except Exception:
        return False


def choose_armor_replacement(
    candidates: Iterable[tuple[int, int, dict]],
    *,
    current_rank: int,
    current_remaining: int | None,
    minimum_rank: int,
    minimum_remaining: int,
) -> tuple[int, int, dict] | None:
    """Choose a durable combat downgrade when worn high-tier armor is unsafe."""
    values = list(candidates)
    if not values:
        return None
    current_unsafe = (
        current_remaining is not None and current_remaining < minimum_remaining
    )
    durable = [
        value
        for value in values
        if value[0] >= minimum_rank and value[1] >= minimum_remaining
    ]
    best = max(durable if current_unsafe and durable else values, key=lambda value: value[:2])
    rank, remaining, _item = best
    same_tier_upgrade = bool(
        rank == current_rank
        and current_unsafe
        and current_remaining is not None
        and remaining > current_remaining
    )
    safe_downgrade = bool(
        current_unsafe and rank >= minimum_rank and remaining >= minimum_remaining
    )
    if (rank < current_rank and not safe_downgrade) or (
        rank == current_rank and not same_tier_upgrade
    ):
        return None
    return best


__all__ = [
    "choose_armor_replacement",
    "choose_best_weapon",
    "equip_best_weapon",
    "remaining_durability",
    "select_exact_item",
    "weapon_score",
]
