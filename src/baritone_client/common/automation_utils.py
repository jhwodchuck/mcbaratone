"""
automation_utils.py - Common helper functions for high-level automation tasks.
"""

import time
from typing import Dict, List, Optional, Any


def refresh_and_check(resources, item_id: str, count: int = 1) -> bool:
    """Refresh inventory and check if we have enough of an item."""
    resources.refresh_inventory()
    current = resources.get_item_count(item_id)
    return current >= count


def wait_for_item(resources, item_id: str, target_count: int, timeout: float = 60.0) -> bool:
    """Wait for an item to reach a target count in inventory."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        if refresh_and_check(resources, item_id, target_count):
            return True
        time.sleep(1.0)
    return False


def click_inventory_slot(client, slot: int, mode: str = "PICKUP", button: int = 0):
    """Perform an inventory click via the bridge."""
    return client.transport.dispatch("inventory_click", {
        "slot": slot,
        "type": mode,
        "button": button
    })


def craft_item_simple(client, resources, item_id: str, recipe_map: Dict[int, str], count: int = 1):
    """
    Very simple crafting helper that places items in slots.
    recipe_map: dict of slot_index -> ingredient_id
    """
    # 1. Clear crafting grid (simplified for now, assumes empty or just moves stuff)
    # 2. Place ingredients
    for slot, ingredient_id in recipe_map.items():
        # Find ingredient in inventory
        ing_slot = resources.find_item_slot(ingredient_id)
        if ing_slot is None:
            print(f"Error: Missing ingredient {ingredient_id}")
            return False
        
        # Move to crafting slot (this is highly simplified and depends on screen handler)
        # Assuming player inventory for now (2x2 grid)
        # Player crafting slots: 1, 2, 3, 4. Output: 0.
        click_inventory_slot(client, ing_slot)
        click_inventory_slot(client, slot)
    
    # 3. Click output slot
    # Output slot in player inventory is 0
    click_inventory_slot(client, 0)
    return True


def safe_goto(client, x: int, y: int, z: int, timeout: float = 120.0):
    """Navigate to coordinates and wait for arrival or failure."""
    # Use the bridge's goto handler directly - the chat-command facade
    # misparses the bridge reply and raises even when pathing starts.
    client.transport.dispatch("goto", {"x": x, "y": y, "z": z})
    start_time = time.time()
    while time.time() - start_time < timeout:
        response = client.transport.dispatch("get_state", {})
        pos = response.get("block_position", response.get("position", {}))
        px = pos.get("x", response.get("x", 0))
        py = pos.get("y", response.get("y", 0))
        pz = pos.get("z", response.get("z", 0))
        dist_sq = (px - x) ** 2 + (py - y) ** 2 + (pz - z) ** 2
        if dist_sq < 4:  # Within 2 blocks
            return True

        if not response.get("is_pathing", True) and dist_sq < 9:
            return True
                
        time.sleep(1.0)
    return False


def get_player_pos(client) -> tuple[float, float, float]:
    """Get the current player position as (x, y, z)."""
    state = client.transport.dispatch("get_state", {})
    pos = state.get("block_position", state.get("position", {"x": 0, "y": 0, "z": 0}))
    return (pos.get("x", 0), pos.get("y", 0), pos.get("z", 0))


def place_block(client, x: int, y: int, z: int, item_id: str) -> bool:
    """
    Place a block at specified coordinates.

    The bridge places whatever is in the main hand, so the item must be
    selected first. (select_slot only accepts a hotbar slot index - passing
    item_id to it is silently invalid, which used to make this a no-op.)
    """
    from .inventory import select_item

    if _block_at(client, x, y, z) == item_id:
        return True
    if not select_item(client, item_id, allow_swap=True):
        print(f"  place_block: '{item_id}' not available to select")
        return False
    time.sleep(0.2)

    try:
        client.transport.dispatch("place_block", {
            "x": x, "y": y, "z": z, "block": item_id,
        })
    except Exception:
        # A timeout may follow a physical placement. Reconcile; never replay.
        pass
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        try:
            if _block_at(client, x, y, z) == item_id:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def _block_at(client, x: int, y: int, z: int) -> str:
    response = client.transport.dispatch("get_block", {"x": x, "y": y, "z": z})
    data = response.get("data", response) if isinstance(response, dict) else {}
    if not isinstance(data, dict):
        return ""
    return str(data.get("id") or data.get("block") or "")


def clear_placement_volume(client, positions, *, timeout: float = 8.0) -> bool:
    """Dig every occupied position so a multi-block build cannot fail part-way.

    ``place_block`` raises "Target position is already occupied" against solid
    ground. A caller that places a structure block-by-block catches that at the
    top level -- but only *after* earlier blocks are already in the world, spent
    from the inventory and never recovered. Underground, where every candidate
    site is solid, each retry therefore costs materials and buys nothing.

    Live on A1 2026-09-02 at y=-32 in deepslate: the bot reached the portal
    build carrying exactly 10 obsidian, leaked 2 into a doomed attempt, dropped
    below the requirement, and every subsequent offset failed on ``hasObsidian:
    False`` -- having destroyed the very blocks it spent hours mining.

    An unreadable block counts as occupied, not clear: guessing "probably air"
    is what turns a transport blip into another leaked stack.
    """
    for x, y, z in positions:
        current = _block_at(client, x, y, z)
        if not current or current == "minecraft:void_air":
            print(f"  clear volume: ({x},{y},{z}) unreadable; refusing to place")
            return False
        if current in ("minecraft:air", "minecraft:cave_air"):
            continue
        client.transport.dispatch(
            "dig_block", {"x": x, "y": y, "z": z, "max_ticks": 160}
        )
        deadline = time.monotonic() + max(1.0, float(timeout))
        while time.monotonic() < deadline:
            time.sleep(0.3)
            if _block_at(client, x, y, z) in ("minecraft:air", "minecraft:cave_air"):
                break
        else:
            print(f"  clear volume: {current} at ({x},{y},{z}) would not break")
            return False
    return True


#: Fluids and air are not anchors: Minecraft refuses a placement whose six
#: neighbours are all non-solid.
_NON_SOLID = ("air", "water", "lava", "cave_air", "void_air")


def _is_solid(client, x: int, y: int, z: int) -> bool:
    block = _block_at(client, x, y, z)
    return bool(block) and block != "minecraft:unloaded" and not any(token in block for token in _NON_SOLID)


def has_placement_support(client, positions, *, floor: bool = True) -> bool:
    """True when a structure's blocks will have something to place against.

    A cleared site is not automatically a buildable one. Minecraft refuses a
    placement with no adjacent solid face, and an air pocket underground passes
    every "is it clear?" test while anchoring nothing.

    Live on A1 2026-09-02 at y=-48, immediately after site-clearing shipped:

        Building Nether portal frame at (-74, -48, 99)
        ERROR Portal construction failed: No solid block found to place
              against at BlockPos{x=-73, y=-48, z=99}

    Three attempts in six seconds, each spending obsidian before failing.
    """
    lowest = min(y for _x, y, _z in positions)
    if floor and not all(
        _is_solid(client, x, lowest - 1, z)
        for x, y, z in positions
        if y == lowest
    ):
        print("  placement support: nothing solid under the base")
        return False
    return True


def recover_placed(client, positions) -> int:
    """Dig back blocks placed into a build that then failed.

    Without this, a partial structure is a permanent loss: the blocks are out
    of the inventory and the caller reports failure, so the next attempt starts
    poorer than the last. Broken blocks drop where the bot is standing and are
    picked up automatically.
    """
    recovered = 0
    for x, y, z in positions:
        try:
            client.transport.dispatch(
                "dig_block", {"x": x, "y": y, "z": z, "max_ticks": 160}
            )
        except Exception:
            continue
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            time.sleep(0.3)
            if "air" in _block_at(client, x, y, z):
                recovered += 1
                break
    if recovered:
        print(f"  recovered {recovered} block(s) from the abandoned build")
    return recovered


#: Blocks a bare hand can break at a usable speed. A bot that has just drowned
#: respawns naked, so gating an escape dig on a pickaxe makes it a no-op in the
#: one situation it exists for -- and what sits above a submerged bot near
#: shore is almost always one of these.
HAND_BREAKABLE_BLOCKS = frozenset(
    """dirt coarse_dirt rooted_dirt grass_block podzol mycelium dirt_path
    farmland mud clay sand red_sand gravel soul_sand soul_soil snow snow_block
    moss_block""".split()
)
