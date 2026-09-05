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



#: Even the last-resort pass keeps enough fuel to light a furnace. Everything
#: else here is replaceable by walking; a bot that cannot smelt is not.
LAST_DITCH_FLOORS = {
    "minecraft:coal": 4,
    "minecraft:charcoal": 4,
    **{item: 8 for item in ("minecraft:oak_planks", "minecraft:birch_planks",
                            "minecraft:spruce_planks", "minecraft:jungle_planks",
                            "minecraft:acacia_planks", "minecraft:dark_oak_planks")},
}

def reclaim_drop_tier(
    client: Any, required: int, retain_counts: Dict[str, int] | None = None
) -> bool:
    """Drop whatever the keep-list does not protect, as the last resort.

    ``manage_inventory``'s own tiers are a hardcoded *allow*-list of item
    names, so an inventory full of things nobody thought to name is
    undroppable no matter how worthless it is. The banking path already
    solved this by inverting the question -- ``space_reclaim_deposit_items``
    ships everything ``_KEEP_TOKENS`` does not protect -- but that answer was
    computed inside the chest tour and thrown away when no chest was
    reachable, so disposal never saw it.

    Live on A1 2026-08-31, stuck at 35/36 slots 598m from home (past
    ``MAX_STORAGE_TRAVEL_DISTANCE``, so no banking) and needing 3 free slots
    to mine the diamonds for a diamond pickaxe: of its 29 carried types the
    named tiers matched only cobblestone and cobbled_deepslate, and both sat
    under their retain floors. Whole-stack-only disposal
    (``inventory_disposal.drop_items``) then skipped both, freeing zero slots
    and failing every ~6s indefinitely. amethyst_shard, redstone, string and
    sand -- none of which has a consumer in any phase -- were sitting right
    there, unprotected and unfloored.

    Retain floors are merged caller-first, so a caller that guards building
    stone more strictly than the shared defaults keeps its stricter floor.
    """
    from .inventory import drop_items, free_inventory_slots

    floors = dict(SPACE_RECLAIM_RETAIN_COUNTS)
    floors.update(retain_counts or {})
    reclaim = sorted(space_reclaim_deposit_items(carried_items(client)))
    while reclaim and free_inventory_slots(client) < required:
        needed = required - free_inventory_slots(client)
        if drop_items(client, reclaim, max_stacks=needed, retain_counts=floors) <= 0:
            break
    free = free_inventory_slots(client)
    if free >= required:
        return True

    # Break glass. Retain floors exist to keep a working kit, not to wedge the
    # run: an inventory where every unprotected stack sits under its floor can
    # never free a slot, so the bot repeats the same failure until something
    # else kills it. Live on A1 2026-09-03 at (-8, 160, 9), 36/36 slots and
    # needing 3 free to smelt iron: 13 of 14 candidates were floored -- 46
    # cobblestone under a floor of 64 among them -- and it looped every ~24s
    # for hours without moving a block.
    #
    # `reclaim` already excludes everything _KEEP_TOKENS protects, so this can
    # only shed bulk: tools, armour, food, ores and portal materials are never
    # in it. Losing some cobblestone is strictly better than losing the run.
    #
    # Fuel keeps a small floor even here. Planks and logs are bulk by every
    # other measure, and dropping the last of them strands the bot in front of
    # a furnace it can no longer light -- which is exactly what happened on A1
    # 2026-09-03, where this pass took oak planks from 9 to 4 while the iron
    # phase was blocked on a furnace reporting "stalled without fuel".
    if reclaim and drop_items(
        client, reclaim, max_stacks=required - free, retain_counts=LAST_DITCH_FLOORS
    ) > 0:
        free = free_inventory_slots(client)
        if free >= required:
            print(
                f"  Inventory cleanup: dropped below retain floors to free "
                f"{free}/{required} slot(s) rather than stay wedged"
            )
            return True
    print(f"  Inventory cleanup failed: only {free}/{required} required slots are free")
    return False


def nearest_usable_container(client, snapshot, max_distance: float):
    """A catalogued chest with room, close enough to actually walk to.

    ``resolve_storage_location`` returns the *checkpointed* home, which goes
    stale the moment the bot relocates: banking is then skipped as "distant"
    while a perfectly good chest sits a block away. Live on A1 2026-09-03 at
    (-8, 160, 9), cycling for hours on "Gathering cleanup skipped distant home
    storage (412.6 blocks away)" with a verified chest at (-8, 160, 8) holding
    14 of 27 slots.

    Only containers the catalog has actually seen are considered, and only ones
    with a free slot, so this cannot send the bot to a chest that is missing or
    already full.
    """
    from .storage_catalog import catalog_for

    position = (snapshot or {}).get("block_position")
    if not isinstance(position, dict):
        return None
    try:
        px, py, pz = float(position["x"]), float(position["y"]), float(position["z"])
    except (KeyError, TypeError, ValueError):
        return None

    best = None
    best_distance = float(max_distance)
    try:
        containers = catalog_for(client).list_containers()
    except Exception:
        return None
    def current_storage_block(position):
        """Return a storage block observed at ``position`` right now.

        The catalog is intentionally last-known state.  A fresh block read is
        required before navigation so a removed or replaced container cannot
        become a banking target again.
        """
        transport = getattr(client, "transport", None)
        dispatch = getattr(transport, "dispatch", None)
        if not callable(dispatch):
            return None
        try:
            response = dispatch(
                "get_block",
                {"x": position[0], "y": position[1], "z": position[2]},
            )
        except Exception:
            return None
        if not isinstance(response, dict):
            return None
        if response.get("error") or response.get("status") == "error":
            return None
        block = response.get("data", response)
        if isinstance(block, dict) and isinstance(block.get("block"), dict):
            block = block["block"]
        if not isinstance(block, dict):
            return None
        block_id = block.get("id", block.get("block_id"))
        if not isinstance(block_id, str):
            return None
        if block_id not in {
            "minecraft:chest",
            "minecraft:trapped_chest",
            "minecraft:barrel",
        }:
            return None
        return block_id

    for row in containers:
        try:
            capacity = int(row.get("capacity_slots") or 0)
            occupied = int(row.get("occupied_slots") or 0)
            if capacity and occupied >= capacity:
                continue
            cx, cy, cz = float(row["x"]), float(row["y"]), float(row["z"])
        except (KeyError, TypeError, ValueError):
            continue
        distance = ((px - cx) ** 2 + (py - cy) ** 2 + (pz - cz) ** 2) ** 0.5
        if distance < best_distance:
            position = (int(cx), int(cy), int(cz))
            if current_storage_block(position) is None:
                continue
            best, best_distance = position, distance
    if best is not None:
        print(f"  Banking into nearer catalogued chest at {best} ({best_distance:.1f}m).")
    return best
