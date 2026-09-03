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
        "minecraft:netherite_ingot",
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


def test_redundant_flint_and_steel_is_shed_but_one_is_kept():
    """A1Bot 2026-08-26: 5 flint_and_steel, unstackable, all in _KEEP_TOKENS.

    Each sat in its own slot forever -- flint_and_steel never appeared in
    ``SPACE_RECLAIM_RETAIN_COUNTS`` so ``is_protected`` kept every copy
    unconditionally. That structurally capped free slots at 2 of the 3
    FOOD_AND_IRON needs, and no amount of chest capacity could fix it: the
    bot doesn't need more room, it needs to bank duplicates it will never
    use. Confirmed live: `manage_inventory` then fell back to placing an
    overflow chest, which failed in a cramped alcove every ~90s and ate most
    of a 600s gather timeout doing it.
    """
    carried = {"minecraft:flint_and_steel": 5, "minecraft:iron_pickaxe": 1}
    banked = _banked(carried)

    assert banked.get("minecraft:flint_and_steel") == 4
    assert carried["minecraft:flint_and_steel"] - banked["minecraft:flint_and_steel"] == 1


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


# A1Bot again on 2026-08-31, 35/36 slots, at (-305,-42,563) mining for the
# diamonds a diamond pickaxe needs. 598m from home storage -- past
# MAX_STORAGE_TRAVEL_DISTANCE -- so nothing could be banked, and unlike the
# 2026-08-16 capture above it carried no chests to build an overflow with.
# manage_inventory's named tiers matched only cobblestone (48, floor 128) and
# cobbled_deepslate (58, floor 64); whole-stack disposal skipped both, so it
# freed zero slots and retried every ~6s indefinitely.
A1BOT_DEEP_MINING_INVENTORY = {
    "minecraft:cobbled_deepslate": 58, "minecraft:cobblestone": 48,
    "minecraft:bread": 16, "minecraft:beef": 16, "minecraft:amethyst_shard": 12,
    "minecraft:carrot": 10, "minecraft:sweet_berries": 7, "minecraft:torch": 6,
    "minecraft:apple": 5, "minecraft:flint_and_steel": 4, "minecraft:lead": 4,
    "minecraft:stick": 4, "minecraft:redstone": 4, "minecraft:iron_ingot": 3,
    "minecraft:enchanted_book": 3, "minecraft:iron_sword": 2,
    "minecraft:oak_planks": 2, "minecraft:diamond": 1,
    "minecraft:iron_pickaxe": 1, "minecraft:wheat": 1,
    "minecraft:water_bucket": 1, "minecraft:golden_apple": 1,
    "minecraft:iron_shovel": 1, "minecraft:stone_pickaxe": 1,
    "minecraft:bucket": 1, "minecraft:string": 1, "minecraft:sand": 1,
    "minecraft:shield": 1, "minecraft:leather_boots": 1,
}


def _whole_stacks_droppable(carried):
    """Types disposal can actually shed, given whole-stack-only dropping.

    inventory_disposal.drop_items skips a slot when dropping it would fall
    below the retain floor, and it only moves whole stacks -- so an item under
    its own floor is undroppable no matter how useless it is.
    """
    return sorted(
        item_id
        for item_id in space_reclaim_deposit_items(carried)
        if carried[item_id] - carried[item_id] >= SPACE_RECLAIM_RETAIN_COUNTS.get(item_id, 0)
    )


def test_the_deep_mining_deadlock_frees_the_slots_ore_gathering_needs():
    """The inverted keep-list must free 3 slots where the named tiers freed 0.

    This is the whole point of routing manage_inventory's last resort through
    space_reclaim: nobody had listed amethyst_shard, redstone, string or sand
    as junk, so a name-based allow-list could never shed them, while an
    inverted keep-list sheds them precisely because nothing protects them.
    """
    droppable = _whole_stacks_droppable(A1BOT_DEEP_MINING_INVENTORY)

    assert droppable == [
        "minecraft:amethyst_shard",
        "minecraft:redstone",
        "minecraft:sand",
        "minecraft:string",
    ]
    assert len(droppable) >= IRON_MINING_SLOTS_REQUIRED


def test_the_deep_mining_deadlock_keeps_everything_that_matters():
    """Freeing slots must not cost the run its tools, food, or progression."""
    shed = space_reclaim_deposit_items(A1BOT_DEEP_MINING_INVENTORY)

    for item_id in (
        "minecraft:iron_pickaxe", "minecraft:stone_pickaxe",
        "minecraft:iron_sword", "minecraft:iron_shovel", "minecraft:shield",
        "minecraft:leather_boots", "minecraft:diamond",
        "minecraft:iron_ingot", "minecraft:bread", "minecraft:beef",
        "minecraft:golden_apple", "minecraft:enchanted_book",
    ):
        assert item_id not in shed, f"{item_id} must survive the sweep"


def test_reclaim_drop_tier_stops_once_enough_slots_are_free():
    """It must not keep shedding after the requirement is met."""
    from types import SimpleNamespace

    from baritone_client.common import inventory as inventory_api
    from baritone_client.common.space_reclaim import reclaim_drop_tier

    free = {"n": 0}
    calls = []

    def fake_drop(_client, item_ids, max_stacks=None, retain_counts=None):
        calls.append(max_stacks)
        free["n"] += 1
        return 1

    original_drop = inventory_api.drop_items
    original_free = inventory_api.free_inventory_slots
    original_get = inventory_api.get_inventory
    inventory_api.drop_items = fake_drop
    inventory_api.free_inventory_slots = lambda _c: free["n"]
    inventory_api.get_inventory = lambda _c: dict(A1BOT_DEEP_MINING_INVENTORY)
    try:
        assert reclaim_drop_tier(SimpleNamespace(), 3, {})
    finally:
        inventory_api.drop_items = original_drop
        inventory_api.free_inventory_slots = original_free
        inventory_api.get_inventory = original_get

    assert calls == [3, 2, 1], "must shrink its ask as slots come free, then stop"


def test_a_failed_disposal_says_which_step_refused(capsys):
    """A silent `continue` cost A1 47 blind cleanup failures in 20 minutes.

    Four unprotected, unfloored stacks sat in slots 6/9/12/20 while
    manage_inventory reported only "only 2/3 required slots are free" -- no
    way to tell "nothing was eligible" from "the throw is not landing". Each
    rejection path must now name itself, so the next look at a stuck bot
    starts from a reason instead of a guess.

    Here the throw never empties the slot, which is one of the two silent
    paths; the assertion pins the reason, not just that something printed.
    """
    from types import SimpleNamespace

    from baritone_client.common import inventory as api
    from baritone_client.common import inventory_disposal

    def dispatch(route, _payload=None):
        if route == "get_inventory":
            return {"inventory": [
                {"id": "minecraft:amethyst_shard", "slot": 6, "count": 12},
            ]}
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    original_free = api.free_inventory_slots
    api.free_inventory_slots = lambda _c: 1
    try:
        dropped = inventory_disposal.drop_items(
            client, ["minecraft:amethyst_shard"], max_stacks=3, retain_counts={}
        )
    finally:
        api.free_inventory_slots = original_free

    assert dropped == 0
    out = capsys.readouterr().out
    assert "none of 1 candidate stack(s) left the inventory" in out, out
    assert "not_emptied=1" in out, out


def test_a_disposal_with_no_eligible_stacks_stays_quiet(capsys):
    """Only a real refusal is worth a line; nothing eligible is not news."""
    from types import SimpleNamespace

    from baritone_client.common import inventory_disposal

    def dispatch(route, _payload=None):
        if route == "get_inventory":
            return {"inventory": [
                {"id": "minecraft:diamond", "slot": 3, "count": 2},
            ]}
        return {}

    dropped = inventory_disposal.drop_items(
        SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch)),
        ["minecraft:amethyst_shard"],
        max_stacks=3,
        retain_counts={},
    )

    assert dropped == 0
    assert "Drop items:" not in capsys.readouterr().out


def test_retain_floors_yield_rather_than_wedge_the_run(monkeypatch):
    """An inventory where every candidate is floored can never free a slot.

    Live on A1 2026-09-03 at (-8, 160, 9): 36 of 36 slots used, needing 3 free
    to smelt iron, cycling every ~24 seconds for hours without moving:

        Drop items: none of 14 candidate stack(s) left the inventory
                    (reserved=13, no_slot_gain=1)
        Inventory cleanup failed: only 1/3 required slots are free

    46 cobblestone sat under a floor of 64. The floors were doing exactly what
    they were written to do, and the effect was a permanently stuck run.
    """
    from baritone_client.common import space_reclaim

    calls = []
    free = {"n": 1}

    def drop_items(_client, items, max_stacks=None, retain_counts=None):
        calls.append({"floored": retain_counts is not None})
        if retain_counts is not None:
            return 0          # everything is under its floor
        free["n"] = 3         # unfloored pass sheds bulk
        return 2

    monkeypatch.setattr("baritone_client.common.inventory.drop_items", drop_items)
    monkeypatch.setattr(
        "baritone_client.common.inventory.free_inventory_slots",
        lambda _c: free["n"],
    )
    monkeypatch.setattr(
        space_reclaim, "carried_items", lambda _c: {"minecraft:cobblestone": 46}
    )
    monkeypatch.setattr(
        space_reclaim, "space_reclaim_deposit_items",
        lambda _carried: {"minecraft:cobblestone"},
    )

    from types import SimpleNamespace as _NS

    assert space_reclaim.reclaim_drop_tier(_NS(), 3)
    assert [c["floored"] for c in calls] == [True, False], calls


def test_the_break_glass_pass_only_sheds_what_the_keep_list_allows():
    """Tools, food, ores and portal materials must never reach it."""
    import inspect

    from baritone_client.common import space_reclaim

    source = inspect.getsource(space_reclaim.reclaim_drop_tier)
    # The unfloored drop must reuse `reclaim`, which is already filtered by
    # _KEEP_TOKENS -- not the raw carried inventory.
    assert "drop_items(client, reclaim, max_stacks=required - free)" in source
    assert "carried_items(client)" not in source.split("Break glass")[1]

    for protected in ("pickaxe", "diamond", "obsidian", "iron_ingot", "bread"):
        assert any(
            protected in token for token in space_reclaim._KEEP_TOKENS
        ), protected


def test_the_drop_is_measured_before_the_bot_can_pick_it_back_up():
    """A thrown stack is collectable again after 2 seconds.

    Live on A1 2026-09-03 at (-8, 160, 9): the slot emptied every time
    (`not_emptied` was always 0), yet all 14 unfloored candidates scored
    `no_slot_gain`, because the settle ran 2.25s -- past vanilla's pickup
    delay -- and the bot standing over the pile took it straight back before
    the count was read. Disposal could never free a slot, and the run sat
    wedged for hours.
    """
    from baritone_client.common import inventory_disposal

    assert inventory_disposal.PICKUP_SAFE_SETTLE_SECONDS < 2.0, (
        "the settle must land inside the pickup-delay window"
    )
    assert inventory_disposal.PICKUP_SAFE_SETTLE_SECONDS > 0
