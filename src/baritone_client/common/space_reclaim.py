"""Banking for space when the allow-list has nothing to say.

Normal banking is an allow-list: ``EARLY_GAME_EXCESS_ITEMS |
OVERFLOW_BULK_ITEMS`` enumerates what is safe to put in a chest. That works
while a bot only picks up what its phase expects, and fails the moment it does
not -- the list has to anticipate every item in the game, and anything it
misses is carried forever.

Measured on A1Bot 2026-08-16: of 33 carried item types, exactly one
(``leaf_litter``, three items) was on the deposit list, and its 64 cobblestone
sat under the 64 retain floor. Every ``deposit_excess_to_chest`` call therefore
opened a chest, moved zero stacks and closed it again. With 36/36 slots full,
``manage_inventory`` could not free the 3 slots iron mining requires, so
FOOD_AND_IRON reported "Initial iron mining paused because safe inventory space
is unavailable" for hours while the bot toured its chests. It had also
accumulated 22 chests and 4 water buckets, none of which any list mentioned.

So invert it: when banking for space has otherwise failed, deposit everything
that is *not* protected, rather than only what was named in advance. The
keep-list is bounded and about survival and progression, which is a far more
stable thing to enumerate than the set of all junk. Items go into a chest, not
onto the ground, so a mistake here is recoverable.
"""

from __future__ import annotations

from typing import Any, Dict, Set


# Never banked while reclaiming space, matched as substrings so material tiers
# (wooden/stone/iron/diamond/netherite) and wood types are all covered.
_KEEP_TOKENS = (
    # tools and weapons -- banking these strands the bot mid-phase
    "pickaxe", "_axe", "sword", "shovel", "_hoe", "shears",
    # armour and defence
    "helmet", "chestplate", "leggings", "boots", "shield", "elytra",
    "bow", "arrow", "crossbow", "trident", "totem",
    # progression materials the End run cannot be completed without
    "diamond", "netherite", "emerald", "obsidian", "ender_", "eye_of_ender",
    "blaze_", "ghast_tear", "nether_star", "lapis",
    "iron_ingot", "raw_iron", "gold_ingot", "raw_gold", "iron_block",
    # anything edible; starvation is the fastest way to lose a run
    "cooked_", "_stew", "_soup", "bread", "apple", "carrot", "potato",
    "beetroot", "melon_slice", "sweet_berries", "honey_bottle", "dried_kelp",
    "rabbit", "mutton", "beef", "porkchop", "chicken", "cod", "salmon",
    # irreplaceable or single-use utility
    "enchanted_book", "potion", "name_tag", "music_disc", "map", "compass",
    "clock", "spawn_egg", "shulker_box", "bundle", "saddle", "lead",
    # a carried bed skips the night outright, which is worth far more than the
    # slot it occupies -- sleep_through_night and the boot sequence both use
    # one. "_bed" deliberately does not match "bedrock".
    "_bed", "brewing_stand",
)

# Banked only above a working floor: useful to carry, wasteful to hoard. The
# floors are what a bot needs in hand to keep building and eating between
# trips, not a full stock.
SPACE_RECLAIM_RETAIN_COUNTS: Dict[str, int] = {
    "minecraft:cobblestone": 64,
    "minecraft:cobbled_deepslate": 32,
    "minecraft:dirt": 32,
    "minecraft:torch": 16,
    "minecraft:stick": 16,
    "minecraft:coal": 32,
    "minecraft:charcoal": 32,
    "minecraft:crafting_table": 1,
    "minecraft:furnace": 1,
    "minecraft:chest": 2,
    # flint_and_steel lights the nether portal and does not get consumed by
    # use, so one is enough; bare flint is its crafting input.
    "minecraft:flint_and_steel": 1,
    "minecraft:flint": 1,
    "minecraft:wheat": 3,
    "minecraft:water_bucket": 1,
    "minecraft:bucket": 1,
    "minecraft:oak_planks": 32,
    "minecraft:birch_planks": 32,
    "minecraft:spruce_planks": 32,
    "minecraft:oak_log": 16,
    "minecraft:birch_log": 16,
    "minecraft:spruce_log": 16,
}


def is_protected(item_id: str) -> bool:
    """Return whether an item must stay carried while reclaiming space."""
    value = str(item_id or "").lower()
    if not value:
        return True
    return any(token in value for token in _KEEP_TOKENS)


def space_reclaim_deposit_items(carried: Dict[str, int]) -> Set[str]:
    """Return carried items that may be banked to reclaim inventory slots.

    Everything the player holds except the protected keep-list. Items with a
    retain floor are included: ``deposit_excess_to_chest`` applies
    ``SPACE_RECLAIM_RETAIN_COUNTS`` and banks only the surplus above it, so a
    bot keeps its working stock and shelves the rest.
    """
    return {
        item_id
        for item_id, count in (carried or {}).items()
        if int(count or 0) > 0 and not is_protected(item_id)
    }


def carried_items(client: Any) -> Dict[str, int]:
    """Read the live carried counts, tolerating a transport hiccup."""
    from .inventory import get_inventory

    try:
        return dict(get_inventory(client) or {})
    except Exception as exc:
        print(f"  STORAGE: could not read inventory for space reclaim ({exc})")
        return {}
