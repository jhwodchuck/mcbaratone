"""Banking for space must work on items nobody listed in advance.

`store_surplus_in_chest` banks from an allow-list: `EARLY_GAME_EXCESS_ITEMS |
OVERFLOW_BULK_ITEMS`. That list has to anticipate every item a bot might pick
up, and anything it misses is carried forever.

A1Bot 2026-08-16 carried 33 item types. Exactly one (`leaf_litter`, 3 items)
was on the list, and its 64 cobblestone sat exactly on the 64 retain floor. So
every `deposit_excess_to_chest` opened a chest, moved zero stacks, closed it,
and moved on -- the log shows "deposited 0 excess stacks at home" over and
over while the bot toured its own storage. With 36/36 slots full,
`manage_inventory` could not free the 3 slots iron mining needs:

    Inventory needs 3 free slot(s); clearing verified low-value stacks...
    Inventory cleanup failed: only 1/3 required slots are free
    Initial iron mining paused because safe inventory space is unavailable.

FOOD_AND_IRON made no progress for hours and readiness sat at 0/7 with 0 iron,
while the bot accumulated 22 chests and 4 water buckets.

The fixture below is that exact inventory.
"""

import pytest

from baritone_client.common.space_reclaim import (
    SPACE_RECLAIM_RETAIN_COUNTS,
    is_protected,
    space_reclaim_deposit_items,
)


# A1Bot's live inventory at the moment it was deadlocked, 36/36 slots.
A1BOT_STUCK_INVENTORY = {
    "minecraft:chest": 22, "minecraft:coal": 6, "minecraft:oak_door": 2,
    "minecraft:iron_axe": 1, "minecraft:water_bucket": 4, "minecraft:flint": 1,
    "minecraft:egg": 9, "minecraft:birch_log": 3, "minecraft:wheat": 2,
    "minecraft:apple": 4, "minecraft:smooth_basalt": 18, "minecraft:raw_iron": 9,
    "minecraft:crafting_table": 5, "minecraft:lead": 1, "minecraft:calcite": 10,
    "minecraft:poppy": 3, "minecraft:bread": 13, "minecraft:iron_shovel": 1,
    "minecraft:gold_ingot": 2, "minecraft:string": 8, "minecraft:wooden_hoe": 1,
    "minecraft:leaf_litter": 3, "minecraft:dandelion": 1,
    "minecraft:amethyst_shard": 8, "minecraft:oak_planks": 17,
    "minecraft:oak_log": 3, "minecraft:bucket": 1,
    "minecraft:stone_pickaxe": 1, "minecraft:torch": 5,
    "minecraft:cobblestone": 64, "minecraft:birch_planks": 2,
    "minecraft:stick": 5, "minecraft:charcoal": 4,
}

IRON_MINING_SLOTS_REQUIRED = 3


def _banked(carried):
    """Item -> surplus count that would actually leave the inventory."""
    deposit = space_reclaim_deposit_items(carried)
    return {
        item_id: carried[item_id] - SPACE_RECLAIM_RETAIN_COUNTS.get(item_id, 0)
        for item_id in deposit
        if carried[item_id] - SPACE_RECLAIM_RETAIN_COUNTS.get(item_id, 0) > 0
    }


def test_the_deadlocked_inventory_frees_more_than_iron_mining_needs():
    """THE A1Bot bug: the allow-list freed 1 of 3 slots and mining stayed off."""
    banked = _banked(A1BOT_STUCK_INVENTORY)

    assert len(banked) >= IRON_MINING_SLOTS_REQUIRED, (
        f"only {len(banked)} slot(s) would be freed from a 36/36 inventory; "
        f"iron mining needs {IRON_MINING_SLOTS_REQUIRED}"
    )


@pytest.mark.parametrize(
    "item_id",
    [
        "minecraft:stone_pickaxe",   # no pickaxe means no mining at all
        "minecraft:iron_axe",
        "minecraft:iron_shovel",
        "minecraft:diamond_sword",
        "minecraft:iron_chestplate",
        "minecraft:shield",
        "minecraft:bow",
        "minecraft:arrow",
    ],
)
def test_never_banks_the_tools_or_armour_it_needs_to_survive(item_id):
    """Banking a bot's only pickaxe strands it exactly like a lost grave."""
    assert is_protected(item_id), f"{item_id} would be banked"


@pytest.mark.parametrize(
    "item_id",
    [
        "minecraft:bread", "minecraft:apple", "minecraft:cooked_beef",
        "minecraft:golden_carrot", "minecraft:baked_potato",
        "minecraft:cooked_salmon", "minecraft:rabbit_stew",
    ],
)
def test_never_banks_food(item_id):
    """Starvation is the fastest way to lose a run; food stays carried."""
    assert is_protected(item_id), f"{item_id} would be banked"


@pytest.mark.parametrize(
    "item_id",
    [
        "minecraft:diamond", "minecraft:raw_iron", "minecraft:iron_ingot",
        "minecraft:gold_ingot", "minecraft:obsidian", "minecraft:ender_pearl",
        "minecraft:blaze_rod", "minecraft:blaze_powder", "minecraft:emerald",
        "minecraft:flint_and_steel", "minecraft:netherite_ingot",
    ],
)
def test_never_banks_end_run_progression(item_id):
    """These are what the whole spawn-to-dragon objective is accumulating."""
    assert is_protected(item_id), f"{item_id} would be banked"


def test_the_chest_hoard_is_shed_but_a_working_pair_is_kept():
    """22 chests was itself a symptom; it must not swing to zero either."""
    banked = _banked(A1BOT_STUCK_INVENTORY)

    assert banked.get("minecraft:chest") == 20, (
        f"banked {banked.get('minecraft:chest')} of 22 chests; expected 20 "
        "with a working pair retained"
    )


def test_duplicate_buckets_are_shed_but_one_is_kept():
    """Four water buckets is hoarding; one is needed for lava and falls."""
    banked = _banked(A1BOT_STUCK_INVENTORY)

    assert banked.get("minecraft:water_bucket") == 3
    assert (
        A1BOT_STUCK_INVENTORY["minecraft:water_bucket"]
        - banked["minecraft:water_bucket"]
    ) == 1, "must keep exactly one water bucket"


def test_working_building_stock_survives_the_sweep():
    """Cobblestone at its floor is not surplus, so it must not be banked."""
    banked = _banked(A1BOT_STUCK_INVENTORY)

    assert "minecraft:cobblestone" not in banked, (
        "banked cobblestone that was exactly at the retain floor"
    )


def test_an_all_protected_inventory_banks_nothing():
    """No false positives: a lean survival kit is left completely alone."""
    lean = {
        "minecraft:iron_pickaxe": 1,
        "minecraft:iron_sword": 1,
        "minecraft:bread": 8,
        "minecraft:raw_iron": 12,
    }

    assert space_reclaim_deposit_items(lean) == set()


def test_an_empty_or_unreadable_inventory_is_safe():
    """A failed inventory read must not be treated as 'nothing is protected'."""
    assert space_reclaim_deposit_items({}) == set()
    assert is_protected(""), "an unknown id must default to protected"
    assert is_protected(None), "a missing id must default to protected"


# ---------------------------------------------------------------------------
# the fallback trigger, which is where this fix nearly failed
# ---------------------------------------------------------------------------
def _allow_list_slots(carried, selected_items, selected_retains):
    """Mirror of the trigger arithmetic in store_surplus_in_chest."""
    return sum(
        1
        for item_id, count in carried.items()
        if item_id in selected_items
        and int(count or 0) > selected_retains.get(item_id, 0)
    )


def test_one_listed_junk_stack_must_not_suppress_the_fallback():
    """The near-miss: A1Bot carried 3 leaf_litter, which IS on the allow-list.

    A trigger asking "can the allow-list move anything?" answers yes on that
    single stack, frees one slot of the three iron mining needs, and leaves the
    bot exactly as deadlocked. The trigger has to ask whether the allow-list
    frees ENOUGH slots.
    """
    from baritone_client.common.inventory import EARLY_GAME_EXCESS_ITEMS
    from baritone_client.common.storage_safety import (
        OVERFLOW_BULK_ITEMS,
        OVERFLOW_RETAIN_COUNTS,
    )

    selected = EARLY_GAME_EXCESS_ITEMS | OVERFLOW_BULK_ITEMS
    retains = dict(OVERFLOW_RETAIN_COUNTS)
    freed = _allow_list_slots(A1BOT_STUCK_INVENTORY, selected, retains)

    assert freed < IRON_MINING_SLOTS_REQUIRED, (
        f"fixture drift: the allow-list now frees {freed} slots on its own, so "
        "this no longer reproduces the deadlock"
    )
    assert freed > 0, (
        "fixture drift: the allow-list frees nothing, so a naive "
        "'can it move anything' trigger would accidentally work here"
    )


@pytest.mark.parametrize(
    "item_id",
    ["minecraft:white_bed", "minecraft:red_bed", "minecraft:cyan_bed"],
)
def test_never_banks_a_bed(item_id):
    """A carried bed skips the whole night; that beats the slot it costs.

    sleep_through_night and the boot sequence both rely on one, and the naked
    night-shelter path exists precisely because nights are dangerous.
    """
    assert is_protected(item_id), f"{item_id} would be banked"


def test_bedrock_is_not_mistaken_for_a_bed():
    """The "_bed" token must not accidentally protect terrain."""
    assert not is_protected("minecraft:bedrock")
