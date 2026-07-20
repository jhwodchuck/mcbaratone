"""
Inventory management - Item counting, crafting, and organization.
"""

from typing import Dict, Optional, List, Tuple
import logging
import time

logger = logging.getLogger(__name__)

_last_inventory: Dict[str, int] = {}

def get_inventory(client) -> Dict[str, int]:
    """
    Get aggregated inventory counts.
    
    Returns:
        Dict of item_id -> count
    """
    global _last_inventory
    for attempt in range(3):
        try:
            response = client.transport.dispatch("get_inventory", {})
        except Exception:
            response = None
        if not response or (isinstance(response, dict) and response.get("error")):
            if attempt < 2:
                time.sleep(0.3)
            continue
        data = response.get("data", response)
        counts: Dict[str, int] = {}
        for section in ["inventory", "armor", "offhand"]:
            for item in data.get(section, []):
                item_id = item.get("id", "")
                count = item.get("count", 0)
                if item_id and count > 0:
                    counts[item_id] = counts.get(item_id, 0) + count
        _last_inventory = dict(counts)
        return counts
    logger.warning(
        "get_inventory: returning last-known inventory (%s) because bridge read failed",
        _last_inventory,
    )
    return dict(_last_inventory)


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
        time.sleep(0.2)
        client.transport.dispatch('inventory_click', {'slot': 36, 'type': 'PICKUP', 'button': 0})
        time.sleep(0.2)
        client.transport.dispatch('inventory_click', {'slot': slot, 'type': 'PICKUP', 'button': 0})
        time.sleep(0.2)
        client.transport.dispatch('select_slot', {'slot': 0})
        time.sleep(0.2)
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


_ARMOR_RANK = {
    "leather": 1,
    "golden": 2,
    "chainmail": 3,
    "iron": 4,
    "diamond": 5,
    "netherite": 6,
}
_ARMOR_PIECES = ("helmet", "chestplate", "leggings", "boots")
_PLAYER_ARMOR_CONTAINER_SLOTS = {
    "helmet": 5,
    "chestplate": 6,
    "leggings": 7,
    "boots": 8,
}


def _armor_identity(item_id: str):
    name = item_id.split(":", 1)[-1]
    for material, rank in _ARMOR_RANK.items():
        for piece in _ARMOR_PIECES:
            if name == f"{material}_{piece}":
                return material, piece, rank
    return None


def get_equipped_armor(client) -> Dict[str, str]:
    """Return equipped armor as ``piece -> item id`` from bridge truth."""
    try:
        response = client.transport.dispatch("get_inventory", {})
        data = response.get("data", response)
        equipped: Dict[str, str] = {}
        for item in data.get("armor", []):
            item_id = item.get("id", "")
            identity = _armor_identity(item_id)
            if identity and item.get("count", 0) > 0:
                equipped[identity[1]] = item_id
        return equipped
    except Exception as exc:
        logger.warning("Could not inspect equipped armor: %s", exc)
        return {}


def has_full_armor(client, minimum_material: str = "iron") -> bool:
    """Verify all four equipped pieces meet a minimum material tier."""
    minimum_rank = _ARMOR_RANK.get(minimum_material, _ARMOR_RANK["iron"])
    equipped = get_equipped_armor(client)
    for piece in _ARMOR_PIECES:
        identity = _armor_identity(equipped.get(piece, ""))
        if not identity or identity[2] < minimum_rank:
            return False
    return True


def equip_best_armor(client) -> int:
    """
    Equip best available armor from inventory.
    
    Returns:
        Number of armor pieces equipped
    """
    try:
        client.transport.dispatch("close_screen", {})
    except Exception:
        pass

    for piece in _ARMOR_PIECES:
        response = client.transport.dispatch("get_inventory", {})
        data = response.get("data", response)
        current = get_equipped_armor(client).get(piece)
        current_identity = _armor_identity(current or "")
        current_rank = current_identity[2] if current_identity else 0

        candidates = []
        for item in data.get("inventory", []):
            identity = _armor_identity(item.get("id", ""))
            if not identity or identity[1] != piece or item.get("count", 0) <= 0:
                continue
            candidates.append((identity[2], item))
        if not candidates:
            continue

        best_rank, best_item = max(candidates, key=lambda entry: entry[0])
        if best_rank <= current_rank:
            continue

        try:
            # QUICK_MOVE on a player-container slot is the same verified path
            # used by functional test T302.  Hotbar indices 0-8 map to
            # protocol slots 36-44; main inventory indices already match.
            if current:
                client.transport.dispatch(
                    "inventory_click",
                    {
                        "slot": _PLAYER_ARMOR_CONTAINER_SLOTS[piece],
                        "type": "QUICK_MOVE",
                        "button": 0,
                    },
                )
                time.sleep(0.2)
            inventory_slot = int(best_item["slot"])
            player_slot = 36 + inventory_slot if 0 <= inventory_slot <= 8 else inventory_slot
            client.transport.dispatch(
                "inventory_click",
                {"slot": player_slot, "type": "QUICK_MOVE", "button": 0},
            )
            deadline = time.time() + 3.0
            while time.time() < deadline:
                if get_equipped_armor(client).get(piece) == best_item["id"]:
                    break
                time.sleep(0.1)
        except Exception as exc:
            logger.warning("Failed to equip %s: %s", best_item.get("id"), exc)

    return len(get_equipped_armor(client))


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



_PLANK_WOODS = [
    "oak",
    "spruce",
    "birch",
    "dark_oak",
    "acacia",
    "jungle",
    "mangrove",
    "cherry",
    "pale_oak",
]


def _craft_family_count(client, item_id: str) -> int:
    """Plank requests count all wood types: the bridge crafts from whatever
    logs are in inventory (asking for oak_planks with birch logs yields
    birch_planks)."""
    if item_id.split(":")[-1].endswith("_planks"):
        return sum(count_item(client, f"minecraft:{w}_planks") for w in _PLANK_WOODS)
    return count_item(client, item_id)


def _craft_log_family_for_planks(log_family: str) -> str:
    family = log_family.split(":")[-1]
    if family.endswith("_log"):
        family = family[:-4]
    return f"minecraft:{family}_planks"


def _first_log_family_with_stock(client, required_logs: int = 1) -> str:
    """Return the first available log family with at least ``required_logs`` stock."""
    for wood in _PLANK_WOODS:
        log_id = f"minecraft:{wood}_log"
        if count_item(client, log_id) >= required_logs:
            return log_id
    return ""


def _ensure_planks_for_sticks(client, required_sticks: int) -> bool:
    """Ensure enough planks are available to craft ``required_sticks``."""
    required_planks = max(0, required_sticks) * 2
    current_planks = _craft_family_count(client, "minecraft:oak_planks")
    if current_planks >= required_planks:
        return True

    missing_planks = required_planks - current_planks
    while missing_planks > 0:
        log_family = _first_log_family_with_stock(client, 1)
        if not log_family:
            # No carried logs: gather a small reserve instead of failing the
            # whole tool craft (same ingredient-acquisition gap as the
            # stone-material fix; live blocker "failed to prepare planks for
            # minecraft:iron_pickaxe" with zero logs in inventory).
            from .resources import gather_wood  # lazy: resources imports this module

            needed_logs = max(1, (missing_planks + 3) // 4)
            print(
                f"  [Craft Debug] no carried logs for {missing_planks} planks; "
                f"gathering {needed_logs} logs first..."
            )
            gather_wood(client, count=needed_logs)
            log_family = _first_log_family_with_stock(client, 1)
        if not log_family:
            return False
        plank_family = _craft_log_family_for_planks(log_family)
        planks_to_craft = ((missing_planks + 3) // 4) * 4
        logs_to_use = min(count_item(client, log_family), max(1, (planks_to_craft + 3) // 4))
        planks_to_craft = max(4, min(planks_to_craft, logs_to_use * 4))
        if not craft(client, plank_family, planks_to_craft):
            return False
        current_planks = _craft_family_count(client, "minecraft:oak_planks")
        if current_planks >= required_planks:
            return True
        missing_planks = required_planks - current_planks

    return True


# Progression recipes the bridge cannot craft: native `craft` depends on
# recipe listing (broken on the live 1.21.x client) and `auto_craft` only
# knows a hardcoded slice (planks, sticks, stone/wood tools, iron
# pickaxe/sword, torch, chest, furnace, crafting table). Everything else on
# the road to the Dragon must be drivable through the verified manual grid
# (slots are 1-9 row-major; "#planks" matches any plank family). The bucket
# entry replaced the first live dead-end ("Prepare deep-mining tools +
# bucket"); the rest are the recipes the progression plan is known to need
# ahead of time: shield (iron defense), enchanting line (paper, book,
# bookshelf, enchanting table), nether line (flint and steel, blaze powder,
# eye of ender), and dragon-fight ranged gear (bow, arrow) plus ladders.
_MANUAL_GRID_RECIPES: Dict[str, Dict] = {
    "minecraft:bucket": {
        "placements": [
            ("minecraft:iron_ingot", 1),
            ("minecraft:iron_ingot", 3),
            ("minecraft:iron_ingot", 5),
        ],
    },
    "minecraft:shield": {
        "placements": [
            ("#planks", 1),
            ("minecraft:iron_ingot", 2),
            ("#planks", 3),
            ("#planks", 4),
            ("#planks", 5),
            ("#planks", 6),
            ("#planks", 8),
        ],
    },
    "minecraft:flint_and_steel": {
        "placements": [
            ("minecraft:iron_ingot", 1),
            ("minecraft:flint", 2),
        ],
    },
    "minecraft:paper": {
        "placements": [
            ("minecraft:sugar_cane", 1),
            ("minecraft:sugar_cane", 2),
            ("minecraft:sugar_cane", 3),
        ],
        "output": 3,
    },
    "minecraft:book": {
        "placements": [
            ("minecraft:paper", 1),
            ("minecraft:paper", 2),
            ("minecraft:paper", 3),
            ("minecraft:leather", 4),
        ],
    },
    "minecraft:bookshelf": {
        "placements": [
            ("#planks", 1),
            ("#planks", 2),
            ("#planks", 3),
            ("minecraft:book", 4),
            ("minecraft:book", 5),
            ("minecraft:book", 6),
            ("#planks", 7),
            ("#planks", 8),
            ("#planks", 9),
        ],
    },
    "minecraft:enchanting_table": {
        "placements": [
            ("minecraft:book", 2),
            ("minecraft:diamond", 4),
            ("minecraft:obsidian", 5),
            ("minecraft:diamond", 6),
            ("minecraft:obsidian", 7),
            ("minecraft:obsidian", 8),
            ("minecraft:obsidian", 9),
        ],
    },
    "minecraft:blaze_powder": {
        "placements": [("minecraft:blaze_rod", 1)],
        "output": 2,
    },
    "minecraft:ender_eye": {
        "placements": [
            ("minecraft:blaze_powder", 1),
            ("minecraft:ender_pearl", 2),
        ],
    },
    "minecraft:bow": {
        "placements": [
            ("minecraft:stick", 2),
            ("minecraft:string", 3),
            ("minecraft:stick", 4),
            ("minecraft:string", 6),
            ("minecraft:stick", 8),
            ("minecraft:string", 9),
        ],
    },
    "minecraft:arrow": {
        "placements": [
            ("minecraft:flint", 2),
            ("minecraft:stick", 5),
            ("minecraft:feather", 8),
        ],
        "output": 4,
    },
    "minecraft:ladder": {
        "placements": [
            ("minecraft:stick", 1),
            ("minecraft:stick", 3),
            ("minecraft:stick", 4),
            ("minecraft:stick", 5),
            ("minecraft:stick", 6),
            ("minecraft:stick", 7),
            ("minecraft:stick", 9),
        ],
        "output": 3,
    },
}


def _wait_craft_result(client, item_id: str, target: int, timeout: float = 6.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _craft_family_count(client, item_id) >= target:
            return True
        time.sleep(0.5)
    return False


def ensure_tool_sticks(client, item_id: str, count: int = 1) -> bool:
    """Craft the stick dependency required by a tool recipe.

    Tool crafting can be entered from several progression paths.  The bridge
    does not recursively craft recipe ingredients, so asking it for a tool
    while the player has planks but no sticks silently fails.  Prepare sticks
    in the player 2x2 grid before any code opens a crafting table.
    """
    from . import harness_ops

    parsed = harness_ops.parse_tool_id(item_id)
    if not parsed:
        return True

    _material, tool_type = parsed
    sticks_per_tool = 1 if tool_type == "sword" else 2
    required = sticks_per_tool * max(1, count)
    current = count_item(client, "minecraft:stick")
    if current >= required:
        return True

    try:
        client.transport.dispatch("close_screen", {})
    except Exception as exc:
        print(f"  [Craft Debug] could not close screen before crafting sticks: {exc}")

    missing = required - current
    if not _ensure_planks_for_sticks(client, missing):
        print(f"  [Craft Debug] failed to prepare planks for {item_id}")
        return False
    print(f"  [Craft Debug] preparing sticks for {item_id} ({current}/{required})...")
    if not craft(client, "minecraft:stick", max(4, missing)):
        return False
    return count_item(client, "minecraft:stick") >= required


def craft(client, item_id: str, count: int = 1) -> bool:
    """
    Craft an item, verified by inventory delta (a status=ok reply does not
    mean anything appeared - the 1.21.4 client has no recipe listing, so
    crafting silently no-ops in several situations).

    Fallback chain: bridge "craft" -> "auto_craft" -> manual grid clicks via
    the functional-harness library for recipes it knows (tools, beds, doors,
    chests).

    Args:
        item_id: Item to craft
        count: Number to craft

    Returns:
        True if the items verifiably appeared in inventory
    """
    from . import harness_ops

    if not ensure_tool_sticks(client, item_id, count):
        print(f"  [Craft Debug] could not prepare stick dependency for {item_id}")
        return False

    before = _craft_family_count(client, item_id)
    target = before + count

    # Recipe lookup is unavailable on this client, and the native recipe
    # handler can leave unrelated ingredients arranged in the player grid.
    # Planks are a one-cell 2x2 recipe, so drive that deterministic path first.
    if item_id.split(":")[-1].endswith("_planks") and harness_ops.available():
        try:
            if harness_ops.craft_planks_manual(client, item_id, count):
                return _craft_family_count(client, item_id) >= target
        except Exception as exc:
            print(f"  [Craft Debug] manual plank craft failed for {item_id}: {exc}")

    # Native 1.21.8 tool crafting can leave an invalid recipe in the 3x3
    # table.  Tool recipes have a verified manual implementation below, so
    # skip both corrupting native attempts and go straight to that path.
    manual_tool = (
        harness_ops.available()
        and harness_ops.parse_tool_id(item_id) is not None
    )

    if not manual_tool:
        try:
            client.transport.dispatch("craft", {"item": item_id, "count": count})
        except Exception as e:
            print(f"  [Craft Debug] craft dispatch failed for {item_id}: {e}")
        if _wait_craft_result(client, item_id, target):
            return True

        try:
            client.transport.dispatch("auto_craft", {"item": item_id, "quantity": count})
        except Exception as e:
            print(f"  [Craft Debug] auto_craft dispatch failed for {item_id}: {e}")
        if _wait_craft_result(client, item_id, target, timeout=8.0):
            return True

    if harness_ops.available():
        name = item_id.split(":")[-1]
        manual = None
        if harness_ops.parse_tool_id(item_id):
            manual = lambda: harness_ops.craft_tool_manual(client, item_id)
        elif harness_ops.parse_armor_id(item_id):
            manual = lambda: harness_ops.craft_armor_manual(client, item_id)
        elif name.endswith("_bed"):
            manual = lambda: harness_ops.craft_bed_manual(client, item_id)
        elif name.endswith("_door"):
            manual = lambda: harness_ops.craft_door_manual(client, item_id)
        elif name == "chest":
            manual = lambda: harness_ops.craft_chest_manual(client)
        elif name == "furnace":
            manual = lambda: harness_ops.craft_furnace_manual(client)
        elif name == "crafting_table":
            manual = lambda: harness_ops.craft_crafting_table_manual(client)
        elif item_id in _MANUAL_GRID_RECIPES:
            spec = _MANUAL_GRID_RECIPES[item_id]
            output_per_recipe = spec.get("output", 1)
            crafts = max(1, (count + output_per_recipe - 1) // output_per_recipe)
            manual = lambda: harness_ops.craft_recipe_manual(
                client,
                item_id,
                spec["placements"],
                crafts=crafts,
                output_per_recipe=output_per_recipe,
            )

        if manual is not None:
            print(f"  [Craft Debug] falling back to manual grid crafting for {item_id}...")
            try:
                if name == "crafting_table":
                    client.transport.dispatch("close_screen", {})
                else:
                    # A generic open GUI is not proof that the 3x3 crafting
                    # table is open (the player 2x2 screen also has slots).
                    # When no table is carried, make one before asking the
                    # placement harness to enumerate candidates. The old
                    # order spent tens of seconds trying to place a missing
                    # item at every candidate before reaching this recovery.
                    table_open = False
                    if count_item(client, "minecraft:crafting_table") == 0:
                        nearby = client.transport.dispatch(
                            "find_blocks",
                            {
                                "blocks": ["minecraft:crafting_table"],
                                "radius": 8,
                                "limit": 16,
                            },
                        )
                        found_tables = nearby.get("found")
                        if found_tables:
                            nearest = min(
                                found_tables,
                                key=lambda value: float(value.get("distance", float("inf"))),
                            )
                            table_open = harness_ops.ensure_crafting_table_open(
                                client,
                                table_pos=(
                                    int(nearest["x"]),
                                    int(nearest["y"]),
                                    int(nearest["z"]),
                                ),
                            )
                        elif found_tables is None:
                            # Compatibility for transports without block search.
                            table_open = harness_ops.ensure_crafting_table_open(client)

                        if not table_open:
                            client.transport.dispatch("close_screen", {})
                            if craft(client, "minecraft:crafting_table", 1):
                                table_open = harness_ops.ensure_crafting_table_open(client)
                    else:
                        table_open = harness_ops.ensure_crafting_table_open(client)
                    if not table_open:
                        print(f"  [Craft Debug] no verified crafting table for {item_id}")
                        return False
                if manual():
                    return True
            except Exception as e:
                print(f"  [Craft Debug] manual grid craft failed for {item_id}: {e}")

    print(f"  [Craft Debug] all methods failed for {item_id} (have {_craft_family_count(client, item_id)}, wanted {target})")
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


def _storage_position_from_mapping(value) -> Optional[Tuple[int, int, int]]:
    """Normalize a storage checkpoint or StateManager location entry."""
    if not isinstance(value, dict):
        return None
    data = value.get("data", value)
    if not isinstance(data, dict):
        return None
    try:
        return (int(data["x"]), int(data["y"]), int(data["z"]))
    except (KeyError, TypeError, ValueError):
        return None


def resolve_storage_location(
    client,
    state=None,
    *,
    verify: bool = True,
) -> Optional[Tuple[int, int, int]]:
    """Resolve the newest usable chest from production or legacy state.

    ``StateManager`` stores landmarks inside
    ``spawn_to_dragon_checkpoint.json`` while ``WorldState`` stores
    ``checkpoint_storage.json``. Older code wrote one format and read the
    other. This compatibility resolver accepts both and, by default, refuses
    stale coordinates that no longer contain a chest.
    """

    candidates: List[Tuple[int, int, int]] = []

    if state is not None:
        try:
            location_map = state.get_locations("chest")
            locations = location_map.get("chest", [])
        except (AttributeError, TypeError):
            locations = (
                getattr(state, "custom_data", {})
                .get("locations", {})
                .get("chest", [])
            )
        for location in reversed(locations):
            position = _storage_position_from_mapping(location)
            if position is not None and position not in candidates:
                candidates.append(position)

    from .state import WorldState

    legacy = WorldState(client).load_checkpoint("storage")
    legacy_position = _storage_position_from_mapping(legacy)
    if legacy_position is not None and legacy_position not in candidates:
        candidates.append(legacy_position)

    for position in candidates:
        if not verify:
            return position
        x, y, z = position
        try:
            block_id = client.transport.dispatch(
                "get_block", {"x": x, "y": y, "z": z}
            ).get("id", "")
        except Exception as exc:
            print(f"STORAGE: could not verify saved chest at {position}: {exc}")
            continue
        if "chest" in block_id:
            return position
        # The bridge reports void_air for coordinates in an unloaded chunk.
        # That is not proof that a persisted chest was removed. Return the
        # landmark so the caller can path there, load the chunk, and perform
        # the stronger open-container verification.
        if block_id == "minecraft:void_air":
            print(f"STORAGE: saved chest at {position} is in an unloaded chunk; retaining landmark")
            return position
        print(f"STORAGE: ignoring stale saved location {position} ({block_id or 'unknown'})")

    return None


def persist_storage_location(client, chest_pos, state=None) -> bool:
    """Persist a verified chest to both checkpoint formats.

    The production ``StateManager`` checkpoint is authoritative. The
    ``WorldState`` checkpoint is maintained for older callers until they are
    all migrated. When a StateManager is provided, flush it immediately so a
    crash after placement cannot orphan the physical chest.
    """

    try:
        x, y, z = (int(value) for value in chest_pos)
    except (TypeError, ValueError):
        return False

    try:
        block_id = client.transport.dispatch(
            "get_block", {"x": x, "y": y, "z": z}
        ).get("id", "")
    except Exception as exc:
        print(f"STORAGE: could not verify chest at {(x, y, z)}: {exc}")
        return False
    if "chest" not in block_id:
        print(f"STORAGE: refusing to checkpoint non-chest block at {(x, y, z)}")
        return False

    saved = False
    if state is not None and hasattr(state, "add_location"):
        try:
            state.add_location(
                "chest", x, y, z, tags=["storage"], client=client
            )
            saved = True
        except Exception as exc:
            print(f"STORAGE: failed to update production location: {exc}")

    from .state import WorldState

    try:
        WorldState(client).save_checkpoint(
            "storage", {"x": x, "y": y, "z": z}
        )
        saved = True
    except Exception as exc:
        print(f"STORAGE: failed to write compatibility checkpoint: {exc}")

    if state is not None and hasattr(state, "save_checkpoint"):
        try:
            state.save_checkpoint(get_inventory(client))
        except Exception as exc:
            print(f"STORAGE: failed to flush production checkpoint: {exc}")

    # The checkpoint answers "where is home storage?"; the catalog answers
    # "what containers exist and what did they contain last time?".  Keep
    # both because the catalog is intentionally not embedded in checkpoint
    # JSON as it can grow to hundreds of containers.
    try:
        from .storage_catalog import catalog_for

        dimension = client.transport.dispatch("get_state", {}).get(
            "dimension", "minecraft:overworld"
        )
        catalog_for(client, state).register_container(
            (x, y, z),
            dimension=str(dimension),
            container_type=block_id,
            label="home_storage",
            purpose="general_storage",
            metadata={"tags": ["storage", "home"]},
        )
    except Exception as exc:
        # Cataloging must never turn a successfully placed chest into a failed
        # survival action.  The next inspection will repair the catalog.
        print(f"STORAGE: catalog registration deferred ({exc})")

    return saved


def dump_to_chest(client, keep_items: List[str], state=None) -> int:
    """
    Dump all items except specified ones to nearby chest.
    
    Args:
        keep_items: List of item IDs to keep
        
    Returns:
        Number of stacks deposited, or ``-1`` when storage was unavailable.
    """
    chest_pos = resolve_storage_location(client, state=state, verify=True)
    if chest_pos is None:
        print("  No storage location saved.")
        return -1

    return deposit_excess_to_chest(
        client,
        chest_pos,
        keep_items=set(keep_items),
        state=state,
    )


EARLY_GAME_EXCESS_ITEMS = {
    "minecraft:birch_door",
    "minecraft:birch_sapling",
    "minecraft:bone",
    "minecraft:pumpkin_seeds",
    "minecraft:melon_seeds",
    "minecraft:wheat_seeds",
    "minecraft:beetroot_seeds",
    "minecraft:rotten_flesh",
    "minecraft:spider_eye",
    "minecraft:poisonous_potato",
    "minecraft:grass_block",
    "minecraft:moss_block",
    "minecraft:cobbled_deepslate",
    "minecraft:dirt",
    "minecraft:music_disc_cat",
    "minecraft:golden_horse_armor",
    "minecraft:gunpowder",
    "minecraft:leather",
    "minecraft:leaf_litter",
    "minecraft:name_tag",
    "minecraft:redstone",
}


def deposit_excess_to_chest(
    client,
    chest_pos: Tuple[int, int, int],
    deposit_items=None,
    keep_items=None,
    state=None,
) -> int:
    """Deposit selected player stacks into a verified base chest.

    Returns the number of stacks moved, or ``-1`` when the chest could not be
    verified/opened.  The allow-list makes this safe at phase boundaries:
    ores, tools, food, fuel, and future progression materials stay carried.
    """
    from . import harness_ops
    from .navigation import goto

    deposit_items = set(deposit_items or EARLY_GAME_EXCESS_ITEMS)
    keep_items = None if keep_items is None else set(keep_items)
    cx, cy, cz = (int(value) for value in chest_pos)

    try:
        client.transport.dispatch("close_screen", {})
    except Exception:
        pass

    block = client.transport.dispatch(
        "get_block", {"x": cx, "y": cy, "z": cz}
    ).get("id", "")
    if block == "minecraft:void_air":
        # A persisted home outside render distance is not missing. Path close
        # enough to load its chunk before deciding whether the chest survived.
        print(f"STORAGE: loading saved chest chunk at {(cx, cy, cz)}")
        if not goto(
            client,
            cx,
            cy,
            cz,
            timeout=120,
            check_interval=0.5,
            tolerance=3.0,
        ):
            print("STORAGE: could not reach saved chest chunk")
            return -1
        block = client.transport.dispatch(
            "get_block", {"x": cx, "y": cy, "z": cz}
        ).get("id", "")
    if "chest" not in block:
        print(f"STORAGE: expected chest is missing at {(cx, cy, cz)}")
        return -1

    live_state = client.transport.dispatch("get_state", {})
    position = live_state.get(
        "block_position", live_state.get("position", {})
    )
    distance = (
        (float(position.get("x", 0)) - cx) ** 2
        + (float(position.get("y", 0)) - cy) ** 2
        + (float(position.get("z", 0)) - cz) ** 2
    ) ** 0.5
    if distance > 4.5 and not harness_ops.move_near(
        client, cx, cy, cz, timeout=30.0
    ):
        print("STORAGE: could not move within interaction range")
        return -1

    # Movement/pathing must never be allowed to silently remove the target.
    block = client.transport.dispatch(
        "get_block", {"x": cx, "y": cy, "z": cz}
    ).get("id", "")
    if "chest" not in block:
        print("STORAGE: chest disappeared during approach")
        return -1

    # Stand at a real adjacent floor tile.  Merely being within four blocks is
    # insufficient when the crafting table/furnace blocks the ray trace from
    # the opposite side of this compact starter house.
    for sx, sy, sz in (
        (cx + 1, cy, cz),
        (cx + 1, cy, cz + 1),
        (cx, cy, cz + 1),
        (cx - 1, cy, cz),
    ):
        stand_block = client.transport.dispatch(
            "get_block", {"x": sx, "y": sy, "z": sz}
        ).get("id", "")
        floor_block = client.transport.dispatch(
            "get_block", {"x": sx, "y": sy - 1, "z": sz}
        ).get("id", "")
        if "air" not in stand_block or "air" in floor_block:
            continue
        goto(
            client,
            sx,
            sy,
            sz,
            timeout=20,
            check_interval=0.25,
            tolerance=0.5,
        )
        break

    screen = {}
    opened = False
    try:
        if harness_ops.available() and harness_ops.open_container(
            client, (cx, cy, cz), timeout=4.0
        ):
            screen = client.transport.dispatch("get_screen", {})
            data = screen.get("data", screen)
            total_slots = int(
                data.get("total_slots") or len(data.get("slots", []))
            )
            opened = total_slots in (63, 90)
    except Exception as exc:
        print(f"STORAGE: verified chest opener failed ({exc}); retrying natively")

    if not opened:
        for _attempt in range(3):
            client.transport.dispatch(
                "look_at", {"x": cx + 0.5, "y": cy + 0.5, "z": cz + 0.5}
            )
            time.sleep(0.2)
            client.transport.dispatch(
                "interact_block", {"x": cx, "y": cy, "z": cz}
            )
            for _ in range(20):
                screen = client.transport.dispatch("get_screen", {})
                data = screen.get("data", screen)
                total_slots = int(
                    data.get("total_slots") or len(data.get("slots", []))
                )
                if total_slots in (63, 90):
                    opened = True
                    break
                time.sleep(0.1)
            if opened:
                break
            client.transport.dispatch("close_screen", {})
    if not opened:
        client.transport.dispatch("close_screen", {})
        print("STORAGE: chest screen did not open")
        return -1

    data = screen.get("data", screen)
    slots = data.get("slots", [])
    total_slots = int(data.get("total_slots") or len(slots))
    container_slots = total_slots - 36
    if container_slots not in (27, 54):
        client.transport.dispatch("close_screen", {})
        print(f"STORAGE: unexpected container layout ({total_slots} slots)")
        return -1

    sync_id = data.get("sync_id", screen.get("sync_id"))
    deposited = 0
    for item in slots:
        slot = int(item.get("slot", -1))
        item_id = item.get("id")
        if slot < container_slots or int(item.get("count", 0)) <= 0:
            continue
        if keep_items is not None and item_id in keep_items:
            continue
        if keep_items is None and item_id not in deposit_items:
            continue
        payload = {"slot": slot, "type": "QUICK_MOVE", "button": 0}
        if sync_id is not None:
            payload["sync_id"] = sync_id
        client.transport.dispatch("inventory_click", payload)
        deposited += 1
        time.sleep(0.05)

    try:
        from .storage_catalog import catalog_for, observe_open_container

        final_screen = client.transport.dispatch("get_screen", {})
        observe_open_container(
            client,
            (cx, cy, cz),
            final_screen,
            state=state,
            container_type=block,
            label="home_storage",
            purpose="general_storage",
        )
        catalog_for(client, state).record_event(
            (cx, cy, cz),
            "deposit",
            dimension=str(
                client.transport.dispatch("get_state", {}).get(
                    "dimension", "minecraft:overworld"
                )
            ),
            details={"moved_stacks": deposited},
        )
    except Exception as exc:
        print(f"STORAGE: post-deposit catalog update deferred ({exc})")

    client.transport.dispatch("close_screen", {})
    print(f"STORAGE: deposited {deposited} excess stacks at home")
    return deposited


def withdraw_required_from_chest(
    client,
    chest_pos: Tuple[int, int, int],
    requirements: Dict[str, int],
    state=None,
) -> int:
    """Withdraw only banked stacks needed by the current objective.

    Returns the number of chest stacks moved, ``0`` when the player already
    carries every requirement, or ``-1`` when the checkpointed chest cannot be
    verified/opened.  Shift-clicking may retrieve more than the exact shortfall;
    that is preferable to splitting stacks through bridge-specific GUI packets.
    """
    from . import harness_ops

    remaining = {
        item_id: max(0, int(required) - count_item(client, item_id))
        for item_id, required in requirements.items()
    }
    remaining = {item_id: count for item_id, count in remaining.items() if count}
    if not remaining:
        return 0

    cx, cy, cz = (int(value) for value in chest_pos)
    block = client.transport.dispatch(
        "get_block", {"x": cx, "y": cy, "z": cz}
    ).get("id", "")
    if "chest" not in block:
        print(f"STORAGE: expected chest is missing at {(cx, cy, cz)}")
        return -1

    try:
        client.transport.dispatch("close_screen", {})
        opened = harness_ops.open_container(client, (cx, cy, cz), timeout=4.0)
    except Exception as exc:
        print(f"STORAGE: could not open supply chest ({exc})")
        return -1
    if not opened:
        print("STORAGE: supply chest screen did not open")
        return -1

    try:
        screen = client.transport.dispatch("get_screen", {})
        data = screen.get("data", screen)
        slots = data.get("slots", [])
        total_slots = int(data.get("total_slots") or len(slots))
        container_slots = total_slots - 36
        if container_slots not in (27, 54):
            print(f"STORAGE: unexpected container layout ({total_slots} slots)")
            return -1

        sync_id = data.get("sync_id", screen.get("sync_id"))
        moved = 0
        for item in slots:
            slot = int(item.get("slot", -1))
            item_id = str(item.get("id", ""))
            count = int(item.get("count", 0))
            if slot < 0 or slot >= container_slots or remaining.get(item_id, 0) <= 0:
                continue
            payload = {"slot": slot, "type": "QUICK_MOVE", "button": 0}
            if sync_id is not None:
                payload["sync_id"] = sync_id
            client.transport.dispatch("inventory_click", payload)
            remaining[item_id] = max(0, remaining[item_id] - count)
            moved += 1
            time.sleep(0.05)
        try:
            from .storage_catalog import catalog_for, observe_open_container

            final_screen = client.transport.dispatch("get_screen", {})
            observe_open_container(
                client,
                (cx, cy, cz),
                final_screen,
                state=state,
                container_type=block,
                label="home_storage",
                purpose="general_storage",
            )
            catalog_for(client, state).record_event(
                (cx, cy, cz),
                "withdraw",
                dimension=str(
                    client.transport.dispatch("get_state", {}).get(
                        "dimension", "minecraft:overworld"
                    )
                ),
                details={"moved_stacks": moved, "requirements": requirements},
            )
        except Exception as exc:
            print(f"STORAGE: post-withdraw catalog update deferred ({exc})")
        print(f"STORAGE: withdrew {moved} required stacks from home")
        return moved
    finally:
        client.transport.dispatch("close_screen", {})


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
