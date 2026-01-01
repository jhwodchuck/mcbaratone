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
    client.command.run(f"#goto {x} {y} {z}")
    start_time = time.time()
    while time.time() - start_time < timeout:
        response = client.transport.dispatch("get_state", {})
        pos = response.get("block_position", response.get("position", {}))
        px = pos.get("x", response.get("x", 0))
        pz = pos.get("z", response.get("z", 0))
        dist_sq = (px - x) ** 2 + (pz - z) ** 2
        if dist_sq < 4:  # Within 2 blocks
            return True

        if not response.get("is_pathing", True) and dist_sq < 9:
            return True
                
        time.sleep(1.0)
    return False


def get_player_pos(client) -> Dict[str, float]:
    """Get the current player position."""
    state = client.transport.dispatch("get_state", {})
    return state.get("position", {"x": 0, "y": 0, "z": 0})


def place_block(client, x: int, y: int, z: int, item_id: str) -> bool:
    """Place a block at specified coordinates."""
    # First select the item
    client.transport.dispatch("select_slot", {"item_id": item_id})
    time.sleep(0.2)
    
    response = client.transport.dispatch("place_block", {
        "x": x,
        "y": y,
        "z": z
    })
    return response.get("status") == "ok" and response.get("data", {}).get("placed", False)
