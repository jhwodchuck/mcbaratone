"""Adapters between supply shortfalls and absolute acquisition targets.

``ensure_supplies`` hands every strategy a *shortfall*: ``_missing_requirements``
reports ``required - current``. Most acquisition helpers take the opposite
thing -- an *absolute carried target* -- and early-return True once
``carried >= target``. Handing one an unconverted shortfall turns a
well-stocked item into a silent no-op that still reports success, so
``ensure_supplies`` re-checks, finds the same shortfall, and spins until it
times out.

Live on A1 2026-08-21: carrying 14 bread and needing 15, the shortfall of 1
satisfied ``14 >= 1`` instantly. FOOD_AND_IRON logged the same
"Ensuring supplies: {'minecraft:bread': 1}" line every ~5.5s for the full 600s
window, failed "Bake durable prepared food", retried the phase, and repeated on
an ~11.5 minute cycle for days -- blocking ``prepared_food_32``.

The gatherers' ``max(qty, N)`` floors do not protect against this. They set a
low bar a stocked inventory clears trivially: needing 30 cobblestone while
carrying 20 gives a shortfall of 10, and ``max(10, 16)`` is 16, which 20 already
satisfies. ``gather_gravel`` has no floor at all.

Callers outside ``DEFAULT_REQUIREMENT_STRATEGIES`` already pass real absolute
targets and must keep calling those helpers directly.
"""

from __future__ import annotations

import time
from typing import Any, Callable


def absolute_requirement(
    client: Any,
    item_id: str,
    shortfall: int,
    *,
    count_item: Callable[[Any, str], int],
) -> int:
    """Recover ``ensure_supplies``' original absolute requirement.

    ``_missing_requirements`` reports ``required - current``, so adding the
    carried count back reconstructs ``required`` exactly.
    """
    return count_item(client, item_id) + max(1, int(shortfall))


def craft_shortfall_with_table(
    client: Any,
    item_id: str,
    shortfall: int,
    *,
    count_item: Callable[[Any, str], int],
    craft_with_table: Callable[[Any, str, int], bool],
) -> bool:
    """Add a requested shortfall to the carried count before crafting."""
    target = absolute_requirement(
        client, item_id, shortfall, count_item=count_item
    )
    return craft_with_table(client, item_id, target)


#: Obsidian, ancient debris and the respawn anchor are diamond-tier only.
DIAMOND_TIER_PICKAXES = ("minecraft:diamond_pickaxe", "minecraft:netherite_pickaxe")
#: A pickaxe recipe's own head material. ``_craft_with_table`` resolves stick
#: and cobblestone dependencies (``ensure_tool_sticks``,
#: ``ensure_stone_material``) but has no equivalent for ore, so asking for the
#: tool alone dead-ends on "Missing ingredient 'minecraft:diamond'" rather
#: than going and mining any.
PICKAXE_HEAD_MATERIAL = {"minecraft:diamond_pickaxe": ("minecraft:diamond", 3)}


def mine_requiring_pickaxe(
    client: Any,
    block_id: str,
    quantity: int,
    *,
    durability: Callable[[Any, list], int],
    ensure: Callable[..., Any],
    mine: Callable[[Any, str, int], bool],
    pickaxes: tuple = DIAMOND_TIER_PICKAXES,
) -> bool:
    """Refuse a mine the carried tools physically cannot break.

    Baritone's ``mine`` route silently does nothing when no carried tool can
    break the target, and the caller reports success merely for dispatching
    it, so ``ensure_supplies`` re-checks an unchanged inventory and respins
    for its entire timeout -- the same false-success shape this module was
    written for, one layer lower.

    Live on A1 2026-08-29, carrying only a stone pickaxe: NETHER_AND_BLAZE
    logged "Gathering minecraft:obsidian x14" every ~6s across three full
    180s windows per attempt, then "Could not gather portal materials".
    Obsidian is diamond-tier, so not one of those swings could ever have
    landed. ``inventory._MANUAL_GRID_RECIPES`` already records why this
    matters: the portal, and therefore the whole Nether phase, is
    unreachable without a diamond pickaxe.
    """
    if durability(client, list(pickaxes)) > 0:
        return mine(client, block_id, quantity)
    wanted = pickaxes[0]
    # Ask for the head material in the same requirement set as the tool. The
    # crafter does not gather ore for a recipe, so requesting the pickaxe
    # alone reports "all methods failed ... (have 0, wanted 1)" without ever
    # mining a diamond -- the acquisition names the blocker but cannot clear
    # it. Listing both lets ensure_supplies route the ore through its own
    # gather_ores strategy first.
    head = PICKAXE_HEAD_MATERIAL.get(wanted)
    requirement = {wanted: 1}
    if head is not None:
        requirement[head[0]] = head[1]
    if not ensure(client, requirement, timeout=300).success:
        print(f"  {block_id} needs a {wanted.split(':')[-1]}; none available yet.")
        return False
    return mine(client, block_id, quantity)


def mine_until_satisfied(
    client: Any,
    block_id: str,
    quantity: int,
    *,
    timeout: float = 150.0,
    poll: float = 2.0,
) -> bool:
    """Mine one target to an absolute carried count, waiting for completion.

    ``_default_mine`` dispatches Baritone's ``mine`` route and returns True for
    the dispatch alone, and ``ensure_supplies`` discards that answer anyway --
    it only re-reads the inventory, so a strategy that never finishes looks
    exactly like one that is still working. Every other gatherer already avoids
    this: ``gather_stone`` and ``gather_ores`` dispatch once and then poll,
    and ``gather_stone``'s own comment records why -- "Reissuing ``mine`` on
    every idle poll used to reset these counters forever."

    Obsidian is the one strategy-table target that cannot survive that. Each
    re-dispatch re-enters Baritone's mine process, clearing its target and
    blacklist bookkeeping, and obsidian needs ~9.4s of uninterrupted breaking
    at diamond tier against a ~6s poll. Live A1 2026-09-02 logged
    "Gathering minecraft:obsidian x14" every ~6s for over a day at 0 carried,
    wandering 90 blocks out and back with a stale iron_sword in hand while
    obsidian sat within 64 blocks the whole time.

    So: put the right pickaxe in hand, start the process exactly once through
    ``_start_mine_process`` (which keeps the cancel-then-grace ordering a live
    Baritone crash was traced to), then poll the carried count and give up on a
    bounded idle rather than spinning forever.
    """
    from . import resources as api

    target = absolute_requirement(
        client, block_id, quantity, count_item=api.count_item
    )
    if api.count_item(client, block_id) >= target:
        return True

    api.equip_best_pickaxe(client, list(DIAMOND_TIER_PICKAXES))
    api._start_mine_process(client, [block_id], max(1, target))

    deadline = time.monotonic() + max(1.0, float(timeout))
    idle_checks = 0
    try:
        while time.monotonic() < deadline:
            if api.count_item(client, block_id) >= target:
                return True
            state = api._read_state_optional(
                client, retries=2, label=f"{block_id} mine wait"
            )
            if state is not None:
                if state.get("is_dead", False):
                    return False
                if float(state.get("health", 20) or 0) < 12.0:
                    print(f"  {block_id} mining paused: health too low to continue")
                    return False
                if state.get("is_pathing", False):
                    idle_checks = 0
                else:
                    idle_checks += 1
                    if idle_checks >= 4:
                        print(f"  No reachable {block_id} in the bounded search.")
                        return False
            time.sleep(max(0.5, float(poll)))
        print(f"  {block_id} mining hit its {timeout:.0f}s budget")
        return api.count_item(client, block_id) >= target
    finally:
        api._serialized_dispatch(
            client,
            "cancel",
            {},
            post_delay_seconds=api._BARITONE_CANCEL_GRACE_SECONDS,
        )


def mine_or_cast_obsidian(
    client: Any,
    block_id: str,
    quantity: int,
    *,
    timeout: float = 150.0,
    poll: float = 2.0,
) -> bool:
    """Mine reachable obsidian; if there is none left, cast some from lava.

    ``mine_until_satisfied`` reports "No reachable minecraft:obsidian in the
    bounded search" once the natural supply within reach is gone, and no amount
    of retrying changes that -- natural obsidian only exists where water has
    already met lava, so it is a fixed stock, not a slow one. Live A1
    2026-09-02: hours of that message while carrying a water bucket at a depth
    where lava is everywhere.

    So on failure, make more and mine again. One retry only: if casting
    produced obsidian the second mine finds it, and if it did not, spinning
    here just burns the phase budget that the caller still needs.
    """
    if mine_until_satisfied(
        client, block_id, quantity, timeout=timeout, poll=poll
    ):
        return True

    from . import resources as api
    from .navigation import goto
    from .obsidian_casting import cast_obsidian

    shortfall = max(1, int(quantity) - api.count_item(client, block_id))
    if cast_obsidian(client, shortfall, goto=goto) <= 0:
        return False
    return mine_until_satisfied(client, block_id, quantity, timeout=timeout, poll=poll)
