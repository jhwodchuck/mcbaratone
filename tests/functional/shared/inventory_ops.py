"""
Inventory Operations Module

Consolidated inventory management operations for functional tests.
Provides functions for inventory manipulation, chest operations, crafting, and smelting.

This module was refactored to unify shared functionality from actions/inventory_ops.py
and other disparate sources. The get_workshop_furnace function was moved here to provide
consistent access to furnace positions across all test suites.

Key functions:
- Inventory management: get_inventory_payload, select_item, safe_inventory_click
- Chest operations: open_supply_chest, withdraw_from_supply_chest, refresh_supply_slot_map
- Crafting: craft_bed_manual, ensure_crafting_table_open, craft_and_wait
- Smelting: smelt_in_furnace, get_workshop_furnace
"""

import time
from typing import Dict, List, Optional, Tuple, Union

from tests.functional.shared.block_ops import (
    block_id_at,
    is_liquid,
    move_near,
    place_block_at,
    bot_place_block,
    get_inv_slots,
    BASE_Y,
)
from tests.utils.mc_harness.interaction import robust_interact_block
from tests.utils.mc_harness.waits import close_screen
from tests.functional.shared.constants import (
    PLANK_ITEM_IDS,
    LOG_BLOCK_IDS,
    STRIPPED_LOG_BLOCK_IDS,
    WOOD_BLOCK_IDS,
    STRIPPED_WOOD_BLOCK_IDS,
)


def get_inventory_payload(ctx) -> List[Dict]:
    """
    Retrieve inventory list in a robust manner.

    Args:
        ctx: Test context

    Returns:
        List of inventory items
    """
    inv = ctx.get_inventory()
    if not inv:
        return []
    if isinstance(inv, dict):
        return inv.get("inventory", [])
    return inv


def get_slot_entry(inv_list: List[Dict], slot_idx: int) -> Optional[Dict]:
    """
    Find the inventory entry for a specific slot index.

    Args:
        inv_list: List of inventory items
        slot_idx: Slot index to find

    Returns:
        Inventory entry dict or None
    """
    for item in inv_list:
        if item.get("slot") == slot_idx:
            return item
    return None


def item_in_slot(inv_list: List[Dict], slot_idx: int) -> Tuple[Optional[str], int]:
    """
    Return (id, count) for a slot. Returns (None, 0) if empty.

    Args:
        inv_list: List of inventory items
        slot_idx: Slot index

    Returns:
        Tuple of (item_id, count)
    """
    item = get_slot_entry(inv_list, slot_idx)
    if not item:
        return None, 0
    return item.get("id"), item.get("count", 0)


def find_slot_with_item(inv_list: List[Dict], item_id: str) -> Optional[int]:
    """
    Find first slot containing specified item_id.

    Args:
        inv_list: List of inventory items
        item_id: Item ID to find

    Returns:
        Slot index or None
    """
    for item in inv_list:
        if item.get("id") == item_id and item.get("count", 0) > 0:
            return item.get("slot")
    return None


def find_empty_slot(inv_list: List[Dict], prefer_main: bool = True) -> Optional[int]:
    """
    Find an empty slot index.

    Args:
        inv_list: List of inventory items
        prefer_main: Prefer main inventory over hotbar

    Returns:
        Slot index or None
    """
    slots_to_check = list(range(9, 36)) + list(range(0, 9)) if prefer_main else list(range(0, 9)) + list(range(9, 36))
    occupied_slots = {item.get("slot") for item in inv_list if item.get("id") != "minecraft:air" and item.get("count", 0) > 0}

    for slot in slots_to_check:
        if slot not in occupied_slots:
            return slot
    return None




def select_item(ctx, item_id: str, allow_swap: bool = True) -> bool:
    """
    Select item in inventory (hotbar or swap to hotbar).

    Args:
        ctx: Test context
        item_id: Item ID to select
        allow_swap: Allow swapping from main inventory to hotbar

    Returns:
        True if item was selected
    """
    from baritone_client.common.inventory import find_item_slot
    from baritone_client.common.inventory import select_item as common_select

    # Use the common implementation
    return common_select(ctx.client, item_id, allow_swap)


def safe_inventory_click(ctx, slot: int, action: str, button: int = 0) -> None:
    """
    Perform a safe inventory click operation.

    Args:
        ctx: Test context
        slot: Slot index
        action: Action type (e.g., "PICKUP", "QUICK_MOVE")
        button: Button (0=left, 1=right)
    """
    ctx.client.transport.dispatch("inventory_click", {
        "slot": slot,
        "type": action,
        "button": button
    })
    time.sleep(0.1)  # Small delay for UI synchronization


_CRAFTING_SPACE_DISCARD_PRIORITY = (
    "minecraft:rotten_flesh",
    "minecraft:poisonous_potato",
    "minecraft:spider_eye",
    "minecraft:leaf_litter",
    "minecraft:beetroot_seeds",
    "minecraft:melon_seeds",
    "minecraft:pumpkin_seeds",
    "minecraft:wheat_seeds",
    "minecraft:wildflowers",
    "minecraft:birch_sapling",
    "minecraft:oak_sapling",
    "minecraft:spruce_sapling",
    "minecraft:grass_block",
    "minecraft:moss_block",
    "minecraft:dirt",
)


def ensure_crafting_output_space(ctx, screen=None) -> bool:
    """Ensure the active player/table crafting result has a destination.

    Minecraft cannot quick-move a crafted result into a completely full
    inventory. Early autonomous runs reached that state before attempting the
    first crafting table, leaving a valid table in result slot 0 forever. We
    only discard a deliberately low-value stack; valuable items are never
    selected implicitly.
    """

    screen = screen or ctx.client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    slots = data.get("slots", [])
    screen_type = data.get("type", "")
    if "Player" in screen_type or screen_type in {"class_1723", "PlayerScreenHandler"}:
        player_slot_range = range(9, 45)
    elif "Crafting" in screen_type or screen_type in {"class_1714", "CraftingScreenHandler"}:
        player_slot_range = range(10, 46)
    else:
        ctx.log_event(
            f"Cannot reserve crafting output space from screen {screen_type}"
        )
        return False

    # PlayerScreenHandler: 9-44. CraftingScreenHandler: 10-45. Use the
    # already-open handler's slot ids directly so hotbar translation cannot
    # accidentally target a crafting-grid or armor slot.
    player_slots = [
        slot
        for slot in slots
        if int(slot.get("slot", -1)) in player_slot_range
    ]
    if any(
        not slot.get("id")
        or slot.get("id") == "minecraft:air"
        or int(slot.get("count", 0)) <= 0
        for slot in player_slots
    ):
        return True

    for item_id in _CRAFTING_SPACE_DISCARD_PRIORITY:
        candidate = next(
            (slot for slot in player_slots if slot.get("id") == item_id),
            None,
        )
        if candidate is None:
            continue
        slot_id = int(candidate["slot"])
        count = int(candidate.get("count", 0))
        ctx.log_event(
            f"Inventory full; dropping low-value stack {item_id} x{count} "
            "to make room for crafting output"
        )
        safe_inventory_click(ctx, slot_id, "THROW", 1)
        for _ in range(20):
            refreshed = ctx.client.transport.dispatch("get_screen", {})
            refreshed_data = refreshed.get("data", refreshed)
            refreshed_slot = next(
                (
                    slot
                    for slot in refreshed_data.get("slots", [])
                    if int(slot.get("slot", -1)) == slot_id
                ),
                None,
            )
            if (
                refreshed_slot is None
                or refreshed_slot.get("id") == "minecraft:air"
                or int(refreshed_slot.get("count", 0)) <= 0
            ):
                return True
            time.sleep(0.1)
        return False

    # Mining commonly fills every remaining slot with cobblestone before the
    # first furnace craft.  If several stacks exist, sacrifice the smallest
    # one while retaining at least one recipe's eight blocks.  This is bounded
    # and does not broaden the discard policy to tools, ores, food, or unique
    # building materials.
    cobble_slots = [
        slot for slot in player_slots
        if slot.get("id") == "minecraft:cobblestone"
        and int(slot.get("count", 0)) > 0
    ]
    cobble_total = sum(int(slot.get("count", 0)) for slot in cobble_slots)
    candidates = [
        slot for slot in cobble_slots
        if cobble_total - int(slot.get("count", 0)) >= 8
    ]
    if candidates:
        candidate = min(candidates, key=lambda slot: int(slot.get("count", 0)))
        slot_id = int(candidate["slot"])
        count = int(candidate.get("count", 0))
        ctx.log_event(
            f"Inventory full; dropping redundant cobblestone stack x{count} "
            f"(retaining {cobble_total - count}) to make room for crafting output"
        )
        safe_inventory_click(ctx, slot_id, "THROW", 1)
        for _ in range(20):
            refreshed = ctx.client.transport.dispatch("get_screen", {})
            refreshed_data = refreshed.get("data", refreshed)
            refreshed_slot = next(
                (
                    slot for slot in refreshed_data.get("slots", [])
                    if int(slot.get("slot", -1)) == slot_id
                ),
                None,
            )
            if (
                refreshed_slot is None
                or refreshed_slot.get("id") == "minecraft:air"
                or int(refreshed_slot.get("count", 0)) <= 0
            ):
                return True
            time.sleep(0.1)
        return False

    # Cobblestone is the common case, not the only one. A bot deep underground
    # fills up on deepslate, tuff, netherrack and gravel instead, and refusing
    # to craft because none of it is literally cobblestone wedges the run: live
    # A1 2026-09-04 at (-442, -54, -601) could not place a crafting table for
    # want of one free slot, with 36 slots of exactly that kind of bulk.
    #
    # Reuse the progression keep-list rather than inventing a second policy --
    # it already protects tools, armour, food, ores and portal materials, so
    # what remains is bulk by construction.
    from baritone_client.common.space_reclaim import space_reclaim_deposit_items

    carried = {}
    for slot in player_slots:
        item_id = slot.get("id")
        if item_id and item_id != "minecraft:air":
            carried[item_id] = carried.get(item_id, 0) + int(slot.get("count", 0))
    shedable = space_reclaim_deposit_items(carried)
    spare = [
        slot for slot in player_slots
        if slot.get("id") in shedable and int(slot.get("count", 0)) > 0
    ]
    if spare:
        # Largest stack first: one throw, most room, least likely to be the
        # last of anything the bot is mid-way through using.
        candidate = max(spare, key=lambda slot: int(slot.get("count", 0)))
        slot_id = int(candidate["slot"])
        ctx.log_event(
            f"Inventory full; dropping {candidate.get('id')} "
            f"x{candidate.get('count')} to make room for crafting output"
        )
        safe_inventory_click(ctx, slot_id, "THROW", 1)
        time.sleep(0.3)
        return True

    ctx.log_event(
        "Inventory is full and contains no approved low-value stack to drop; "
        "crafting output cannot be collected safely"
    )
    return False


def ensure_player_crafting_output_space(ctx, screen=None) -> bool:
    """Backward-compatible wrapper for player 2x2 crafting callers."""
    return ensure_crafting_output_space(ctx, screen=screen)


#: Vanilla eye height for a standing player.
_EYE_HEIGHT = 1.62


def _line_of_sight_clear(
    ctx,
    eye: Tuple[float, float, float],
    center: Tuple[float, float, float],
    target: Tuple[int, int, int],
) -> bool:
    """Sample the straight line eye->center for a solid block.

    Mirrors the bridge's own interact_block raycast: a real line between two
    points, independent of look direction. Distance and walkability say
    nothing about whether that line is actually clear. The target block
    itself is solid by definition -- that is what we are aiming at -- so a
    sample that lands inside it (the last stretch of any short line
    necessarily does) is the goal being reached, not an obstruction.
    """
    ex, ey, ez = eye
    cx, cy, cz = center
    for step in range(1, 8):
        t = step / 8
        x = ex + (cx - ex) * t
        y = ey + (cy - ey) * t
        z = ez + (cz - ez) * t
        cell = (int(x // 1), int(y // 1), int(z // 1))
        if cell == target:
            continue
        block = block_id_at(ctx, *cell)
        if not block or block == "minecraft:void_air" or "air" not in block:
            return False
    return True


def _find_clear_approach(ctx, pos: Tuple[int, int, int]) -> Optional[Tuple[int, int, int]]:
    """Find a neighbour of pos with an actually-unobstructed view of it.

    find_stand_positions/move_near only check walkability -- never line of
    sight -- so a "close enough" candidate they pick can still have a wall
    or pillar between its eye and the target. That is exactly the shape of
    a raycast-miss failure, which no distance or walkability heuristic
    predicts. This checks the target's immediate neighbours, then the
    second ring, and verifies each candidate's sightline
    directly, since that is the one thing that actually predicts success --
    unlike an unbounded area scan, which costs hundreds of bridge round
    trips and still cannot tell you this.
    """
    tx, ty, tz = pos
    center = (tx + 0.5, ty + 0.5, tz + 0.5)
    immediate = (
        (tx + 1, ty, tz), (tx - 1, ty, tz),
        (tx, ty, tz + 1), (tx, ty, tz - 1),
        (tx + 1, ty, tz + 1), (tx - 1, ty, tz - 1),
        (tx + 1, ty, tz - 1), (tx - 1, ty, tz + 1),
    )
    # A1's floating chest has no usable immediate neighbour. A clear, supported
    # position two blocks north and one east opens it successfully. Extend the
    # bounded search to that ring rather than repeating an impossible ray.
    candidates = immediate + tuple(
        (tx + dx, ty, tz + dz)
        for dx in range(-2, 3) for dz in range(-2, 3)
        if max(abs(dx), abs(dz)) == 2
    )
    for cx, cy, cz in candidates:
        floor = block_id_at(ctx, cx, cy - 1, cz)
        if (not floor or "air" in floor or is_liquid(floor)
                or floor in {"minecraft:powder_snow", "minecraft:magma_block",
                             "minecraft:campfire", "minecraft:soul_campfire",
                             "minecraft:cactus", "minecraft:pointed_dripstone"}):
            continue
        stand = block_id_at(ctx, cx, cy, cz)
        if not stand or "air" not in stand:
            continue
        head = block_id_at(ctx, cx, cy + 1, cz)
        if not head or "air" not in head:
            continue
        eye = (cx + 0.5, cy + _EYE_HEIGHT, cz + 0.5)
        if _line_of_sight_clear(ctx, eye, center, pos):
            return (cx, cy, cz)
    return None


def do_open_container(
    ctx,
    pos: Tuple[int, int, int],
    timeout: float = 3.0,
    attempts: int = 4,
) -> bool:
    """
    Open container at specified position.

    Args:
        ctx: Test context
        pos: (x, y, z) coordinates
        timeout: Timeout for operation

    Returns:
        True if container was opened
    """
    # Check what's at the target position
    block_at = block_id_at(ctx, pos[0], pos[1], pos[2])
    px, py, pz = ctx.get_position()
    dist = ((px - pos[0]) ** 2 + (py - pos[1]) ** 2 + (pz - pos[2]) ** 2) ** 0.5
    vertical_gap = abs(py - pos[1])

    ctx.log_event(f"Opening container at {pos}: block={block_at}, player=({px:.1f},{py:.1f},{pz:.1f}), dist={dist:.1f}")

    if "chest" not in block_at and "container" not in block_at and "crafting_table" not in block_at and "furnace" not in block_at and "shulker" not in block_at and "barrel" not in block_at:
        ctx.log_event(f"No interactable block at {pos}, found: {block_at}")
        return False

    if dist > 5.0 or vertical_gap > 2:
        # Walk into reach before giving up. Callers that already approach (see
        # the chest path above) never hit this, but the smelting path opens a
        # furnace straight from a stored coordinate, and a bot that drifted a
        # couple of blocks while working could never recover: the step failed,
        # the phase retried, and it failed at the same distance forever.
        # Live 2026-08-01: Bot07 sat 6.1 blocks from its own furnace -- barely
        # outside the ~4.5 block reach -- and logged 419 retries with zero
        # blocks placed, because nothing ever moved it those two blocks.
        #
        # A large vertical_gap despite dist <= 5.0 is a different failure: a
        # multi-level base can put a bot one floor below its own chest, well
        # under Euclidean reach, with a solid floor sitting directly between
        # eye and target -- interact_block's own raycast then reports the
        # floor block, not the chest, on every attempt. Live A1 2026-09-06:
        # a bot at y=157 retried a chest at y=160 (dist=3.2) 24 times with
        # "Target is not visible on a real block ray" every time, because
        # dist alone said "close enough" and nothing ever moved it up a
        # level. force_reposition=True skips move_near's own close-enough
        # shortcut, which uses the same blind Euclidean check.
        ctx.log_event(
            f"Cannot reach container at {pos} from here: "
            f"dist={dist:.1f}, vertical_gap={vertical_gap:.1f}; approaching"
        )
        move_near(
            ctx, pos[0], pos[1], pos[2], timeout=15.0,
            force_reposition=vertical_gap > 2,
        )
        px, py, pz = ctx.get_position()
        dist = ((px - pos[0]) ** 2 + (py - pos[1]) ** 2 + (pz - pos[2]) ** 2) ** 0.5
        vertical_gap = abs(py - pos[1])
        if dist > 5.0 or vertical_gap > 2:
            ctx.log_event(
                f"Still cannot reach container at {pos} after approach: "
                f"dist={dist:.1f}, vertical_gap={vertical_gap:.1f}"
            )
            return False

    block_lower = (block_at or "").lower()
    expected_total_slots = None
    if "crafting_table" in block_lower:
        expected_total_slots = {46}
    elif any(token in block_lower for token in ("furnace", "smoker", "blast_furnace")):
        expected_total_slots = {39}
    elif any(token in block_lower for token in ("chest", "barrel", "shulker")):
        expected_total_slots = {63, 90}

    # Ensure no stale GUI is open (crafting/inventory screens can otherwise "mask" chest opens).
    close_screen(ctx, timeout=min(1.0, timeout))
    ctx.client.transport.dispatch("close_screen", {})
    time.sleep(0.05)

    def _screen_matches_expected() -> Tuple[bool, Dict]:
        screen = ctx.client.transport.dispatch("get_screen", {})
        data = screen.get("data", screen)
        total_slots = int(data.get("total_slots") or 0)
        if total_slots <= 0:
            total_slots = len(get_inv_slots(data))
        if expected_total_slots is None:
            if total_slots > 0 and int(data.get("sync_id", -1)) != -1:
                return True, data
            return False, data
        if total_slots not in expected_total_slots:
            return False, data
        if expected_total_slots == {46}:
            # A real crafting table screen and the player's own always-open
            # 2x2-crafting inventory screen both report exactly 46 total
            # slots, so slot count alone cannot tell a genuinely opened table
            # from a raycast miss that left the default player screen in
            # place. Live A1 2026-09-06: a blocked-sightline crafting-table
            # interact failed every one of 6 raw retries, but this check
            # still reported success on attempt 1/4 because the player
            # screen matched on slot count -- skipping the retry+reposition
            # loop below entirely and only surfacing as a failure later, in
            # the manual recipe grid's own (correct) screen-type check.
            screen_type = str(data.get("type", ""))
            if "Player" in screen_type or screen_type in {"class_1723", "PlayerScreenHandler"}:
                return False, data
        return True, data

    # Retry opening; we frequently see a stale 46-slot screen returned. Callers
    # probing an untrusted catalog landmark may choose one bounded attempt so a
    # blocked chest cannot hold the player exposed for the full retry cycle.
    attempts = max(1, int(attempts))
    per_attempt_timeout = max(0.3, timeout / attempts)
    last_data: Dict = {}
    for attempt in range(1, attempts + 1):
        close_screen(ctx, timeout=min(0.8, per_attempt_timeout))
        ctx.client.transport.dispatch("close_screen", {})
        time.sleep(0.05)

        ok_interact = robust_interact_block(
            ctx, pos[0], pos[1], pos[2], retries=6, interval=0.25
        )
        if not ok_interact:
            ctx.log_event(f"Container interact failed (attempt {attempt}/{attempts}) at {pos}")
            # A missed raycast at "in range" distance means something is
            # physically between eye and target -- a different floor
            # (vertical_gap catches that above), but also a wall corner or
            # pillar cutting the diagonal at the SAME floor, which no
            # distance check predicts in advance. Live A1 2026-09-06: a bot
            # 2.2 blocks from its own crafting table, same y-level, failed
            # this raycast on every one of at least 7 consecutive campaign
            # attempts because a snow_block sat on the direct line between
            # its eye and the table's center.
            #
            # move_near(force_reposition=True) was tried here first and made
            # this worse: find_stand_positions' radius=3 area scan is ~600
            # sequential block_id_at calls with no line-of-sight check at
            # all, which took ~80s on live A1 network latency, starved
            # concurrent bridge users badly enough to trip a "Hunger Check
            # Failed: Timeout waiting for bridge response", and still ended
            # up re-selecting the same unobstructed-by-its-own-criteria but
            # sightline-blocked candidate every time. _find_clear_approach
            # checks a bounded pair of neighbour rings and verifies
            # the one thing that actually predicts success -- a real
            # sightline -- for a small fraction of the cost.
            if attempt < attempts:
                approach = _find_clear_approach(ctx, pos)
                if approach is not None:
                    from tests.utils.mc_harness.actions import do_goto

                    do_goto(
                        ctx, {"x": approach[0], "y": approach[1], "z": approach[2]},
                        timeout=8.0, arrival_radius=1.0,
                        require_arrival=True, allow_incomplete=False,
                    )
                else:
                    ctx.log_event(f"No line-of-sight approach found near {pos}")

        deadline = time.time() + per_attempt_timeout
        while time.time() < deadline:
            ok_screen, data = _screen_matches_expected()
            last_data = data
            if ok_screen:
                return True
            time.sleep(0.05)

        ctx.log_event(
            "Container UI mismatch after interact. "
            f"attempt={attempt}/{attempts} "
            f"expected_total_slots={sorted(expected_total_slots) if expected_total_slots else None} "
            f"screen_type={last_data.get('type', '')} sync_id={last_data.get('sync_id', -1)} "
            f"total_slots={last_data.get('total_slots', 0)}"
        )

    ctx.log_event(
        "Container UI did not open. "
        f"expected_total_slots={sorted(expected_total_slots) if expected_total_slots else None} "
        f"screen_type={last_data.get('type', '')} sync_id={last_data.get('sync_id', -1)} total_slots={last_data.get('total_slots', 0)}"
    )
    return False


def do_close_container(ctx) -> None:
    """
    Close the current container screen.

    Args:
        ctx: Test context
    """
    close_screen(ctx, timeout=1.0)
    ctx.client.transport.dispatch("close_screen", {})
    time.sleep(0.05)


def withdraw_from_supply_chest(ctx, suite_state: Dict, item_counts: Dict[str, int]) -> bool:
    """
    Withdraw specified items from supply chests.

    Args:
        ctx: Test context
        suite_state: Suite state dict
        item_counts: Dict of item_id -> count needed

    Returns:
        True if withdrawal successful
    """
    slot_map = refresh_supply_slot_map(ctx, suite_state)
    if not slot_map:
        ctx.log_event("No supply slot map available")
        return False

    chest_positions = suite_state.get("supply_chest_positions")
    if not chest_positions:
        chest_positions = suite_state.get("T1000", {}).get("chest_positions")

    if not chest_positions:
        pos = suite_state.get("T1000", {}).get("chest_pos") or (0, BASE_Y, 0)
        chest_positions = [pos]

    # Verify sufficiency
    missing_any = False
    for item_id, needed in item_counts.items():
        entries = slot_map.get(item_id, [])
        calc_count = sum(e["count"] for e in entries)
        if calc_count < needed:
            ctx.log_event(f"Not enough {item_id}: have {calc_count}, need {needed}")
            missing_any = True

    if missing_any:
        return False

    slots_to_move = {}
    for item_id, needed in item_counts.items():
        entries = slot_map.get(item_id, [])
        if not entries:
            ctx.log_event(f"No entries in slot_map for {item_id}")
            continue
        remaining = needed
        for entry in entries:
            key = entry["chest"]
            slots_to_move.setdefault(key, []).append(entry["slot"])
            remaining -= entry["count"]
            if remaining <= 0:
                break
    
    ctx.log_event(f"Slots to move by chest: {slots_to_move}")

    overall_ok = True
    for i, pos in enumerate(chest_positions):
        key = "main" if i == 0 else f"overflow_{i}"
        target_slots = slots_to_move.get(key)
        if not target_slots:
            ctx.log_event(f"No slots to move for {key} at {pos}")
            continue

        ctx.log_event(f"Moving items from {key} chest at {pos}: slots {target_slots}")
        
        # Move to chest before opening
        move_near(ctx, pos[0], pos[1], pos[2], timeout=10.0)
        
        if not do_open_container(ctx, pos, timeout=3.0):
            ctx.log_event(f"Failed to open {key} chest at {pos}")
            overall_ok = False
            continue

        processed_slots = set()
        for slot in target_slots:
            if slot in processed_slots:
                continue
            ctx.log_event(f"Quick-moving slot {slot} from {key}")
            safe_inventory_click(ctx, slot, "QUICK_MOVE")
            time.sleep(0.05)
            processed_slots.add(slot)

        do_close_container(ctx)

    return overall_ok


def ensure_item_from_supply(ctx, item_id: str, count: int, suite_state: Dict) -> bool:
    """
    Ensure specific item count is available from supply.

    Args:
        ctx: Test context
        item_id: Item ID
        count: Required count
        suite_state: Suite state dict

    Returns:
        True if item is available
    """
    if ctx.has_item(item_id, count):
        return True
    if not open_supply_chest(ctx, suite_state):
        ctx.log_event(f"Failed to open supply chest to fetch {item_id}")
        return False
    screen = ctx.client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    slots = get_inv_slots(data)
    for slot in slots:
        if slot.get("id") == item_id and slot.get("slot") is not None and slot.get("slot") < 54:
            safe_inventory_click(ctx, slot["slot"], "QUICK_MOVE")
            time.sleep(0.05)
            if ctx.has_item(item_id, count):
                break
    do_close_container(ctx)
    if ctx.has_item(item_id, count):
        return True
    overflow_pos = suite_state.get("supply_overflow_pos")
    if not overflow_pos:
        return False
    if not do_open_container(ctx, overflow_pos, timeout=3.0):
        return False
    screen = ctx.client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    slots = get_inv_slots(data)
    for slot in slots:
        if slot.get("id") == item_id and slot.get("slot") is not None and slot.get("slot") < 27:
            safe_inventory_click(ctx, slot["slot"], "QUICK_MOVE")
            time.sleep(0.05)
            if ctx.has_item(item_id, count):
                break
    do_close_container(ctx)
    return ctx.has_item(item_id, count)


def smelt_in_furnace(ctx, furnace_pos: Tuple[int, int, int], input_id: str, fuel_id: str,
                      output_id: str, output_count: int, wait_per_item: float = 10.5) -> bool:
    """
    Smelt items in a furnace.

    Places a furnace if none exists, loads it with input and fuel items,
    waits for smelting to complete, then retrieves the output.

    Args:
        ctx: Test context
        furnace_pos: (x, y, z) coordinates of the furnace
        input_id: Item ID to smelt (e.g., "minecraft:iron_ore")
        fuel_id: Fuel item ID (e.g., "minecraft:coal")
        output_id: Expected output item ID (e.g., "minecraft:iron_ingot")
        output_count: Minimum number of output items required
        wait_per_item: Seconds to wait per item being smelted

    Returns:
        True if smelting successful and output items obtained.

    Example:
        >>> # Smelt 8 iron ore into iron ingots
        >>> success = smelt_in_furnace(ctx, (10, 80, 10), "minecraft:iron_ore",
        ...                            "minecraft:coal", "minecraft:iron_ingot", 8)
        >>> assert success
    """
    starting_output_count = ctx.count_item(output_id)
    target_output_count = starting_output_count + output_count

    if "furnace" not in block_id_at(ctx, furnace_pos[0], furnace_pos[1], furnace_pos[2]):
        if not bot_place_block(ctx, furnace_pos[0], furnace_pos[1], furnace_pos[2], "minecraft:furnace"):
            ctx.log_event("Failed to place furnace for smelting")
            return False

    if not do_open_container(ctx, furnace_pos, timeout=3.0):
        ctx.log_event("Failed to open furnace for smelting")
        return False

    screen = ctx.client.transport.dispatch("get_screen", {})
    slots = get_inv_slots(screen.get("data", screen))

    input_slot_idx = None
    fuel_slot_idx = None

    for i, item in enumerate(slots):
        if i < 3:
            continue
        if not item:
            continue
        if item.get("id") == input_id and input_slot_idx is None:
            input_slot_idx = i
        elif item.get("id") == fuel_id and fuel_slot_idx is None:
            fuel_slot_idx = i

    if input_slot_idx is None or fuel_slot_idx is None:
        ctx.log_event(f"Missing smelt items in inventory")
        do_close_container(ctx)
        return False

    safe_inventory_click(ctx, fuel_slot_idx, "QUICK_MOVE")
    time.sleep(0.3)
    safe_inventory_click(ctx, input_slot_idx, "QUICK_MOVE")
    time.sleep(0.3)

    # Keep the bridge connection active while the furnace runs.  A single long
    # sleep can leave the TCP session idle long enough for the next inventory
    # click to time out, even though the smelting itself completed in-game.
    deadline = time.monotonic() + max(12.0, output_count * wait_per_item + 15.0)
    while time.monotonic() < deadline:
        # If an earlier click completed despite a client-side timeout, accept
        # the verified player inventory instead of clicking the empty output.
        if ctx.count_item(output_id) >= target_output_count:
            do_close_container(ctx)
            return True

        screen = ctx.client.transport.dispatch("get_screen", {})
        screen_slots = get_inv_slots(screen.get("data", screen))
        output_slot = next(
            (item for item in screen_slots if item and item.get("slot") == 2),
            screen_slots[2] if len(screen_slots) > 2 else None,
        )
        if (
            output_slot
            and output_slot.get("id") == output_id
            and output_slot.get("count", 0) >= output_count
        ):
            break
        time.sleep(1.0)
    else:
        ctx.log_event(
            f"Timed out waiting for {output_count} {output_id} in furnace output"
        )
        do_close_container(ctx)
        return False

    safe_inventory_click(ctx, 2, "QUICK_MOVE")
    time.sleep(0.3)

    do_close_container(ctx)
    return ctx.count_item(output_id) >= target_output_count


def craft_bed_manual(ctx, bed_id: str = "minecraft:white_bed") -> bool:
    """
    Manually craft a bed through crafting interface.

    Requires wool and planks in inventory. Uses the 3x3 crafting grid with
    wool in the top row and planks in the middle row.

    Args:
        ctx: Test context
        bed_id: Bed item ID (e.g., "minecraft:white_bed")

    Returns:
        True if crafting successful

    Example:
        >>> # Craft a white bed (requires 3 wool and 3 planks in inventory)
        >>> success = craft_bed_manual(ctx, "minecraft:white_bed")
        >>> assert success
    """
    screen = ctx.client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    screen_type = data.get("type", "")
    if (
        "Crafting" not in screen_type
        and screen_type not in {"class_1714", "class_1723", "CraftingScreenHandler"}
    ):
        state = ctx.get_state()
        state_screen = state.get("screen", "")
        if "craft" not in state_screen.lower():
            ctx.log_event(f"Bed craft failed: wrong screen {screen_type}")
            return False
    slots = data.get("slots", [])
    if not slots:
        ctx.log_event("Bed craft failed: no slots in crafting screen")
        return False

    def find_slot(predicate, min_count: int = 1):
        for slot in slots:
            if predicate(slot) and slot.get("count", 0) >= min_count:
                return slot.get("slot")
        return None

    wool_slot = find_slot(lambda s: s.get("id", "").endswith("_wool"), min_count=3)
    plank_slot = find_slot(lambda s: s.get("id", "").endswith("_planks"), min_count=3)
    if wool_slot is None or plank_slot is None:
        ctx.log_event(f"Bed craft missing ingredients")
        return False

    for grid_slot in range(1, 10):
        slot_info = next((s for s in slots if s.get("slot") == grid_slot), None)
        if slot_info and slot_info.get("id") not in ("minecraft:air", None) and slot_info.get("count", 0) > 0:
            safe_inventory_click(ctx, grid_slot, "QUICK_MOVE")
            time.sleep(0.05)

    def place_one_each(source_slot: int, target_slots: List[int]) -> None:
        safe_inventory_click(ctx, source_slot, "PICKUP", button=0)
        time.sleep(0.05)
        for target in target_slots:
            safe_inventory_click(ctx, target, "PICKUP", button=1)
            time.sleep(0.05)
        safe_inventory_click(ctx, source_slot, "PICKUP", button=0)
        time.sleep(0.05)

    place_one_each(wool_slot, [1, 2, 3])
    place_one_each(plank_slot, [4, 5, 6])

    safe_inventory_click(ctx, 0, "QUICK_MOVE")
    time.sleep(0.1)
    return ctx.has_item(bed_id)


def craft_door_manual(ctx, door_id: str = "minecraft:oak_door") -> bool:
    """
    Manually craft a door (2 columns of 3 planks).
    """
    ctx.log_event("Door craft: Starting manual door crafting")
    
    # Wait a moment for screen to fully open
    time.sleep(0.5)
    
    # Try getting screen with retry
    slots = []
    for attempt in range(5):
        screen = ctx.client.transport.dispatch("get_screen", {})
        data = screen.get("data", screen)
        slots = data.get("slots", [])
        if slots and len(slots) > 10:
            break
        ctx.log_event(f"Door craft: Wait for screen (attempt {attempt+1}, got {len(slots)} slots)")
        time.sleep(0.5)
    
    ctx.log_event(f"Door craft: Got screen with {len(slots)} slots")
    
    if not slots:
        ctx.log_event("Door craft failed: no slots in screen")
        return False

    # Clear stale ingredients from the 3x3 grid.  Interrupted native/auto
    # craft attempts can leave planks in arbitrary cells; adding the door
    # recipe on top of those cells produces an empty result slot.
    for grid_slot in range(1, 10):
        slot_info = next((s for s in slots if s.get("slot") == grid_slot), None)
        if slot_info and slot_info.get("id") not in ("minecraft:air", None) and slot_info.get("count", 0) > 0:
            safe_inventory_click(ctx, grid_slot, "QUICK_MOVE")
            time.sleep(0.05)

    screen = ctx.client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    slots = data.get("slots", [])
    
    # Debug: log all plank slots found
    plank_slots_found = []
    for slot in slots:
        slot_idx = slot.get("slot", -1)
        item_id = slot.get("id", "")
        count = slot.get("count", 0)
        if "_planks" in item_id and count > 0:
            plank_slots_found.append(f"slot{slot_idx}:{item_id}x{count}")
    ctx.log_event(f"Door craft: Plank slots found: {plank_slots_found}")
        
    def find_plank_slot(min_count: int = 6):
        """Find a plank slot in player inventory (slots 10+ for crafting table screen)."""
        for slot in slots:
            slot_idx = slot.get("slot", -1)
            # Crafting table screen: slots 1-9 are grid, 0 is output, 10+ is player inventory
            if slot_idx < 10:
                continue
            if slot.get("id", "").endswith("_planks") and slot.get("count", 0) >= min_count:
                return slot_idx
        return None
        
    plank_slot = find_plank_slot(min_count=6)
    if plank_slot is None:
        ctx.log_event("Door craft failed: missing 6 planks in inventory (checked slots 10+)")
        return False
        
    ctx.log_event(f"Door craft: found planks at slot {plank_slot}")
    
    # Recipe: Left and Middle columns (1,4,7 and 2,5,8)
    target_slots = [1, 4, 7, 2, 5, 8]
    
    def place_one_each(source_slot: int, target_slots: List[int]) -> None:
        safe_inventory_click(ctx, source_slot, "PICKUP", button=0)
        time.sleep(0.05)
        for target in target_slots:
            safe_inventory_click(ctx, target, "PICKUP", button=1)
            time.sleep(0.05)
        safe_inventory_click(ctx, source_slot, "PICKUP", button=0)
        time.sleep(0.05)
        
    place_one_each(plank_slot, target_slots)
    
    # Check result slot before taking
    screen = ctx.client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    result_slot = None
    for s in data.get("slots", []):
        if s.get("slot") == 0:
            result_slot = s
            break
    ctx.log_event(f"Door craft: Result slot before taking: {result_slot}")
    
    safe_inventory_click(ctx, 0, "QUICK_MOVE")
    time.sleep(0.3)
    
    # Check if door is in inventory using screen slots (not base inventory)
    screen = ctx.client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    for s in data.get("slots", []):
        if s.get("slot", -1) >= 10 and "_door" in s.get("id", ""):
            ctx.log_event(f"Door craft: Found door in slot {s.get('slot')}")
            return True
    
    ctx.log_event("Door craft: Door not found in inventory after crafting")
    return False


def ensure_crafting_table_open(ctx, table_pos: Optional[Tuple[int, int, int]] = None, suite_state: Optional[Dict] = None) -> bool:
    """
    Ensure crafting table is available and open.

    Args:
        ctx: Test context
        table_pos: Optional table position
        suite_state: Optional suite state

    Returns:
        True if table is open
    """
    px, py, pz = ctx.get_position()
    if table_pos is None:
        # Prefer a real nearby table before choosing a placement candidate.
        # The house builder deliberately places one at an exact interior
        # coordinate; blindly selecting a new candidate wastes four planks
        # and can starve the following chest recipe.
        found = []
        try:
            response = ctx.client.transport.dispatch(
                "find_blocks",
                {"blocks": ["minecraft:crafting_table"], "radius": 8, "limit": 64},
            )
            found = response.get("found", [])
        except Exception as exc:
            ctx.log_event(f"Crafting table scan failed: {exc}")

        if found:
            nearest = min(
                found,
                key=lambda value: float(value.get("distance", float("inf"))),
            )
            table_pos = (
                int(nearest["x"]),
                int(nearest["y"]),
                int(nearest["z"]),
            )
            ctx.log_event(f"Found existing crafting table at {table_pos}")
        else:
            from tests.functional.shared.block_ops import find_place_pos_near
            table_pos = find_place_pos_near(ctx, int(px) + 1, int(py), int(pz))
    elif isinstance(table_pos, dict):
        table_pos = (table_pos.get("x", int(px)), table_pos.get("y", int(py)), table_pos.get("z", int(pz)))

    ctx.log_event(f"Crafting table target position: {table_pos}")

    # A known table that is already within reach should be opened from the
    # current safe tile.  The generic stand-position search optimizes for a
    # radius around the block; inside a compact house that can select a tile
    # outside the wall and expose the player during a slow manual craft.
    current_block = block_id_at(ctx, *table_pos)
    px, py, pz = ctx.get_position()
    distance = (
        (px - table_pos[0]) ** 2
        + (py - table_pos[1]) ** 2
        + (pz - table_pos[2]) ** 2
    ) ** 0.5
    if "crafting_table" in current_block and distance <= 4.5:
        if do_open_container(ctx, table_pos, timeout=3.0):
            if suite_state is not None:
                suite_state["crafting_table_pos"] = table_pos
            return True

    candidates = [table_pos]
    candidates.append((table_pos[0] + 1, table_pos[1], table_pos[2]))
    candidates.append((table_pos[0] - 1, table_pos[1], table_pos[2]))
    candidates.append((table_pos[0], table_pos[1], table_pos[2] + 1))
    candidates.append((table_pos[0], table_pos[1], table_pos[2] - 1))

    for i, (tx, ty, tz) in enumerate(candidates):
        tx, ty, tz = int(tx), int(ty), int(tz)

        current_block = block_id_at(ctx, tx, ty, tz)
        if current_block and "air" not in current_block and "crafting_table" not in current_block:
            continue

        px, py, pz = ctx.get_position()
        if int(px) == tx and int(pz) == tz:
            move_near(ctx, tx + 2, ty, tz, timeout=5.0)

        move_near(ctx, tx, ty, tz, timeout=8.0)

        # Retry block detection to handle timing issues (block may be placed but state not updated yet)
        for check_attempt in range(5):
            detected_block = block_id_at(ctx, tx, ty, tz)
            if "crafting_table" in detected_block:
                if suite_state is not None:
                    suite_state["crafting_table_pos"] = (tx, ty, tz)
                return bool(do_open_container(ctx, (tx, ty, tz), timeout=3.0))
            if check_attempt < 4:
                time.sleep(0.5)

        if bot_place_block(ctx, tx, ty, tz, "minecraft:crafting_table", allow_move=True):
            if suite_state is not None:
                suite_state["crafting_table_pos"] = (tx, ty, tz)
            if do_open_container(ctx, (tx, ty, tz), timeout=3.0):
                return True

        ctx.log_event(f"Candidate {i} failed at {tx},{ty},{tz}")

    ctx.log_event("All crafting table placement candidates failed.")
    return False


def open_supply_chest(ctx, suite_state: Dict) -> bool:
    """
    Open the supply chest at configured position.

    Args:
        ctx: Test context
        suite_state: Suite state dict

    Returns:
        True if chest opened
    """
    pos = suite_state.get("T1000", {}).get("chest_pos")
    if not pos:
        pos = (0, BASE_Y, 0)
    move_near(ctx, pos[0], pos[1], pos[2], timeout=15.0)
    return bool(do_open_container(ctx, pos, timeout=3.0))


_MISSING_ITEM_FALLBACKS = {
    "#logs": "minecraft:oak_log",
    "#planks": "minecraft:oak_planks",
    "#coals": "minecraft:coal",
    "#wool": "minecraft:white_wool",
    "#stone_tool_material": "minecraft:cobblestone",
    "any_logs": "minecraft:oak_log",
    "any_planks": "minecraft:oak_planks",
    "any_coal": "minecraft:coal",
    "any_wool": "minecraft:white_wool",
}


def _normalize_missing_requirements(missing: List[Dict]) -> Dict[str, int]:
    normalized: Dict[str, int] = {}
    for entry in missing:
        item_id = entry.get("item") or entry.get("id")
        count = entry.get("count", 0)
        if not item_id or count <= 0:
            continue
        if item_id in {"unknown", "minecraft:air"}:
            continue
        item_id = _MISSING_ITEM_FALLBACKS.get(item_id, item_id)
        normalized[item_id] = normalized.get(item_id, 0) + int(count)
    return normalized


def _attempt_withdraw_for_craft(ctx, item_id: str, count: int, suite_state: Dict) -> None:
    from baritone_client.common.inventory import check_craft

    try:
        craft_info = check_craft(ctx.client, item_id, count=count)
    except Exception as exc:
        ctx.log_event(f"Craft precheck failed for {item_id}: {exc}")
        return

    missing = craft_info.get("missing", [])
    needed = _normalize_missing_requirements(missing)
    if not needed:
        return

    if not withdraw_from_supply_chest(ctx, suite_state, needed):
        ctx.log_event(f"Supply withdrawal failed for {item_id}: {needed}")


def craft_and_wait(
    ctx,
    item_id: str,
    count: int = 1,
    timeout: float = 5.0,  # Increased default from 2.0
    batch: int = 16,
    suite_state: Optional[Dict] = None,
) -> bool:
    """
    Craft an item and wait for it to appear.

    Args:
        ctx: Test context
        item_id: Item to craft
        count: Number to craft
        timeout: Wait timeout
        batch: Batch size for large craft counts
        suite_state: Optional suite state for supply chest withdrawals

    Returns:
        True if crafting successful
    """
    if count <= 0:
        return True

    if suite_state is not None:
        _attempt_withdraw_for_craft(ctx, item_id, count, suite_state)

    batch = max(1, batch)
    use_batches = count > batch
    remaining = count
    crafted = 0
    start_count = ctx.count_item(item_id)
    
    ctx.log_event(f"DEBUG: Crafting {item_id} x{count}. Start count: {start_count}")

    while remaining > 0:
        craft_count = remaining if not use_batches else min(batch, remaining)
        ctx.client.transport.dispatch("craft", {"item": item_id, "count": craft_count})
        target_total = start_count + crafted + craft_count
        
        # Manual wait with debug logging
        wait_start = time.time()
        curr_count = 0
        success = False
        while time.time() - wait_start < timeout:
             curr_count = ctx.count_item(item_id)
             if curr_count >= target_total:
                 success = True
                 break
             time.sleep(0.5)
             
        if not success:
            ctx.log_event(f"Craft failed for {item_id} batch {craft_count}. Wanted {target_total}, got {curr_count}")
            try:
                debug_inv = get_inv_slots(ctx.get_inventory())
                ctx.log_event(f"DEBUG: Inv dump: {debug_inv}")
            except Exception as e:
                ctx.log_event(f"DEBUG: Failed to dump inv: {e}")
            return False
            
        remaining -= craft_count
        crafted += craft_count

    return True


def refresh_supply_slot_map(ctx, suite_state: Dict) -> Dict[str, List[Dict]]:
    """
    Refresh supply chest slot map.

    Args:
        ctx: Test context
        suite_state: Suite state dict

    Returns:
        Slot map dict
    """
    slot_map = {}

    def record(item_id: str, chest_key: str, slot_idx: int, count: int) -> None:
        slot_map.setdefault(item_id, []).append(
            {"chest": chest_key, "slot": slot_idx, "count": count}
        )

    chest_positions = suite_state.get("supply_chest_positions")
    if not chest_positions:
        chest_positions = suite_state.get("T1000", {}).get("chest_positions")

    if not chest_positions:
        fallback_pos = suite_state.get("T1000", {}).get("chest_pos") or (0, BASE_Y, 0)
        ctx.log_event(
            "Supply chest positions missing; using fallback. "
            f"supply_chest_positions={suite_state.get('supply_chest_positions')} "
            f"t1000_chest_positions={suite_state.get('T1000', {}).get('chest_positions')} "
            f"t1000_chest_pos={suite_state.get('T1000', {}).get('chest_pos')} "
            f"fallback={fallback_pos}"
        )
        chest_positions = [fallback_pos]

    chest_positions = list(chest_positions)

    overflow_pos = suite_state.get("supply_extra_pos")
    if overflow_pos:
        chest_positions.append(overflow_pos)

    for i, pos in enumerate(chest_positions):
        label = "main" if i == 0 else f"overflow_{i}"

        do_close_container(ctx)
        move_near(ctx, pos[0], pos[1], pos[2], timeout=10.0)

        meta = suite_state.get("chest_meta", {}).get(pos)
        expected_container_slots = 27
        if meta:
            expected_container_slots = int(meta.get("slots", 27))
        expected_total_slots = expected_container_slots + 36

        opened = do_open_container(ctx, pos, timeout=3.0)
        if not opened:
            ctx.log_event(f"Failed to open supply chest {i} at {pos}")
            continue

        screen = ctx.client.transport.dispatch("get_screen", {})
        data = screen.get("data", screen)
        slots = get_inv_slots(data)
        total_slots = int(data.get("total_slots") or len(slots))

        if not slots:
            ctx.log_event(f"Refresh failed: no slots in screen for {label}")
            do_close_container(ctx)
            continue

        if total_slots != expected_total_slots:
            ctx.log_event(
                f"Chest at {pos} opened with unexpected slot count: total_slots={total_slots} expected_total_slots={expected_total_slots}"
            )
            do_close_container(ctx)
            # Retry once: stale GUIs can cause a crafting screen (46 slots) to be returned.
            if not do_open_container(ctx, pos, timeout=3.0):
                ctx.log_event(f"Refresh failed: could not reopen chest {label} at {pos}")
                continue
            screen = ctx.client.transport.dispatch("get_screen", {})
            data = screen.get("data", screen)
            slots = get_inv_slots(data)
            total_slots = int(data.get("total_slots") or len(slots))
            if total_slots != expected_total_slots or len(slots) < expected_total_slots:
                ctx.log_event(
                    f"Refresh failed: chest {label} at {pos} still has wrong slot count: total_slots={total_slots} expected_total_slots={expected_total_slots}"
                )
                do_close_container(ctx)
                continue

        for slot_idx in range(expected_container_slots):
            item = slots[slot_idx]
            if item:
                record(item["id"], label, slot_idx, item["count"])

        do_close_container(ctx)

    suite_state["supply_slot_map"] = slot_map
    return slot_map


# Additional functions from actions/inventory_ops.py

def fill_supply_chests(ctx, x: int, y: int, z: int, items: Dict[str, int], chest_positions: List[Tuple[int, int, int]], chest_meta: Dict) -> Dict[str, List[Dict]]:
    """
    Fill supply chests with items.

    Args:
        ctx: Test context
        x, y, z: Base position
        items: Item dict
        chest_positions: List of chest positions
        chest_meta: Chest metadata

    Returns:
        Slot map
    """
    total_slots = 0
    for item_id, count in items.items():
        total_slots += (count + 63) // 64

    ctx.log_event(f"Supply needs {total_slots} slots, using up to 4 double chests (216 slots)")

    for i, (cx, cy, cz) in enumerate(chest_positions):
        block_id = block_id_at(ctx, cx, cy, cz)
        if "chest" not in block_id:
            ctx.require(False, f"Supply chest {i+1} missing at {cx},{cy},{cz} (found {block_id})")

    slot_assignments = []
    for item_id, count in items.items():
        remaining = count
        while remaining > 0:
            stack_size = min(remaining, 64)
            slot_assignments.append((item_id, stack_size))
            remaining -= stack_size

    slot_map = {}
    def record(item_id: str, chest_key: str, slot_idx: int, count: int) -> None:
        slot_map.setdefault(item_id, []).append(
            {"chest": chest_key, "slot": slot_idx, "count": count}
        )

    slot_idx = 0
    for chest_num, (cx, cy, cz) in enumerate(chest_positions):
        chest_key = "main" if chest_num == 0 else f"overflow_{chest_num}"
        meta = chest_meta.get((cx, cy, cz), {"slots": 54})
        max_slots = meta.get("slots", 54)

        for local_slot in range(max_slots):
            if slot_idx >= len(slot_assignments):
                break
            item_id, stack_size = slot_assignments[slot_idx]
            ctx.run_command(f"item replace block {cx} {cy} {cz} container.{local_slot} with {item_id} {stack_size}")
            record(item_id, chest_key, local_slot, stack_size)
            slot_idx += 1

    if len(slot_assignments) > 216:
        ctx.log_event(f"WARNING: {len(slot_assignments) - 216} stacks don't fit in 4 double chests!")

    return slot_map


def deposit_inventory_to_supply_chest(ctx, chest_positions: List[Tuple[int, int, int]], chest_meta: Dict) -> bool:
    """
    Deposit inventory to supply chests.

    Args:
        ctx: Test context
        chest_positions: Chest positions
        chest_meta: Chest metadata

    Returns:
        True if deposited
    """
    def quick_move_range(start_slot: int, end_slot: int) -> None:
        for slot in range(start_slot, end_slot + 1):
            safe_inventory_click(ctx, slot, "QUICK_MOVE")
            time.sleep(0.05)

    for i, pos in enumerate(chest_positions):
        # Move to chest before opening
        move_near(ctx, pos[0], pos[1], pos[2], timeout=10.0)

        if not do_open_container(ctx, pos, timeout=3.0):
            ctx.log_event(f"Failed to open supply chest {i} for deposit")
            continue

        meta = chest_meta.get(pos)
        screen = ctx.client.transport.dispatch("get_screen", {})
        data = screen.get("data", screen)
        slots = get_inv_slots(data)

        if not slots:
            do_close_container(ctx)
            continue

        num_container_slots = len(slots) - 36
        if meta:
            num_container_slots = meta.get("slots", num_container_slots)

        player_start = num_container_slots
        player_end = len(slots) - 1

        quick_move_range(player_start, player_end)
        do_close_container(ctx)

        if ctx.wait_for_inventory_clear(timeout=1.0):
            return True

    return ctx.wait_for_inventory_clear(timeout=2.0)


def place_double_chest(ctx, x: int, y: int, z: int, base_y: int) -> bool:
    """
    Place double chest.

    Args:
        ctx: Test context
        x, y, z: Position
        base_y: Base Y

    Returns:
        True if placed
    """
    move_near(ctx, x, base_y, z - 2, timeout=10.0)
    ctx.client.transport.dispatch("look_at", {"x": x + 0.5, "y": y + 0.5, "z": z + 5.5})
    ok = place_block_at(ctx, x, y, z, "minecraft:chest", allow_move=False)
    ctx.client.transport.dispatch("look_at", {"x": x + 1.5, "y": y + 0.5, "z": z + 5.5})
    ok = place_block_at(ctx, x + 1, y, z, "minecraft:chest", allow_move=False) and ok
    return ok


def build_slot_map_from_screen(slots: list, chest_key: str, max_slots: int) -> dict:
    """
    Build slot map from screen slots data.

    Args:
        slots: List of slot dicts
        chest_key: Key for the chest
        max_slots: Maximum number of slots

    Returns:
        Slot map dict
    """
    slot_map = {}

    def record(item_id: str, slot_idx: int, count: int) -> None:
        slot_map.setdefault(item_id, []).append(
            {"chest": chest_key, "slot": slot_idx, "count": count}
        )

    for slot_idx in range(max_slots):
        item = slots[slot_idx]
        if item and item.get("id") and item.get("count", 0) > 0:
            record(item["id"], slot_idx, item["count"])

    return slot_map


def get_workshop_furnace(suite_state: Dict) -> Tuple[int, int, int]:
    """
    Get the workshop furnace position, with fallback to default beyond supply chests.

    Args:
        suite_state: Suite state dict

    Returns:
        (x, y, z) coordinates
    """
    if suite_state.get("furnace_pos"):
        return suite_state["furnace_pos"]
    # Fallback: beyond the 4 double chests (x to x+10)
    pos = suite_state.get("T1000", {}).get("chest_pos") or (0, BASE_Y, 0)
    return (pos[0] + 13, pos[1], pos[2])


def craft_wooden_axe_manual(ctx) -> bool:
    """
    Manually craft wooden axe in 3x3 grid.
    
    Args:
        ctx: Test context
    
    Returns:
        True if crafting successful
    """
    return _craft_tool_manual_generic(ctx, "_planks", "axe", "minecraft:wooden_axe")


def craft_chest_manual(ctx) -> bool:
    """
    Manual chest crafting sequence.
    
    Args:
        ctx: Test context
        
    Returns:
        True if crafting successful
    """
    ctx.log_event("Starting manual chest click sequence...")
    # 1. Take result slot if any (clear it)
    safe_inventory_click(ctx, 0, "QUICK_MOVE", 0)
    time.sleep(0.2)
    
    # 2. Find planks from screen slots (NOT base inventory - slot numbers are different!)
    # When crafting table is open: 0=output, 1-9=grid, 10-36=main inventory, 37-45=hotbar
    screen = ctx.client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    screen_slots = data.get("slots", [])
    for grid_slot in range(1, 10):
        slot_info = next((s for s in screen_slots if s.get("slot") == grid_slot), None)
        if slot_info and slot_info.get("id") not in ("minecraft:air", None) and slot_info.get("count", 0) > 0:
            safe_inventory_click(ctx, grid_slot, "QUICK_MOVE", 0)
            time.sleep(0.05)
    screen = ctx.client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    screen_slots = data.get("slots", [])
    plank_slots = []
    total_planks = 0
    for s in screen_slots:
        slot_id = s.get("slot", -1)
        item_id = s.get("id", "")
        count = s.get("count", 0)
        # Player inventory starts at slot 10 in crafting table screen
        if slot_id >= 10 and item_id.endswith("_planks") and count > 0:
            plank_slots.append((slot_id, count))
            total_planks += count
            if total_planks >= 8:
                break

    if total_planks < 8:
        ctx.log_event(f"ERROR: Not enough planks (have {total_planks}, need 8)")
        return False
    
    ctx.log_event(f"Found plank slots for chest: {plank_slots}, total: {total_planks}")

    # 3. Place in 'O' shape: 1,2,3, 4,6, 7,8,9 (middle 5 empty)
    chest_pattern = [1, 2, 3, 4, 6, 7, 8, 9]
    slot_idx = 0
    remaining = 0
    current_slot = None
    for grid_slot in chest_pattern:
        if remaining == 0:
            if slot_idx >= len(plank_slots):
                ctx.log_event("ERROR: Ran out of planks during chest craft")
                return False
            current_slot, remaining = plank_slots[slot_idx]
            slot_idx += 1
            safe_inventory_click(ctx, current_slot, "PICKUP", 0)
            time.sleep(0.1)

        safe_inventory_click(ctx, grid_slot, "PICKUP", 1) # Right click to place 1
        time.sleep(0.1)
        remaining -= 1

        if remaining == 0 and current_slot is not None:
            safe_inventory_click(ctx, current_slot, "PICKUP", 0)
            time.sleep(0.1)
            current_slot = None

    if remaining > 0 and current_slot is not None:
        safe_inventory_click(ctx, current_slot, "PICKUP", 0)
        time.sleep(0.1)
    
    # Debug: Check screen state before taking result
    screen = ctx.client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    result_slot = None
    for s in data.get("slots", []):
        if s.get("slot") == 0:
            result_slot = s
            break
    ctx.log_event(f"Result slot (0) before taking: {result_slot}")
    
    # 6. Take result
    safe_inventory_click(ctx, 0, "QUICK_MOVE", 0)
    time.sleep(0.5)
    
    if ctx.has_item("minecraft:chest"):
        ctx.log_event("Manual chest crafting successful!")
        return True
    
    ctx.log_event("ERROR: Chest not in inventory after crafting sequence")
    return False


def craft_furnace_manual(ctx) -> bool:
    """Craft a furnace in the verified 3x3 table grid."""
    ctx.log_event("Starting manual furnace click sequence...")
    # Use the same atomic bridge primitive and verified output-space handling
    # as the other progression recipes.  The legacy path below remains the
    # fallback inside craft_recipe_manual for older bridge jars.
    return craft_recipe_manual(
        ctx,
        "minecraft:furnace",
        [("minecraft:cobblestone", slot) for slot in (1, 2, 3, 4, 6, 7, 8, 9)],
    )


def _craft_furnace_manual_legacy(ctx) -> bool:
    """Legacy furnace click choreography retained for diagnostic comparison."""
    safe_inventory_click(ctx, 0, "QUICK_MOVE", 0)
    time.sleep(0.2)

    screen = ctx.client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    screen_slots = data.get("slots", [])
    for grid_slot in range(1, 10):
        slot_info = next((s for s in screen_slots if s.get("slot") == grid_slot), None)
        if slot_info and slot_info.get("id") not in ("minecraft:air", None) and slot_info.get("count", 0) > 0:
            safe_inventory_click(ctx, grid_slot, "QUICK_MOVE", 0)
            time.sleep(0.05)

    screen = ctx.client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    screen_slots = data.get("slots", [])
    cobble_slots = [
        (slot.get("slot"), slot.get("count", 0))
        for slot in screen_slots
        if slot.get("slot", -1) >= 10
        and slot.get("id") == "minecraft:cobblestone"
        and slot.get("count", 0) > 0
    ]
    if sum(count for _slot, count in cobble_slots) < 8:
        ctx.log_event(f"Furnace craft failed: need 8 cobblestone, found {cobble_slots}")
        return False

    remaining_targets = [1, 2, 3, 4, 6, 7, 8, 9]
    for source_slot, source_count in cobble_slots:
        if not remaining_targets:
            break
        safe_inventory_click(ctx, source_slot, "PICKUP", 0)
        time.sleep(0.05)
        for _ in range(min(source_count, len(remaining_targets))):
            safe_inventory_click(ctx, remaining_targets.pop(0), "PICKUP", 1)
            time.sleep(0.05)
        safe_inventory_click(ctx, source_slot, "PICKUP", 0)
        time.sleep(0.05)

    result_screen = ctx.client.transport.dispatch("get_screen", {})
    result_data = result_screen.get("data", result_screen)
    result_slot = next(
        (slot for slot in result_data.get("slots", []) if slot.get("slot") == 0),
        None,
    )
    ctx.log_event(f"Furnace result slot before taking: {result_slot}")
    if not result_slot or result_slot.get("id") != "minecraft:furnace":
        return False

    safe_inventory_click(ctx, 0, "QUICK_MOVE", 0)
    time.sleep(0.3)
    return ctx.has_item("minecraft:furnace")

# --- Generic Manual Crafting Helpers ---

def _craft_tool_manual_generic(ctx, material_id_or_tag: str, tool_type: str, result_id: str) -> bool:
    """
    Generic manual tool crafting helper.

    Args:
        ctx: Test context
        material_id_or_tag: Item ID substring (e.g. "_planks", "minecraft:cobblestone")
        tool_type: "axe", "pickaxe", "shovel", "sword", "hoe"
        result_id: Expected result item ID (e.g. "minecraft:stone_axe")

    Returns:
        True if successful.
    """
    TOOL_Recipes = {
        "axe": {"material": [1, 2, 4], "stick": [5, 8], "mat_count": 3},
        "pickaxe": {"material": [1, 2, 3], "stick": [5, 8], "mat_count": 3},
        "shovel": {"material": [2], "stick": [5, 8], "mat_count": 1},
        "sword": {"material": [2, 5], "stick": [8], "mat_count": 2},
        "hoe": {"material": [1, 2], "stick": [5, 8], "mat_count": 2},
    }

    recipe = TOOL_Recipes.get(tool_type)
    if not recipe:
        ctx.log_event(f"Unknown tool type: {tool_type}")
        return False

    # Prefer the bridge-native atomic recipe primitive.  The older per-click
    # choreography can be interrupted between grid placements and repeatedly
    # left Bot02 with no output despite valid ingredients.  craft_recipe_manual
    # retains the verified click fallback for older bridge jars.
    placements = [
        (material_id_or_tag, grid_slot)
        for grid_slot in recipe["material"]
    ] + [
        ("minecraft:stick", grid_slot)
        for grid_slot in recipe["stick"]
    ]
    if craft_recipe_manual(
        ctx,
        result_id,
        placements,
        crafts=1,
        output_per_recipe=1,
    ):
        return True

    screen = ctx.client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    slots = data.get("slots", [])
    
    mat_slot = -1
    stick_slot = -1
    
    # 1. Check ingredients
    for s in slots:
        sid = s.get("id", "")
        count = s.get("count", 0)
        
        # Check material
        if material_id_or_tag in sid and count >= recipe["mat_count"]:
            # Special case for planks vs logs vs sticks
            if "stick" not in sid: 
                mat_slot = s.get("slot")
        
        # Check sticks
        if "stick" in sid and count >= len(recipe["stick"]):
            stick_slot = s.get("slot")

    if mat_slot == -1 or stick_slot == -1:
        ctx.log_event(f"Manual {tool_type}: Missing ingredients (mat_slot={mat_slot}, stick_slot={stick_slot})")
        return False

    ctx.log_event(f"Placing {tool_type} recipe: material from {mat_slot}, sticks from {stick_slot}")

    # 2. Clear output slot
    safe_inventory_click(ctx, 0, "QUICK_MOVE", 0)
    time.sleep(0.1)

    # 3. Place Materials
    safe_inventory_click(ctx, mat_slot, "PICKUP", 0)
    time.sleep(0.1)
    for grid_idx in recipe["material"]:
        safe_inventory_click(ctx, grid_idx, "PICKUP", 1) # Right click place 1
        time.sleep(0.1)
    safe_inventory_click(ctx, mat_slot, "PICKUP", 0) # Return rest
    time.sleep(0.1)

    # 4. Place Sticks
    safe_inventory_click(ctx, stick_slot, "PICKUP", 0)
    time.sleep(0.1)
    for grid_idx in recipe["stick"]:
        safe_inventory_click(ctx, grid_idx, "PICKUP", 1)
        time.sleep(0.1)
    safe_inventory_click(ctx, stick_slot, "PICKUP", 0) # Return rest
    time.sleep(0.1)

    # 5. Take Result
    safe_inventory_click(ctx, 0, "QUICK_MOVE", 0)
    time.sleep(0.3)
    
    do_close_container(ctx)
    time.sleep(0.2)

    return ctx.has_item(result_id)


def craft_recipe_manual(
    ctx,
    result_id: str,
    placements: List[Tuple[str, int]],
    crafts: int = 1,
    output_per_recipe: int = 1,
    try_bridge: bool = True,
) -> bool:
    """Craft a bounded shaped/shapeless recipe through verified grid clicks.

    ``placements`` contains ``(ingredient_selector, grid_slot)`` pairs.  An
    exact item id selects that item; ``#planks`` accepts any plank variant.
    The crafting-table GUI must already be open.  This is intentionally small
    and deterministic for progression recipes unavailable through recipe
    listing on the live 1.21.x client.

    By default, tries the bridge-native ``place_recipe`` command first (single
    TCP round-trip).  Set ``try_bridge=False`` when the caller already made
    that attempt; this prevents a failed atomic craft from being submitted a
    second time before the per-click recovery path runs.
    """
    if crafts <= 0:
        return True

    # Both the bridge-native and per-click paths QUICK_MOVE the result.  A
    # completely full inventory leaves valid output stranded in slot 0 and
    # used to make the controller repeat the recipe indefinitely.
    if not ensure_crafting_output_space(ctx):
        return False

    # --- Bridge-native fast path (single round-trip) ---
    if try_bridge:
        try:
            payload = {
                "placements": [
                    {"selector": sel, "grid_slot": slot}
                    for sel, slot in placements
                ],
                "expected_output": result_id,
                "expected_count": output_per_recipe,
                "crafts": crafts,
            }
            resp = ctx.client.transport.dispatch("place_recipe", payload)
            data = resp.get("data", resp) if isinstance(resp, dict) else {}
            if data.get("crafted"):
                return True
            # Bridge returned a structured error — fall through to Python path.
        except Exception:
            # Command not recognised by older bridge, transport error, etc.
            pass


    def matches(selector: str, item_id: str) -> bool:
        # "_planks" is the selector _craft_tool_manual_generic passes for
        # wooden tools (from _TOOL_MATERIALS["wooden"]); "#planks" is the
        # convention used by hand-authored _MANUAL_GRID_RECIPES placements
        # elsewhere. Both mean "any plank family" and must match here, or an
        # exact-equality fallback against a literal item id like
        # "minecraft:spruce_planks" never matches and every wooden tool
        # craft fails at this step regardless of carried plank count --
        # confirmed live: Bot09 stuck looping "missing _planks" with 8
        # spruce planks in hand.
        if selector in ("#planks", "_planks"):
            return item_id.endswith("_planks")
        # Same class of bug as the plank selector above: a "#coals" placement
        # never matched a literal item id, so a bot holding charcoal could not
        # manually craft torches. Coal and charcoal are interchangeable for
        # every recipe that burns them. Confirmed live: Bot16 held 6 charcoal
        # and 4 sticks and failed torch_supply 50 times.
        if selector in ("#coals", "any_coal"):
            return item_id in ("minecraft:coal", "minecraft:charcoal")
        return item_id == selector

    def read_slots():
        screen = ctx.client.transport.dispatch("get_screen", {})
        data = screen.get("data", screen)
        slots = data.get("slots", [])
        screen_type = data.get("type", "")
        if len(slots) < 46 or (
            "Crafting" not in screen_type
            and screen_type not in {"class_1714", "CraftingScreenHandler"}
        ):
            ctx.log_event(
                f"Manual recipe failed: expected crafting table, got "
                f"{screen_type} ({len(slots)} slots)"
            )
            return None
        return slots

    slots = read_slots()
    if slots is None:
        return False
    for grid_slot in range(1, 10):
        slot_info = next(
            (slot for slot in slots if slot.get("slot") == grid_slot), None
        )
        if slot_info and int(slot_info.get("count", 0)) > 0:
            safe_inventory_click(ctx, grid_slot, "QUICK_MOVE", 0)
            time.sleep(0.05)

    before = ctx.count_item(result_id)
    for craft_index in range(crafts):
        for selector, grid_slot in placements:
            slots = read_slots()
            if slots is None:
                do_close_container(ctx)
                return False
            source = next(
                (
                    slot
                    for slot in slots
                    if int(slot.get("slot", -1)) >= 10
                    and int(slot.get("count", 0)) > 0
                    and matches(selector, str(slot.get("id", "")))
                ),
                None,
            )
            if source is None:
                ctx.log_event(
                    f"Manual {result_id} missing {selector} at craft "
                    f"{craft_index + 1}/{crafts}"
                )
                do_close_container(ctx)
                return False
            source_slot = int(source["slot"])
            safe_inventory_click(ctx, source_slot, "PICKUP", 0)
            time.sleep(0.04)
            safe_inventory_click(ctx, grid_slot, "PICKUP", 1)
            time.sleep(0.04)
            safe_inventory_click(ctx, source_slot, "PICKUP", 0)
            time.sleep(0.04)

        output_ready = False
        for _ in range(10):
            slots = read_slots()
            if slots is None:
                break
            output = next(
                (slot for slot in slots if int(slot.get("slot", -1)) == 0),
                None,
            )
            if (
                output
                and output.get("id") == result_id
                and int(output.get("count", 0)) >= output_per_recipe
            ):
                output_ready = True
                break
            time.sleep(0.05)
        if not output_ready:
            ctx.log_event(
                f"Manual {result_id} produced no verified output at craft "
                f"{craft_index + 1}/{crafts}"
            )
            do_close_container(ctx)
            return False
        safe_inventory_click(ctx, 0, "QUICK_MOVE", 0)
        time.sleep(0.08)

    target = before + crafts * output_per_recipe
    complete = ctx.count_item(result_id) >= target
    do_close_container(ctx)
    return complete


def _craft_armor_manual_generic(
    ctx,
    material_id: str,
    armor_type: str,
    result_id: str,
) -> bool:
    """Craft one armor piece in an already-open 3x3 crafting table."""
    armor_recipes = {
        "helmet": [1, 2, 3, 4, 6],
        "chestplate": [1, 3, 4, 5, 6, 7, 8, 9],
        "leggings": [1, 2, 3, 4, 6, 7, 9],
        "boots": [4, 6, 7, 9],
    }
    targets = armor_recipes.get(armor_type)
    if targets is None:
        ctx.log_event(f"Unknown armor type: {armor_type}")
        return False

    # Clear stale recipe ingredients before reading source slots.
    safe_inventory_click(ctx, 0, "QUICK_MOVE", 0)
    for grid_slot in range(1, 10):
        screen = ctx.client.transport.dispatch("get_screen", {})
        data = screen.get("data", screen)
        slot = next(
            (value for value in data.get("slots", []) if value.get("slot") == grid_slot),
            None,
        )
        if slot and slot.get("count", 0) > 0:
            safe_inventory_click(ctx, grid_slot, "QUICK_MOVE", 0)
            time.sleep(0.05)

    screen = ctx.client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    material_slots = [
        (slot.get("slot"), int(slot.get("count", 0)))
        for slot in data.get("slots", [])
        if slot.get("slot", -1) >= 10
        and slot.get("id") == material_id
        and int(slot.get("count", 0)) > 0
    ]
    if sum(count for _slot, count in material_slots) < len(targets):
        ctx.log_event(
            f"Manual {armor_type}: need {len(targets)} {material_id}, "
            f"found {material_slots}"
        )
        return False

    remaining_targets = list(targets)
    for source_slot, source_count in material_slots:
        if not remaining_targets:
            break
        safe_inventory_click(ctx, source_slot, "PICKUP", 0)
        time.sleep(0.05)
        for _ in range(min(source_count, len(remaining_targets))):
            safe_inventory_click(ctx, remaining_targets.pop(0), "PICKUP", 1)
            time.sleep(0.05)
        safe_inventory_click(ctx, source_slot, "PICKUP", 0)
        time.sleep(0.05)

    result_screen = ctx.client.transport.dispatch("get_screen", {})
    result_data = result_screen.get("data", result_screen)
    result_slot = next(
        (slot for slot in result_data.get("slots", []) if slot.get("slot") == 0),
        None,
    )
    ctx.log_event(f"Armor result slot before taking: {result_slot}")
    if not result_slot or result_slot.get("id") != result_id:
        return False

    safe_inventory_click(ctx, 0, "QUICK_MOVE", 0)
    time.sleep(0.3)
    do_close_container(ctx)
    time.sleep(0.2)
    return ctx.has_item(result_id)

def craft_stone_axe_manual(ctx) -> bool:
    return _craft_tool_manual_generic(ctx, "minecraft:cobblestone", "axe", "minecraft:stone_axe")

def craft_iron_axe_manual(ctx) -> bool:
    return _craft_tool_manual_generic(ctx, "minecraft:iron_ingot", "axe", "minecraft:iron_axe")

def craft_golden_axe_manual(ctx) -> bool:
    return _craft_tool_manual_generic(ctx, "minecraft:gold_ingot", "axe", "minecraft:golden_axe")

def craft_diamond_axe_manual(ctx) -> bool:
    return _craft_tool_manual_generic(ctx, "minecraft:diamond", "axe", "minecraft:diamond_axe")

def craft_stone_pickaxe_manual(ctx) -> bool:
    return _craft_tool_manual_generic(ctx, "minecraft:cobblestone", "pickaxe", "minecraft:stone_pickaxe")

def craft_iron_pickaxe_manual(ctx) -> bool:
    return _craft_tool_manual_generic(ctx, "minecraft:iron_ingot", "pickaxe", "minecraft:iron_pickaxe")

def craft_diamond_pickaxe_manual(ctx) -> bool:
    return _craft_tool_manual_generic(ctx, "minecraft:diamond", "pickaxe", "minecraft:diamond_pickaxe")

def craft_stone_shovel_manual(ctx) -> bool:
    return _craft_tool_manual_generic(ctx, "minecraft:cobblestone", "shovel", "minecraft:stone_shovel")

def craft_iron_shovel_manual(ctx) -> bool:
    return _craft_tool_manual_generic(ctx, "minecraft:iron_ingot", "shovel", "minecraft:iron_shovel")

def craft_stone_sword_manual(ctx) -> bool:
    return _craft_tool_manual_generic(ctx, "minecraft:cobblestone", "sword", "minecraft:stone_sword")

def craft_iron_sword_manual(ctx) -> bool:
    return _craft_tool_manual_generic(ctx, "minecraft:iron_ingot", "sword", "minecraft:iron_sword")

def craft_stone_hoe_manual(ctx) -> bool:
    return _craft_tool_manual_generic(ctx, "minecraft:cobblestone", "hoe", "minecraft:stone_hoe")

def craft_iron_hoe_manual(ctx) -> bool:
    return _craft_tool_manual_generic(ctx, "minecraft:iron_ingot", "hoe", "minecraft:iron_hoe")

# --- Advanced Helpers (Refactored) ---

def equip_item_to_hotbar(ctx, slot: int, hotbar_idx: int = 0):
    """
    Move an item from inventory slot to hotbar slot (default 0).
    """
    try:
        # Swap with hotbar slot (slots 36-44 are hotbar in player inventory screen)
        # Note: This assumes standard inventory screen numbering where hotbar is 36-44 or similar.
        # But safe_inventory_click usually handles the raw slot ID. 
        # For 'swap' to hotbar via number key, we can use 'SWAP' mode if needed, 
        # but here we follow the click-click pattern from the original suite.
        
        target_hotbar_slot = 36 + hotbar_idx # 36 is usually hotbar slot 0 in main inventory container
        
        safe_inventory_click(ctx, slot, "PICKUP", 0)
        time.sleep(0.1)
        safe_inventory_click(ctx, target_hotbar_slot, "PICKUP", 0)
        time.sleep(0.1)
        # If there was something in hotbar, put it back in the original slot
        safe_inventory_click(ctx, slot, "PICKUP", 0)
        time.sleep(0.1)
    except Exception as e:
        ctx.log_event(f"Failed to equip item: {e}")

def get_best_tool_durability(ctx, tool_type_id: str = "minecraft:wooden_axe") -> Optional[int]:
    """
    Check tool durability. Returns remaining uses of BEST tool of that type (None if no tool).
    """
    inv = ctx.get_inventory().get("inventory", [])
    best_durability = None
    best_slot = None
    
    # Generic max damage map? For now hardcoded for wooden axe as per original, 
    # but we can make it smarter later.
    max_damage = 59 if "wooden" in tool_type_id else 131 if "stone" in tool_type_id else 250
    if "iron" in tool_type_id: max_damage = 250
    if "diamond" in tool_type_id: max_damage = 1561
    
    for item in inv:
        if item.get("id") == tool_type_id:
            damage = item.get("damage", 0)
            remaining = max_damage - damage
            slot = item.get("slot", -1)
            
            if best_durability is None or remaining > best_durability:
                best_durability = remaining
                best_slot = slot
    
    # If we found a good tool not in hotbar, equip it
    if best_slot is not None and best_slot >= 9 and best_durability is not None and best_durability > 10:
        ctx.log_event(f"Best {tool_type_id} (durability {best_durability}) at slot {best_slot}, moving to hotbar...")
        equip_item_to_hotbar(ctx, best_slot)
    
    return best_durability

def robust_craft(ctx, item_id: str, count: int = 1, is_tool: bool = False, timeout: float = 30.0) -> bool:
    """
    Craft an item with retries and multiple methods (auto_craft, generic craft, manual fallback).
    """
    
    def _check_count():
        return ctx.count_item(item_id)
        
    start_time = time.time()
    
    # Attempt loop
    for attempt in range(6):
        if time.time() - start_time > timeout:
            break
            
        current_count = _check_count()
        if not is_tool and current_count >= count:
            return True
        if is_tool and ctx.has_item(item_id):
             # For tools, we might want to check durability, but basic existence is often enough for 'crafted' check
             return True

        # Method 1: auto_craft (baritone)
        try:
            ctx.log_event(f"Crafting {count}x {item_id} (Attempt {attempt+1}, using auto_craft)...")
            ctx.client.transport.dispatch("auto_craft", {"item": item_id, "quantity": count})
            time.sleep(3.0)
        except Exception as e:
            ctx.log_event(f"auto_craft attempt failed: {e}")

        if not is_tool and _check_count() >= count: return True
        if is_tool and ctx.has_item(item_id): return True

        # Method 2: craft (vanilla/mod packet)
        try:
            ctx.log_event(f"Crafting {count}x {item_id} (Attempt {attempt+1}, using craft)...")
            ctx.client.transport.dispatch("craft", {"item": item_id, "count": count})
            time.sleep(2.0)
        except Exception as e:
            ctx.log_event(f"craft attempt failed: {e}")
        
        if not is_tool and _check_count() >= count: return True
        if is_tool and ctx.has_item(item_id): return True

        # Method 3: Manual Fallbacks
        fallback_success = False
        if is_tool and item_id == "minecraft:wooden_axe":
            fallback_success = craft_wooden_axe_manual(ctx)
        elif item_id == "minecraft:chest":
             # Try generic manual craft for chest if specific one fails? 
             # We have craft_chest_manual
             fallback_success = craft_chest_manual(ctx)
        elif item_id == "minecraft:stick":
             # We can add a simple manual stick craft if needed, or rely on auto
             pass 
        elif item_id == "minecraft:stick":
             # We can add a simple manual stick craft if needed, or rely on auto
             pass 
        elif item_id == "minecraft:crafting_table":
             pass
        elif "door" in item_id:
             fallback_success = craft_door_manual(ctx, item_id)

        if fallback_success:
            return True
        
        time.sleep(1.0)
        
    return False

def craft_sticks_manual(ctx) -> bool:
    """Craft sticks using robust crafting wrapper."""
    return robust_craft(ctx, "minecraft:stick", 1)

def craft_planks_manual(ctx, plank_id: str, output_count: int = 4) -> bool:
    """Craft one wood family's planks in the verified player 2x2 grid.

    Tries the bridge-native ``place_recipe`` command first. Falls back
    to manual grid clicks if unavailable or fails.
    """
    log_id = plank_id.replace("_planks", "_log")
    if not log_id.startswith("minecraft:"):
        log_id = "minecraft:" + log_id
    before = ctx.count_item(plank_id)
    target = before + max(1, int(output_count))
    crafts_needed = (max(1, int(output_count)) + 3) // 4

    # ``place_recipe`` can report ``crafted=true`` after consuming a log even
    # when a full inventory has nowhere to put the planks. Reserve a verified
    # destination before either the atomic bridge route or the click fallback
    # so a successful response also means the output can be retained.
    if not ensure_crafting_output_space(ctx):
        return False

    # --- Bridge-native fast path ---
    try:
        payload = {
            "placements": [
                {"selector": log_id, "grid_slot": 1}
            ],
            "expected_output": plank_id,
            "expected_count": 4,
            "crafts": crafts_needed,
        }
        resp = ctx.client.transport.dispatch("place_recipe", payload)
        data = resp.get("data", resp) if isinstance(resp, dict) else {}
        if data.get("crafted"):
            return ctx.count_item(plank_id) >= target
    except Exception:
        pass

    # --- Python fallback per-click sequence ---
    for _ in range(crafts_needed):
        do_close_container(ctx)
        time.sleep(0.05)
        screen = ctx.client.transport.dispatch("get_screen", {})
        data = screen.get("data", screen)
        slots = data.get("slots", [])
        screen_type = data.get("type", "")
        if len(slots) < 46 or (
            "Player" not in screen_type
            and screen_type not in {"class_1723", "PlayerScreenHandler"}
        ):
            ctx.log_event(
                f"Manual plank craft expected player 2x2 screen, got {screen_type} ({len(slots)} slots)"
            )
            return False

        # Clear residue from failed native recipes before using one grid cell.
        for grid_slot in (1, 2, 3, 4):
            slot_info = next((slot for slot in slots if slot.get("slot") == grid_slot), None)
            if slot_info and slot_info.get("count", 0) > 0:
                safe_inventory_click(ctx, grid_slot, "QUICK_MOVE", 0)
                time.sleep(0.05)

        # Find log in inventory (slots >= 9)
        screen = ctx.client.transport.dispatch("get_screen", {})
        data = screen.get("data", screen)
        slots = data.get("slots", [])
        log_slot = None
        for slot in slots:
            if slot.get("slot", -1) >= 9 and slot.get("id") == log_id and slot.get("count", 0) > 0:
                log_slot = slot.get("slot")
                break
        if log_slot is None:
            ctx.log_event(f"Manual plank craft: no logs found for {log_id}")
            return False

        # Click log slot to pick up, click grid slot 1 (right click to place 1), put log back to source
        safe_inventory_click(ctx, log_slot, "PICKUP", 0)
        time.sleep(0.05)
        safe_inventory_click(ctx, 1, "PICKUP", 1)  # right-click to place 1
        time.sleep(0.05)
        safe_inventory_click(ctx, log_slot, "PICKUP", 0)
        time.sleep(0.05)

        # Wait for output in slot 0
        output_ready = False
        for _ in range(10):
            screen = ctx.client.transport.dispatch("get_screen", {})
            data = screen.get("data", screen)
            output = next((s for s in data.get("slots", []) if s.get("slot") == 0), None)
            if output and output.get("id") == plank_id and output.get("count", 0) >= 4:
                output_ready = True
                break
            time.sleep(0.05)
        if not output_ready:
            ctx.log_event("Manual plank craft: output not ready")
            return False

        # Shift click output
        safe_inventory_click(ctx, 0, "QUICK_MOVE", 0)
        time.sleep(0.05)

    return ctx.count_item(plank_id) >= target


def craft_crafting_table_manual(ctx) -> bool:
    """Craft a table directly in the player's 2x2 grid.

    The 1.21.8 bridge recipe handler can leave the plank stack on the cursor
    while filling this recipe, which makes the fourth ingredient lookup fail.
    Drive the four player-grid slots explicitly instead.
    """
    ctx.log_event("Starting manual 2x2 crafting-table sequence...")
    do_close_container(ctx)
    screen = {}
    data = {}
    slots = []
    screen_type = ""
    for _ in range(10):
        screen = ctx.client.transport.dispatch("get_screen", {})
        data = screen.get("data", screen)
        slots = data.get("slots", [])
        screen_type = data.get("type", "")
        if len(slots) >= 46 and (
            "Player" in screen_type
            or screen_type in {"class_1723", "PlayerScreenHandler"}
        ):
            break
        # A just-finished container interaction can enqueue its open callback
        # after the first close. Reissue the close only after observing that
        # stale menu, then leave enough time for the client thread to apply it.
        ctx.client.transport.dispatch("close_screen", {})
        time.sleep(0.35)
    else:
        ctx.log_event(
            f"Manual crafting table failed: expected player 2x2 screen, got {screen_type} ({len(slots)} slots)"
        )
        return False

    for grid_slot in (1, 2, 3, 4):
        slot_info = next((slot for slot in slots if slot.get("slot") == grid_slot), None)
        if slot_info and slot_info.get("count", 0) > 0:
            safe_inventory_click(ctx, grid_slot, "QUICK_MOVE", 0)
            time.sleep(0.05)

    screen = ctx.client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    slots = data.get("slots", [])
    if not ensure_player_crafting_output_space(ctx, screen):
        return False

    plank_slots = [
        (slot.get("slot"), slot.get("count", 0))
        for slot in slots
        if slot.get("slot", -1) >= 9
        and slot.get("id", "").endswith("_planks")
        and slot.get("count", 0) > 0
    ]
    if sum(count for _slot, count in plank_slots) < 4:
        ctx.log_event(f"Manual crafting table failed: need 4 planks, found {plank_slots}")
        return False

    remaining_grid = [1, 2, 3, 4]
    for source_slot, source_count in plank_slots:
        if not remaining_grid:
            break
        safe_inventory_click(ctx, source_slot, "PICKUP", 0)
        time.sleep(0.05)
        place_count = min(source_count, len(remaining_grid))
        for _ in range(place_count):
            target_slot = remaining_grid.pop(0)
            safe_inventory_click(ctx, target_slot, "PICKUP", 1)
            time.sleep(0.05)
        # Return any unused cursor stack to its source slot.
        safe_inventory_click(ctx, source_slot, "PICKUP", 0)
        time.sleep(0.05)

    result_screen = ctx.client.transport.dispatch("get_screen", {})
    result_data = result_screen.get("data", result_screen)
    result_slot = next(
        (slot for slot in result_data.get("slots", []) if slot.get("slot") == 0),
        None,
    )
    ctx.log_event(f"Manual crafting table result slot: {result_slot}")
    if not result_slot or result_slot.get("id") != "minecraft:crafting_table":
        return False

    safe_inventory_click(ctx, 0, "QUICK_MOVE", 0)
    success = ctx.wait_for_item("minecraft:crafting_table", 1, timeout=3.0)
    ctx.log_event(
        "Manual crafting table successful!" if success else "Manual crafting table missing after result click"
    )
    return success


# --- Status and Counting Helpers ---

def safe_count_item(ctx, item_id: str) -> int:
    """Safe wrapper for counting items."""
    try:
        return ctx.count_item(item_id)
    except Exception:
        return 0

def get_inventory_counts(ctx) -> Dict[str, int]:
    """Get dictionary of all items in inventory and their counts."""
    inv = ctx.get_inventory().get("inventory", [])
    items = {}
    for slot in inv:
        if slot and slot.get("id") and slot.get("id") != "minecraft:air":
            item_id = slot.get("id")
            count = slot.get("count", 0)
            if item_id in items:
                items[item_id] += count
            else:
                items[item_id] = count
    return items

def count_all_logs(ctx) -> int:
    """Count all wood-like blocks considered as 'logs'."""
    all_ids = (
        LOG_BLOCK_IDS
        + STRIPPED_LOG_BLOCK_IDS
        + WOOD_BLOCK_IDS
        + STRIPPED_WOOD_BLOCK_IDS
    )
    return sum(max(0, safe_count_item(ctx, item_id)) for item_id in all_ids)

def count_any_planks(ctx) -> int:
    """Count all plank types."""
    return sum(max(0, safe_count_item(ctx, item_id)) for item_id in PLANK_ITEM_IDS)

def log_full_status(ctx, prefix: str = "STATUS:"):
    """Print comprehensive status: health, hunger, position, inventory."""
    try:
        # Get position
        pos = ctx.get_position()
        pos_str = f"Pos({pos[0]:.1f}, {pos[1]:.1f}, {pos[2]:.1f})" if pos else "unknown"
        
        # Get health/hunger from player state
        state = ctx.client.transport.dispatch("get_state", {})
        health = state.get("health", "?")
        hunger = state.get("food", state.get("hunger", "?"))
        world_time = state.get("world_time")
        time_str = ""
        if isinstance(world_time, (int, float)):
            day_time = int(world_time) % 24000
            if day_time < 12000:
                eta_ticks = 12000 - day_time
                eta_label = "etaDark"
            else:
                eta_ticks = 24000 - day_time
                eta_label = "etaDay"
            eta_seconds = eta_ticks / 20.0
            time_str = f" | Time={day_time} {eta_label}={eta_seconds:.0f}s"
        
        # Get full inventory
        inv_contents = get_inventory_counts(ctx)
        inv_str = ", ".join([f"{k.replace('minecraft:', '')}: {v}" for k, v in inv_contents.items()]) if inv_contents else "empty"
        
        status_line = f"{prefix} HP={health} Hunger={hunger} | {pos_str}{time_str} | Inv: {inv_str}"
        ctx.log_event(status_line)
        print(status_line)
    except Exception as e:
        print(f"{prefix} Error getting status: {e}")
