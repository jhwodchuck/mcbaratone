"""
Inventory management - Item counting, crafting, and organization.
"""

from typing import Dict, Optional, List
import logging
import time

logger = logging.getLogger(__name__)

def get_inventory(client) -> Dict[str, int]:
    """
    Get aggregated inventory counts.
    
    Returns:
        Dict of item_id -> count
    """
    try:
        response = client.transport.dispatch("get_inventory", {})
        data = response.get("data", response)
        counts: Dict[str, int] = {}
        
        for section in ["inventory", "armor", "offhand"]:
            for item in data.get(section, []):
                item_id = item.get("id", "")
                count = item.get("count", 0)
                if item_id and count > 0:
                    counts[item_id] = counts.get(item_id, 0) + count
        
        return counts

    except Exception as e:
        logger.exception(f"Exception in get_inventory: {e}")
        return {}


def count_item(client, item_id: str) -> int:
    """
    Count specific item in inventory.
    
    Args:
        item_id: Full item ID (e.g., "minecraft:diamond")
        
    Returns:
        Total count of item
    """
    inventory = get_inventory(client)
    return inventory.get(item_id, 0)


def has_items(client, requirements: Dict[str, int]) -> bool:
    """
    Check if inventory has all required items.
    
    Args:
        requirements: Dict of item_id -> minimum count
        
    Returns:
        True if all requirements met
    """
    inventory = get_inventory(client)
    
    for item_id, required in requirements.items():
        if inventory.get(item_id, 0) < required:
            return False
    return True


def find_item_slot(client, item_id: str) -> Optional[int]:
    """
    Find slot containing specified item.
    
    Args:
        item_id: Item ID to find
        
    Returns:
        Slot index or None
    """
    try:
        response = client.transport.dispatch("get_inventory", {})
        data = response.get("data", response)
        
        for item in data.get("inventory", []):
            if item.get("id") == item_id and item.get("count", 0) > 0:
                return item.get("slot")
        
        return None
        
    except Exception:
        return None


def select_item(client, item_id: str, allow_swap: bool = False) -> bool:
    """
    Select item in hotbar.
    
    Args:
        item_id: Item to select
        allow_swap: If True, swap item from inventory to hotbar if needed
        
    Returns:
        True if item found and selected
    """
    slot = find_item_slot(client, item_id)
    if slot is None:
        return False
    
    # Hotbar is slots 0-8
    if 0 <= slot <= 8:
        client.transport.dispatch("select_slot", {"slot": slot})
        return True
    
    if allow_swap:
        # Move to Hotbar 0 (Protocol 36)
        # Use PICKUP sequence
        client.transport.dispatch('inventory_click', {'slot': slot, 'type': 'PICKUP', 'button': 0})
        time.sleep(0.15)
        client.transport.dispatch('inventory_click', {'slot': 36, 'type': 'PICKUP', 'button': 0})
        time.sleep(0.15)
        client.transport.dispatch('inventory_click', {'slot': slot, 'type': 'PICKUP', 'button': 0})
        time.sleep(0.15)
        client.transport.dispatch('select_slot', {'slot': 0})
        return True
        
    return False


def equip_best_weapon(client) -> bool:
    """Equip best available weapon/tool."""
    weapons = [
        "minecraft:netherite_sword", "minecraft:diamond_sword", "minecraft:iron_sword", "minecraft:stone_sword", "minecraft:golden_sword", "minecraft:wooden_sword",
        "minecraft:netherite_axe", "minecraft:diamond_axe", "minecraft:iron_axe", "minecraft:stone_axe", "minecraft:golden_axe", "minecraft:wooden_axe",
        "minecraft:netherite_pickaxe", "minecraft:diamond_pickaxe", "minecraft:iron_pickaxe", "minecraft:stone_pickaxe", "minecraft:wooden_pickaxe"
    ]
    
    for weapon in weapons:
        if select_item(client, weapon, allow_swap=True):
            return True
            
    return False


def equip_best_armor(client) -> int:
    """
    Equip best available armor from inventory.
    
    Returns:
        Number of armor pieces equipped
    """
    # Armor materials from worst to best
    materials = ["leather", "chainmail", "iron", "diamond", "netherite"]
    armor_slots = ["helmet", "chestplate", "leggings", "boots"]
    
    equipped = 0
    inventory = get_inventory(client)
    
    for slot_type in armor_slots:
        for material in reversed(materials):  # Best first
            item_id = f"minecraft:{material}_{slot_type}"
            if inventory.get(item_id, 0) > 0:
                # Try to equip
                slot = find_item_slot(client, item_id)
                if slot is not None:
                    # Right-click to auto-equip
                    try:
                        client.transport.dispatch("inventory_click", {
                            "slot": slot,
                            "type": "PICKUP",
                            "button": 1,  # Right-click
                        })
                        equipped += 1
                    except:
                        pass
                break
    
    return equipped


def equip_offhand(client, item_id: str) -> bool:
    """
    Equip item to offhand slot.
    """
    try:
        slot = find_item_slot(client, item_id)
        if slot is None:
            return False
            
        # 45 is usually offhand
        # or use inventory_click with swap
        # The bridge might not have a direct 'equip_offhand' macro, so we try a click interaction or dispatch a simple 'equip' check if available.
        # Standard minecraft protocol: Swap item to slot 45 (offhand)
        
        # NOTE: Baritone generic 'click' might be needed.
        # Let's try to swap slot with offhand slot (45)
        
        client.transport.dispatch("inventory_click", {
            "slot": slot,
            "type": "SWAP",
            "button": 40, # 'F' key swap usually? Or verify slot ID 45? 
            # Actually SWAP with offhand is a specific packet action often found in 1.9+
            # If the bridge exposes standard click:
            # Slot 45 is offhand.
            
            # Simple fallback: use "equip" command if the bridge supports it? 
            # Or assume the bridge has "equip" macro.
        })
        
        # Actually, let's look at the bridge capabilities. 
        # If 'inventory_click' is raw, we need exact slot IDs.
        # Use simpler approach: Send a client-side command if possible, or try to drag-and-drop.
        
        # Attempt 1: Swap with offhand key (F)
        # client.transport.dispatch("input", {"key": "key.swapOffhand"}) 
        # But that swaps current hotbar item.
        
        # Attempt 2: Pickup item, Click offhand slot (45)
        # Click source
        client.transport.dispatch("inventory_click", {"slot": slot, "type": "PICKUP", "button": 0})
        time.sleep(0.1)
        # Click offhand (45)
        client.transport.dispatch("inventory_click", {"slot": 45, "type": "PICKUP", "button": 0})
        time.sleep(0.1)
        # If we had something in offhand, it's now on cursor, put it back in source (or first empty)
        # For simplicity, put back in source (swap)
        client.transport.dispatch("inventory_click", {"slot": slot, "type": "PICKUP", "button": 0})
        
        return True
    except Exception as e:
        print(f"Offhand equip failed: {e}")
        return False



def craft(client, item_id: str, count: int = 1) -> bool:
    """
    Attempt to craft an item.
    
    Note: This is a high-level request - full crafting automation
    requires recipe lookup and slot manipulation.
    
    Args:
        item_id: Item to craft
        count: Number to craft
        
    Returns:
        True if crafting request was sent
    """
    try:
        response = client.transport.dispatch("craft", {
            "item": item_id,
            "count": count,
        })
        if response.get("status") == "ok":
            return True
        
        # Verbose error logging
        err = response.get("error", "Unknown error")
        print(f"  [Craft Debug] Crafting failed: {err}")
        return False
    except Exception as e:
        print(f"  [Craft Debug] Exception: {e}")
        return False


def get_recipes_for(client, item_id: str) -> List[Dict]:
    """
    Get crafting recipes for an item.
    
    Args:
        item_id: Item to get recipes for
        
    Returns:
        List of recipe dictionaries
    """
    try:
        response = client.transport.dispatch("get_recipes", {
            "filter": item_id,
            "limit": 5,
        })
        
        if response.get("status") == "ok":
            return response.get("data", {}).get("recipes", [])
        return []
        
    except Exception:
        return []


def dump_to_chest(client, keep_items: List[str]) -> int:
    """
    Dump all items except specified ones to nearby chest.
    
    Args:
        keep_items: List of item IDs to keep
        
    Returns:
        Number of items deposited
    """
    from .state import WorldState
    
    # 1. Get Storage Location
    ws = WorldState(client)
    storage_data = ws.load_checkpoint("storage")
    
    if not storage_data:
        print("  No storage location saved.")
        return 0
        
    chest_pos = storage_data.get("data", {})
    cx = chest_pos.get("x")
    cy = chest_pos.get("y")
    cz = chest_pos.get("z")
    
    if cx is None:
        print("  Invalid storage checkpoint data.")
        return 0
        
    print(f"STORAGE: Going UP TO chest at {cx}, {cy}, {cz}...")
    
    # Do NOT go to the exact chest block (Baritone might mine it to stand there)
    # Be smarter: find an adjacent spot that is air
    target_pos = None
    best_dist = 999
    
    # Check 4 cardinal neighbors + diagonals
    candidates = [
        (cx+1, cy, cz), (cx-1, cy, cz), 
        (cx, cy, cz+1), (cx, cy, cz-1),
        (cx+1, cy, cz+1), (cx-1, cy, cz-1),
        (cx+1, cy, cz-1), (cx-1, cy, cz+1)
    ]
    
    current_pos = client.transport.dispatch("get_player_pos", {})
    px, py, pz = current_pos[0], current_pos[1], current_pos[2]
    
    for tx, ty, tz in candidates:
        # Check if safe (air/replaceable)
        check = client.transport.dispatch('get_block', {'x': tx, 'y': ty, 'z': tz})
        bid = check.get('id', '')
        
        # We prefer air, but will accept standing on something solid if the space ITSELF is air is confusing.
        # "get_block" returns the block AT that coordinate. We want to stand IN air ON TOP of solid.
        # Baritone "goto" expects the coordinate of the floor or the target?
        # Baritone "goto x y z" usually means "stand at x y z" (so x,y,z should be navigable space, i.e. air above solid).
        
        # Let's check if the block AT target is passable (air, grass, carpet)
        is_passable = 'air' in bid or 'grass' in bid or 'carpet' in bid or 'fern' in bid
        
        if is_passable:
            # Check distance to player
            dist = ((tx-px)**2 + (ty-py)**2 + (tz-pz)**2)**0.5
            if dist < best_dist:
                best_dist = dist
                target_pos = (tx, ty, tz)
    
    if target_pos:
        print(f"  Navigating to adjacent spot {target_pos}...")
        client.transport.dispatch("goto", {"x": target_pos[0], "y": target_pos[1], "z": target_pos[2]})
    else:
        print("  No adjacent clear spot found! Trying generic approach (may break chest)...")
        client.transport.dispatch("goto", {"x": cx, "y": cy+1, "z": cz}) # Try standing on top?
    
    # Wait for arrival (simple heuristic or loop)
    time.sleep(1)
    # Check distance?
    for _ in range(20):
        pos = client.transport.dispatch("get_player_pos", {})
        dx = pos[0] - cx
        dz = pos[2] - cz
        if (dx*dx + dz*dz)**0.5 < 4:
            break
        time.sleep(1)
        
    print("STORAGE: Opening chest...")
    client.transport.dispatch("interact_block", {"x": cx, "y": cy, "z": cz})
    time.sleep(2.0) # Wait for UI
    
    # 2. Dump Items
    # We need to know what slots to click.
    # We can get inventory, identify non-keep items, and shift-click them.
    # Note: Shift-clicking moves to open container.
    
    inv = get_inventory(client)
    deposited = 0
    
    # Get raw inventory logic to see slots
    try:
        raw_inv = client.transport.dispatch("get_inventory", {})
        items = raw_inv.get("inventory", [])
        
        # Sort by slot to avoid messing up order while clicking?
        # Actually random access for shift-click is fine.
        
        for item in items:
            item_id = item.get("id")
            slot = item.get("slot")
            
            # Skip hotbar? Or allow dumping hotbar?
            # Typically we keep tools in hotbar.
            # keep_items should handle this.
            
            if item_id and item_id not in keep_items:
                # Dump it
                # Protocol 9-35 is main inv, 0-8 is hotbar.
                # Shift-click sends it to chest.
                client.transport.dispatch("inventory_click", {
                    "slot": slot,
                    "type": "QUICK_MOVE", # Shift-click
                    "button": 0
                })
                deposited += 1
                time.sleep(0.1)
                
    except Exception as e:
        print(f"Storage dump error: {e}")
        
    client.transport.dispatch("close_screen", {})
    print(f"STORAGE: Deposited {deposited} stacks.")
    return deposited


def check_craft(client, output_item: str, count: int = 1) -> Dict:
    """Check if a given output_item can be crafted.

    Tries the bridge `check_craft` route if available, otherwise falls back to
    querying recipes and checking inventory locally.

    Returns:
        {"can_craft": bool, "missing": [{"item": str, "count": int}], "recipe_id": Optional[str]}
    """
    try:
        # Try bridge-supported endpoint first
        resp = client.transport.dispatch("check_craft", {"output_item": output_item, "count": count})
        if resp.get("status") == "ok":
            data = resp.get("data", {})
            return {
                "can_craft": bool(data.get("can_craft", False)),
                "missing": data.get("missing", []),
                "recipe_id": data.get("recipe_id"),
            }
    except Exception:
        # Fallback to local inference
        pass

    # Fallback: get recipes and check inventory
    recipes = get_recipes_for(client, output_item)
    inv = get_inventory(client)

    for r in recipes:
        req = r.get("requires", {}) or r.get("ingredients", {})
        missing = []
        ok = True
        for item, needed in req.items():
            if inv.get(item, 0) < needed * count:
                missing.append({"item": item, "count": needed * count - inv.get(item, 0)})
                ok = False
        if ok:
            return {"can_craft": True, "missing": [], "recipe_id": r.get("id")}

    # If none found craftable
    return {"can_craft": False, "missing": [{"item": "unknown", "count": 0}], "recipe_id": None}


def craft_item(client, recipe_id: str, count: int = 1) -> bool:
    """Invoke bridge craft by recipe_id (preferred) or fall back to generic craft(item).

    Returns True on success.
    """
    try:
        resp = client.transport.dispatch("craft", {"recipe_id": recipe_id, "count": count})
        # If bridge returns structured data, check it
        if resp.get("status") == "ok":
            data = resp.get("data", {})
            # Some bridge stubs return crafted boolean in data
            return bool(data.get("crafted", True))
        # Older craft shim returns a bare dict
        return resp.get("crafted", True)
    except Exception:
        # Fallback: attempt best-effort craft using the older 'craft' helper
        try:
            return craft(client, recipe_id, count)
        except Exception:
            return False
def is_full(client) -> bool:
    """Check if inventory is full (no empty slots)."""
    try:
        raw_inv = client.transport.dispatch("get_inventory", {})
        items = raw_inv.get("inventory", [])
        # Normal inventory has 36 slots (0-35). Count occupied ones.
        occupied = 0
        for item in items:
            if item.get("id") and item.get("count", 0) > 0:
                occupied += 1
        return occupied >= 36
    except Exception:
        return False

def drop_items(client, item_ids: List[str]) -> int:
    """
    Drop specified items from inventory to clear space.
    
    Args:
        item_ids: List of item IDs to drop (e.g. ["minecraft:cobblestone", "minecraft:dirt"])
        
    Returns:
        Number of stacks/slots dropped
    """
    dropped = 0
    try:
        raw_inv = client.transport.dispatch("get_inventory", {})
        items = raw_inv.get("inventory", [])
        
        for item in items:
            item_id = item.get("id")
            slot = item.get("slot")
            
            if item_id in item_ids:
                # Drop item using Drop Key (Q) or throwing from inventory
                # throwing from inventory (Ctrl+Q equivalent or clicking outside)
                # Using 'THROW' action on the slot
                client.transport.dispatch("inventory_click", {
                    "slot": slot,
                    "type": "THROW",
                    "button": 1 # 1 = Drop stack, 0 = Drop single?
                })
                dropped += 1
                time.sleep(0.1)
                
    except Exception as e:
        print(f"Drop items error: {e}")
        
    return dropped
