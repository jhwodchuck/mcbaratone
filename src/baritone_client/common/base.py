"""
Base building utilities - Shelter, storage, and infrastructure.
"""

import contextlib
import math
import time
from typing import Optional, Tuple
from .navigation import goto, find_nearby_block
from .automation_utils import get_player_pos
from .build_safety import (
    BuildSurvivalHold as _BuildSurvivalHold,
    has_build_survival_margin as _has_build_survival_margin,
)
from .inventory import count_item, select_item, craft
from .site_selection import (
    find_flat_ground,
    find_flat_site_in_view as _find_flat_site_in_view,
    surface_y_at as _surface_y_at,
)
from .runtime_artifacts import append_world_map_entry


def is_position_safe(client, x: int, y: int, z: int) -> bool:
    """Check if a position is safe for placement (not liquid)."""
    try:
        # Scan area around player to check for liquids
        # Radius 8 covers typical reach distance
        res = client.transport.dispatch("get_view", {"radius": 8})
        voxels = res.get("voxels", [])
        
        for v in voxels:
            if v["x"] == x and v["y"] == y and v["z"] == z:
                # Found the block at target position
                block_id = v.get("id", "")
                if "water" in block_id or "lava" in block_id:
                    print(f"  Target ({x}, {y}, {z}) is liquid: {block_id}")
                    return False
        return True
    except Exception as e:
        print(f"  Safety check error: {e}")
        return True



def robust_place(client, x: int, y: int, z: int, item_id: str) -> bool:
    """
    Place item_id at exactly (x, y, z), preferring the functional-harness
    placement (moves within reach, clears obstructions, retries, verifies).
    Falls back to select_item + safe_place_block when the harness is
    unavailable.
    """
    from . import harness_ops
    if harness_ops.available():
        try:
            if harness_ops.place_block_exact(client, x, y, z, item_id):
                return True
            print(f"  harness place returned False at {(x, y, z)}")
        except Exception as e:
            print(f"  harness place failed at {(x, y, z)}: {e}")
    if not select_item(client, item_id, allow_swap=True):
        return False
    return safe_place_block(client, x, y, z, block_id=item_id)


def first_available_item(client, candidates) -> Optional[str]:
    """Return the first item id from candidates present in inventory."""
    for item_id in candidates:
        if count_item(client, item_id) > 0:
            return item_id
    return None


def _prepare_house_planks(client, required_planks: int) -> bool:
    """Gather and convert enough mixed-family wood for a house stage."""
    from .resources import LOG_TO_PLANKS, gather_wood

    total_planks = sum(count_item(client, item_id) for item_id in _ALL_PLANKS)
    if total_planks >= required_planks:
        return True

    # gather_wood's count is an absolute log-equivalent target and includes
    # planks already carried.  Passing only the shortfall makes an inventory
    # such as 12 planks incorrectly satisfy a request for 18.
    required_log_equivalents = math.ceil(required_planks / 4)
    print("Gathering wood for good house...")
    if not gather_wood(client, count=required_log_equivalents):
        return False

    client.transport.dispatch("close_screen", {})
    for log_id, plank_id in LOG_TO_PLANKS.items():
        total_planks = sum(count_item(client, item_id) for item_id in _ALL_PLANKS)
        if total_planks >= required_planks:
            break
        log_count = count_item(client, log_id)
        if log_count <= 0:
            continue
        missing = required_planks - total_planks
        craft_output = min(log_count * 4, math.ceil(missing / 4) * 4)
        if not craft(client, plank_id, craft_output):
            return False

    return sum(count_item(client, item_id) for item_id in _ALL_PLANKS) >= required_planks


def safe_place_block(client, x, y, z, max_depth=2, block_id: str | None = None) -> bool:
    """
    Attempt to place a block, adding support if needed.
    """
    try:
        payload = {"x": x, "y": y, "z": z}
        if block_id is not None:
            payload["block"] = block_id
        # The bridge can acknowledge the interaction before the server's
        # block update arrives.  Live, that produced an endless 48/49 floor:
        # every command returned success while the target remained air. Poll
        # the world postcondition and retry transient acknowledgements rather
        # than reporting a placement that never happened.
        for attempt in range(3):
            client.transport.dispatch("place_block", payload)
            if block_id is None:
                return True
            for _ in range(3):
                if _house_block_id(client, x, y, z) == block_id:
                    return True
                time.sleep(0.1)
            if attempt < 2:
                select_item(client, block_id, allow_swap=True)
        return False
    except Exception as e:
        msg = str(e)
        if "No solid block found to place against" in msg and max_depth > 0:
            # The bridge already scans all six directions for something to
            # place against, so reaching here means the target is genuinely
            # floating and we must manufacture a neighbour.
            #
            # This used to only ever build downward. Under a roof that means
            # dropping support blocks into the room below: wrong material in
            # the wrong place, and each of those is itself unsupported, so one
            # miss cascades into a column of junk through the interior. Try
            # the lateral neighbours first -- for a roof course or a wall the
            # real support is the block beside it -- and fall back to below
            # only when nothing else works.
            for sx, sy, sz in (
                (x - 1, y, z),
                (x + 1, y, z),
                (x, y, z - 1),
                (x, y, z + 1),
                (x, y - 1, z),
            ):
                print(f"  Placing support block at ({sx}, {sy}, {sz})...")
                if not safe_place_block(
                    client, sx, sy, sz, max_depth - 1, block_id=block_id
                ):
                    continue
                time.sleep(0.2)
                try:
                    payload = {"x": x, "y": y, "z": z}
                    if block_id is not None:
                        payload["block"] = block_id
                    client.transport.dispatch("place_block", payload)
                    return True
                except Exception:
                    # That neighbour did not unblock it; try the next face
                    # rather than giving up on the whole placement.
                    continue
        elif "Target position is already occupied" in msg:
            # Occupied is success only when the requested block is already
            # there. Treating any obstruction as success masked live acacia
            # logs in a cobblestone floor until final verification, causing
            # the same repair to replay forever.
            if block_id is None:
                return False
            return _house_block_id(client, x, y, z) == block_id
        
        # Only print non-routine errors to avoid spam
        if "No solid block" not in msg:
            print(f"  Place block failed at ({x}, {y}, {z}): {msg}")
        return False


def build_dirt_shelter(
    client,
    x: int,
    y: int,
    z: int,
    size: int = 3,
) -> bool:
    """
    Build a basic dirt shelter (walls and roof).
    
    Args:
        client: Baritone client
        x, y, z: Base corner position
        size: Interior size
        
    Returns:
        True if shelter built
    """
    # Check for dirt/cobblestone blocks
    dirt_count = count_item(client, "minecraft:dirt")
    cobble_count = count_item(client, "minecraft:cobblestone")
    
    if dirt_count + cobble_count < 30:
        print(f"  Need 30+ blocks for shelter, have {dirt_count + cobble_count}")
        return False
    
    # Pick building material
    material = "minecraft:cobblestone" if cobble_count >= 30 else "minecraft:dirt"

    # Build walls (simplified - 4 walls, 3 high)
    for wall_y in range(3):
        # North wall
        for dx in range(size + 2):
            robust_place(client, x + dx, y + wall_y, z, material)
            time.sleep(0.1)
        # South wall
        for dx in range(size + 2):
            robust_place(client, x + dx, y + wall_y, z + size + 1, material)
            time.sleep(0.1)
        # West wall
        for dz in range(1, size + 1):
            robust_place(client, x, y + wall_y, z + dz, material)
            time.sleep(0.1)
        # East wall
        for dz in range(1, size + 1):
            robust_place(client, x + size + 1, y + wall_y, z + dz, material)
            time.sleep(0.1)

    # Build roof
    for dx in range(size + 2):
        for dz in range(size + 2):
            robust_place(client, x + dx, y + 3, z + dz, material)
            time.sleep(0.1)

    return True


def place_crafting_table(
    client,
    x: int,
    y: int,
    z: int,
    save_as_base: bool = False,
) -> bool:
    """Place a crafting table, optionally promoting it to the main base.

    Mining workstations are temporary points of interest.  Treating every
    table as the main base previously redirected ``#goto base`` into a deep
    cave and made safe return-home logic impossible.
    """
    if _house_block_id(client, x, y, z) == "minecraft:crafting_table":
        if save_as_base:
            client.transport.dispatch(
                "chat",
                {"message": f"#waypoint save base {x} {y} {z}"},
            )
        return True

    if count_item(client, "minecraft:crafting_table") < 1:
        # Try to craft one
        plank_types = [
            "minecraft:oak_planks", "minecraft:spruce_planks", "minecraft:birch_planks",
            "minecraft:jungle_planks", "minecraft:acacia_planks", "minecraft:dark_oak_planks",
            "minecraft:mangrove_planks", "minecraft:cherry_planks", "minecraft:bamboo_planks",
            "minecraft:crimson_planks", "minecraft:warped_planks"
        ]
        if sum(count_item(client, p) for p in plank_types) >= 4:
            craft(client, "minecraft:crafting_table", 1)
            time.sleep(0.5)
        else:
            print("  Need crafting table or 4 planks (any type)")
            return False
    
    if not is_position_safe(client, x, y, z):
        print(f"  Skipping placement at ({x}, {y}, {z}) - Target is liquid")
        return False

    if not robust_place(client, x, y, z, "minecraft:crafting_table"):
        print(f"  Placement failed via robust_place")
        return False
        
    time.sleep(0.5)
    
    # Verify placement
    try:
        block_res = client.transport.dispatch("get_block", {"x": x, "y": y, "z": z})
        if block_res.get("id") != "minecraft:crafting_table":
            print(f"  Placement verification failed at ({x}, {y}, {z}): Found {block_res.get('id')}")
            return False
    except Exception:
        pass
        
    # Safeguard: Blacklist crafting tables so we don't accidentally mine it
    # Must use 'chat' route for Baritone commands
    client.transport.dispatch("chat", {"message": "#blacklist minecraft:crafting_table"})
    time.sleep(0.5)
    
    if save_as_base:
        client.transport.dispatch(
            "chat",
            {"message": f"#waypoint save base {x} {y} {z}"},
        )
        print(f"  *** BASE LOCATION SET to ({x}, {y}, {z}) ***")
    
    label = "Crafting Table/Base" if save_as_base else "Crafting Table"
    if not append_world_map_entry(label, (x, y, z)):
        print("  Crafting-table landmark already recorded in runtime evidence")
    
    return True


def place_furnace(client, x: int, y: int, z: int) -> bool:
    """
    Place a furnace at specified location.
    
    Returns:
        True if placed
    """
    if _house_block_id(client, x, y, z) in {
        "minecraft:furnace", "minecraft:blast_furnace"
    }:
        return True

    if count_item(client, "minecraft:furnace") < 1:
        # Try to craft one
        if count_item(client, "minecraft:cobblestone") >= 8:
            craft(client, "minecraft:furnace", 1)
            time.sleep(0.5)
        else:
            print("  Need furnace or 8 cobblestone")
            return False
    
    if not is_position_safe(client, x, y, z):
        print(f"  Skipping placement at ({x}, {y}, {z}) - Target is liquid")
        return False

    if not robust_place(client, x, y, z, "minecraft:furnace"):
        print(f"  Place furnace failed")
        return False

    time.sleep(0.5)
    return True


def place_chest(
    client,
    x: int,
    y: int,
    z: int,
    purpose: str = "base_storage",
) -> bool:
    """
    Place a chest at specified location.

    Returns:
        True if placed
    """
    existing_block = _house_block_id(client, x, y, z)
    if existing_block in {
        "minecraft:chest", "minecraft:trapped_chest"
    }:
        try:
            from .storage_catalog import catalog_for

            dimension = client.transport.dispatch("get_state", {}).get(
                "dimension", "minecraft:overworld"
            )
            catalog_for(client).register_container(
                (x, y, z),
                dimension=str(dimension),
                container_type=existing_block,
                purpose=purpose,
            )
        except Exception as exc:
            print(f"  Storage catalog registration deferred: {exc}")
        return True

    if count_item(client, "minecraft:chest") < 1:
        # Try to craft one (any plank type works)
        plank_types = [
            "minecraft:oak_planks", "minecraft:spruce_planks", "minecraft:birch_planks",
            "minecraft:jungle_planks", "minecraft:acacia_planks", "minecraft:dark_oak_planks",
            "minecraft:mangrove_planks", "minecraft:cherry_planks", "minecraft:bamboo_planks",
        ]
        if sum(count_item(client, p) for p in plank_types) >= 8:
            craft(client, "minecraft:chest", 1)
            time.sleep(0.5)
        else:
            print("  Need chest or 8 planks (any type)")
            return False

    if not is_position_safe(client, x, y, z):
        print(f"  Skipping placement at ({x}, {y}, {z}) - Target is liquid")
        return False

    if not robust_place(client, x, y, z, "minecraft:chest"):
        print(f"  Place chest failed")
        return False

    time.sleep(0.5)
    try:
        from .storage_catalog import catalog_for

        dimension = client.transport.dispatch("get_state", {}).get(
            "dimension", "minecraft:overworld"
        )
        catalog_for(client).register_container(
            (x, y, z),
            dimension=str(dimension),
            container_type="minecraft:chest",
            purpose=purpose,
        )
    except Exception as exc:
        print(f"  Storage catalog registration deferred: {exc}")
    return True


def place_bed(client, x: int, y: int, z: int) -> bool:
    """
    Place a bed at specified location.
    
    Returns:
        True if placed
    """
    # Check for any bed color
    bed_types = [
        "minecraft:white_bed", "minecraft:red_bed", "minecraft:blue_bed",
        "minecraft:green_bed", "minecraft:black_bed", "minecraft:yellow_bed",
        "minecraft:orange_bed", "minecraft:pink_bed", "minecraft:purple_bed",
    ]
    
    bed_item = first_available_item(client, bed_types)
    if bed_item is None:
        print("  Need a bed (craft from wool + planks)")
        return False

    if not is_position_safe(client, x, y, z):
        print(f"  Skipping placement at ({x}, {y}, {z}) - Target is liquid")
        return False

    if not robust_place(client, x, y, z, bed_item):
        print(f"  Place bed failed")
        return False

    time.sleep(0.5)
    return True


def place_torch(client, x: int, y: int, z: int) -> bool:
    """
    Place a torch at the specified location.
    
    Returns:
        True if placed
    """
    existing_block = _house_block_id(client, x, y, z)
    if existing_block == "minecraft:torch":
        return True

    # Try to craft torches if none available
    if count_item(client, "minecraft:torch") < 1:
        if count_item(client, "minecraft:stick") >= 1 and (count_item(client, "minecraft:coal") >= 1 or count_item(client, "minecraft:charcoal") >= 1):
            craft(client, "minecraft:torch", 4)
            time.sleep(0.5)
        else:
            print("  Need torches (or coal/charcoal and sticks to craft)")
            return False

    if not is_position_safe(client, x, y, z):
        print(f"  Skipping placement at ({x}, {y}, {z}) - Target is liquid")
        return False

    if not robust_place(client, x, y, z, "minecraft:torch"):
        print("  Place torch failed")
        return False

    time.sleep(0.5)
    return True


def setup_base(
    client,
    location: Optional[Tuple[int, int, int]] = None,
) -> Tuple[bool, Optional[Tuple[int, int, int]]]:
    """
    Set up a complete base with shelter, storage, and crafting.
    
    Args:
        client: Baritone client
        location: Optional specific location, else auto-find
        
    Returns:
        (success, base_coordinates)
    """
    if location is None:
        location = find_flat_ground(client)
        if location is None:
            return (False, None)
    
    x, y, z = location
    
    print(f"  Setting up base at ({x}, {y}, {z})")
    
    # Place essential blocks inside base area
    placed_crafting = place_crafting_table(
        client,
        x + 1,
        y,
        z + 1,
        True,
    )
    placed_furnace = place_furnace(client, x + 2, y, z + 1)
    placed_chest = place_chest(client, x + 1, y, z + 2)
    
    # Optional: try to place bed
    placed_bed = place_bed(client, x + 2, y, z + 2)
    
    # Place torches for spawn proofing
    place_torch(client, x + 3, y + 1, z + 3) # Interior center
    place_torch(client, x + 3, y + 1, z - 1) # North exterior
    place_torch(client, x + 3, y + 1, z + 7) # South exterior
    place_torch(client, x - 1, y + 1, z + 3) # West exterior
    place_torch(client, x + 7, y + 1, z + 3) # East exterior
    
    if placed_crafting and placed_furnace and placed_chest:
        print("  Base setup complete!")
        return (True, location)
    else:
        print("  Base setup incomplete - missing crafting table, furnace, or chest")
        return (False, location)


def open_crafting_table(client, x: Optional[int] = None, y: Optional[int] = None, z: Optional[int] = None) -> bool:
    """
    Find and open a nearby crafting table.
    If coordinates are provided, it will use those.
    
    Returns:
        True if crafting table opened
    """
    if x is not None and y is not None and z is not None:
        table_pos = (x, y, z)
    else:
        # Find nearby crafting table
        table_pos = find_nearby_block(client, ["minecraft:crafting_table"], radius=20)
    
    if table_pos is None:
        print("  No crafting table nearby")
        return False
    
    tx, ty, tz = table_pos

    # The functional harness verifies both the block and the 46-slot screen,
    # and—critically—uses an adjacent stand tile.  A direct Baritone goal on
    # the solid table block repeatedly mined the starter-house workstation and
    # carried it farther across the room on every manual recipe fallback.
    try:
        from . import harness_ops
        if harness_ops.available() and harness_ops.ensure_crafting_table_open(
            client, table_pos=(int(tx), int(ty), int(tz))
        ):
            return True
    except Exception as exc:
        print(f"  Verified crafting-table open failed: {exc}")
    
    # Native compatibility fallback for installed clients without the harness.
    goto(client, tx, ty, tz, timeout=30, tolerance=2)
    time.sleep(0.3)  # Small delay before interaction
    
    # Look at the block first
    try:
        client.transport.dispatch("look_at", {"x": tx, "y": ty, "z": tz})
        time.sleep(0.2)
    except Exception:
        pass  # look_at might not be implemented
    
    # Interact
    client.transport.dispatch("interact_block", {"x": tx, "y": ty, "z": tz})
    
    # Wait for screen - Fabric uses obfuscated class names, so check for any non-'none' screen
    # since we just interacted with a crafting table, any screen opening is likely it
    print(f"  Waiting for crafting table screen to open...")
    for attempt in range(20):  # Increased attempts
        time.sleep(0.2)
        try:
            state = client.transport.dispatch("get_state", {}, timeout=0.5)
            screen = state.get("screen", "none")
            
            # Check for crafting-related screen names (works with both mapped and obfuscated)
            if screen != "none" and screen != "":
                # In Fabric, crafting screen might be CraftingScreen, class_XXX, or similar
                # Any non-empty screen after clicking a crafting table is likely correct
                if "craft" in screen.lower() or screen.startswith("class_"):
                    print(f"  Detected crafting screen: {screen}")
                    time.sleep(0.3)  # Extra buffer for screen to be ready
                    return True
        except Exception as e:
            print(f"  State check error: {e}")
            continue
    
    print(f"  Timed out waiting for crafting table screen")
    return False


def open_furnace(client) -> bool:
    """
    Find and open a nearby furnace.
    
    Returns:
        True if furnace opened
    """
    furnace_pos = find_nearby_block(client, ["minecraft:furnace", "minecraft:blast_furnace"], radius=20)
    
    if furnace_pos is None:
        print("  No furnace nearby")
        return False
    
    x, y, z = furnace_pos
    
    goto(client, x, y, z, timeout=30, tolerance=2)
    
    client.transport.dispatch("interact_block", {"x": x, "y": y, "z": z})
    time.sleep(0.5)
    
    return True


def sleep_through_night(client, timeout: int = 30) -> bool:
    """
    Attempt to place a bed, sleep, and then retrieve the bed.
    Checks time/weather to see if sleep is possible/needed.
    """
    try:
        # Check time/weather
        state = client.transport.dispatch("get_state", {})
        world_time = state.get("world_time", 0)
        is_raining = state.get("is_raining", False)
        
        # Minecraft Night is roughly 13000 to 23000
        is_night = (world_time % 24000) >= 12542
        
        if not (is_night or is_raining):
            return True # Not needed
            
        print(f"Night/Rain detected (time={world_time%24000}). Attempting to sleep...")
        
        # Check for bed
        bed_types = [
            "minecraft:white_bed", "minecraft:red_bed", "minecraft:blue_bed",
            "minecraft:green_bed", "minecraft:black_bed", "minecraft:yellow_bed",
            "minecraft:orange_bed", "minecraft:pink_bed", "minecraft:purple_bed",
            # Add others if needed
        ]
        has_bed = False
        for bed in bed_types:
            if count_item(client, bed) > 0:
                has_bed = True
                break
        
        if not has_bed:
            print("No bed to sleep in!")
            return False
            
        # Find flat spot
        pos = find_flat_ground(client, radius=10)
        if not pos:
            # Try current pos
            pos = get_player_pos(client)
            if not pos:
                return False
            x, y, z = int(pos[0]), int(pos[1]), int(pos[2])
            pos = (x, y, z)
            
        x, y, z = pos
        # Place bed slightly offset to avoid suffocating or overlapping
        # x+1, z+1
        bx, by, bz = x+1, y, z+1 # Simple offset
        
        print(f"Placing bed at {bx}, {by}, {bz}")
        # place_bed selects the bed item and places it - no raw place_block
        # first (that used to place whatever was in hand at the bed spot).
        if not place_bed(client, bx, by, bz):
            print("Failed to place bed")
            return False
            
        # Sleep
        print("Sleeping...")
        client.transport.dispatch("interact_block", {"x": bx, "y": by, "z": bz})
        
        # Wait for morning
        # We can wait for time to change or just fixed delay
        time.sleep(10) 
        
        # Verify it's morning? 
        state = client.transport.dispatch("get_state", {})
        new_time = state.get("world_time", 0)
        if (new_time % 24000) < 1000:
            print("Woke up! Morning.")
        
        # Retrieve bed
        print("Mining bed to retrieve it...")
        client.transport.dispatch("mine", {"blocks": bed_types, "quantity": 1})
        time.sleep(3) # Wait for mine
        client.transport.dispatch("cancel", {})
        
        # Pickup item? (Auto-pickup usually works if close)
        return True
        
    except Exception as e:
        print(f"Sleep error: {e}")
        return False


def build_compact_night_shelter(client) -> bool:
    """Enclose the player with owned blocks without digging or using cheats."""
    from . import harness_ops

    cobble = count_item(client, "minecraft:cobblestone")
    dirt = count_item(client, "minecraft:dirt")
    material = "minecraft:cobblestone" if cobble >= 10 else "minecraft:dirt" if dirt >= 10 else None
    if material is None or not harness_ops.available():
        return False

    try:
        state = client.transport.dispatch("get_state", {})
        pos = state.get("block_position", state.get("position", {}))
        x, y, z = int(pos["x"]), int(pos["y"]), int(pos["z"])
    except Exception as exc:
        print(f"Night shelter: cannot determine player position: {exc}")
        return False

    side_columns = [
        ((x + 1, y, z), (x + 1, y + 1, z)),
        ((x - 1, y, z), (x - 1, y + 1, z)),
        ((x, y, z + 1), (x, y + 1, z + 1)),
        ((x, y, z - 1), (x, y + 1, z - 1)),
    ]
    roof = (x, y + 2, z)

    def _block_at(target) -> str:
        return client.transport.dispatch(
            "get_block",
            {"x": target[0], "y": target[1], "z": target[2]},
        ).get("id", "")

    def _is_wall(block_id: str) -> bool:
        if block_id == "minecraft:grass_block":
            return True
        return bool(block_id) and not any(
            token in block_id for token in ("air", "water", "lava", "flower", "grass", "fern")
        )

    def _interrupt_for_threat() -> bool:
        from .combat import defend_or_flee, scan_for_threats
        threats = scan_for_threats(client, radius=12)
        if not threats or threats[0].get("distance", 999) > 10:
            return False
        print("Night shelter: hostile approached; interrupting placement")
        defend_or_flee(client)
        return True

    print(f"Night shelter: enclosing player at {(x, y, z)} with {material}...")
    # Uneven terrain and treetops can leave every adjacent wall column hanging
    # over air. Build outward from the block beneath the player first so each
    # wall has a legal support face. Existing natural solid blocks are kept.
    center_support = (x, y - 1, z)
    if not _is_wall(_block_at(center_support)):
        try:
            harness_ops.place_block_exact(
                client, *center_support, material, allow_break=False
            )
        except Exception as exc:
            print(f"Night shelter: center support failed at {center_support}: {exc}")
    for column in side_columns:
        base_target = column[0]
        foundation = (base_target[0], y - 1, base_target[2])
        if _is_wall(_block_at(foundation)):
            continue
        if _interrupt_for_threat():
            return False
        try:
            harness_ops.place_block_exact(
                client, *foundation, material, allow_break=False
            )
        except Exception as exc:
            print(f"Night shelter: foundation failed at {foundation}: {exc}")

    for column in side_columns:
        for target in column:
            # Exact block placement is comparatively slow.  Abort immediately
            # if a hostile enters striking range instead of continuing to
            # build while the player takes damage.
            if _interrupt_for_threat():
                return False
            if _is_wall(_block_at(target)):
                continue
            try:
                harness_ops.place_block_exact(
                    client,
                    target[0],
                    target[1],
                    target[2],
                    material,
                    allow_break=False,
                )
            except Exception as exc:
                print(f"Night shelter: placement failed at {target}: {exc}")

    # The roof center is two blocks above the player and initially has no
    # adjacent face to place against.  Extend one completed wall column by a
    # block first, giving the center roof block a legitimate horizontal
    # support face.
    if not _is_wall(_block_at(roof)):
        roof_supports = [
            (x + 1, y + 2, z),
            (x - 1, y + 2, z),
            (x, y + 2, z + 1),
            (x, y + 2, z - 1),
        ]
        for support in roof_supports:
            below = (support[0], support[1] - 1, support[2])
            if not _is_wall(_block_at(below)):
                continue
            if not _is_wall(_block_at(support)):
                if _interrupt_for_threat():
                    return False
                try:
                    harness_ops.place_block_exact(
                        client,
                        support[0],
                        support[1],
                        support[2],
                        material,
                        allow_break=False,
                    )
                except Exception as exc:
                    print(f"Night shelter: roof support failed at {support}: {exc}")
            if _is_wall(_block_at(support)):
                break

    if not _is_wall(_block_at(roof)):
        if _interrupt_for_threat():
            return False
        try:
            harness_ops.place_block_exact(
                client,
                roof[0],
                roof[1],
                roof[2],
                material,
                allow_break=False,
            )
        except Exception as exc:
            print(f"Night shelter: roof placement failed: {exc}")

    complete_sides = sum(
        all(_is_wall(_block_at(target)) for target in column)
        for column in side_columns
    )
    roof_complete = _is_wall(_block_at(roof))
    # A missing side is not survivable once hostiles can spawn: it is an open
    # doorway a mob can walk through while the caller stops watching for
    # threats. Confirmed by a live Bot09 death on Easy difficulty from a
    # shelter this call reported as "ready" with one open side.
    sheltered = roof_complete and complete_sides >= 4
    print(
        f"Night shelter: {'ready' if sheltered else 'partial'} "
        f"({complete_sides}/4 sides, roof={roof_complete})."
    )
    return sheltered


def _has_existing_enclosure(client, state=None, radius: int = 4) -> bool:
    """Recognize a roofed room so night waiting does not build inside it."""
    try:
        state = state or client.transport.dispatch("get_state", {})
        pos = state.get("block_position", state.get("position", {}))
        x, y, z = int(pos["x"]), int(pos["y"]), int(pos["z"])
    except Exception:
        return False

    def solid(tx, ty, tz) -> bool:
        block_id = _house_block_id(client, tx, ty, tz)
        return bool(block_id) and not any(
            token in block_id
            for token in (
                "air", "water", "lava", "grass", "flower", "fern",
                "torch", "leaf_litter",
            )
        )

    roofed = any(solid(x, y + dy, z) for dy in range(2, radius + 1))
    if not roofed:
        return False

    enclosed_directions = 0
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        for distance in range(1, radius + 1):
            tx, tz = x + dx * distance, z + dz * distance
            if solid(tx, y, tz) and solid(tx, y + 1, tz):
                enclosed_directions += 1
                break
    return enclosed_directions >= 3


def wait_for_safe_daylight(
    client,
    max_wait: float = 720.0,
    poll_interval: float = 5.0,
) -> bool:
    """Wait in place until daytime, yielding death recovery to the automator.

    This is the no-bed fallback for the first night.  A fresh player has no
    credible defense, so wandering for wood is much less reliable than
    cancelling movement and waiting for dawn at the spawn perch.
    """
    deadline = time.monotonic() + max_wait
    next_status = 0.0
    next_bed_attempt = 0.0
    next_anti_idle = 0.0
    anti_idle_yaw = 0.0
    shelter_attempted = False
    shelter_complete = False

    try:
        client.transport.dispatch("close_screen", {})
        client.transport.dispatch("cancel", {})
    except Exception as exc:
        print(f"Daylight safety: could not clear current action: {exc}")

    while time.monotonic() < deadline:
        try:
            state = client.transport.dispatch("get_state", {})
        except Exception as exc:
            print(f"Daylight safety: state check failed: {exc}")
            time.sleep(poll_interval)
            continue

        if state.get("is_dead", False):
            # Do not respawn here.  The top-level automator must see the death
            # screen so DeathRecoveryAction can capture the pre-death
            # inventory and exact coordinates before the bridge clears them.
            print("Daylight safety: player died; yielding to death recovery")
            return False

        day_time = int(state.get("world_time", 0)) % 24000
        if day_time < 12000:
            print(f"Daylight safety: daylight confirmed (time={day_time}).")
            return True

        if not shelter_attempted:
            if _has_existing_enclosure(client, state):
                shelter_attempted = True
                shelter_complete = True
                print("Daylight safety: existing enclosure verified; waiting inside.")

        now = time.monotonic()
        if (
            shelter_complete
            and day_time >= 12542
            and now >= next_bed_attempt
        ):
            if _sleep_in_nearby_bed(client):
                return True
            next_bed_attempt = now + 30.0

        if not shelter_complete:
            # Only stand still once the shelter is verified complete (4/4
            # walls + roof) -- outside mobs cannot hit the player then, and
            # leaving a truly complete shelter to engage a detected creeper
            # caused a confirmed prior survival death. A partial enclosure
            # (e.g. one open side) is not safe: it must keep fighting/fleeing
            # every poll, not just before the first build attempt, or a mob
            # can walk in through the gap while the bot waits unarmed -- this
            # is exactly how Bot09 died on Easy difficulty.
            from .combat import defend_or_flee, scan_for_threats
            threats = scan_for_threats(client, radius=16)
            if threats and threats[0].get("distance", 999) <= 12:
                print("Daylight safety: hostile nearby; defending before shelter work")
                defend_or_flee(client)
                time.sleep(poll_interval)
                continue
            if not shelter_attempted:
                try:
                    # A partial result is still an attempt. Retrying the same
                    # impossible placement every poll produced thousands of
                    # log lines and consumed the entire night without
                    # improving it, so only the placement call itself is
                    # one-shot; threat scanning above still runs every poll
                    # while shelter_complete is False.
                    shelter_attempted = True
                    shelter_complete = bool(build_compact_night_shelter(client))
                except Exception as exc:
                    print(f"Daylight safety: compact shelter attempt failed: {exc}")

        client.transport.dispatch("cancel", {})
        now = time.monotonic()
        if now >= next_anti_idle:
            # Aternos enforces an idle timeout. A tiny alternating look packet
            # keeps a safely sheltered bot connected without moving it out of
            # its enclosure or starting a competing Baritone process.
            anti_idle_yaw = 1.0 if anti_idle_yaw == 0.0 else 0.0
            try:
                client.transport.dispatch(
                    "look", {"yaw": anti_idle_yaw, "pitch": 0.0}
                )
            except Exception as exc:
                print(f"Daylight safety: anti-idle nudge failed: {exc}")
            next_anti_idle = now + 20.0
        if now >= next_status:
            ticks_until_dawn = 24000 - day_time
            seconds_until_dawn = max(0, ticks_until_dawn // 20)
            print(
                f"Daylight safety: unarmed at night (time={day_time}); "
                f"waiting about {seconds_until_dawn}s for dawn..."
            )
            next_status = now + 30.0
        time.sleep(poll_interval)

    return False


def _sleep_in_nearby_bed(client, timeout: float = 15.0) -> bool:
    """Use an already-placed bed and prove that the world advanced to day."""
    bed_blocks = [
        f"minecraft:{color}_bed"
        for color in (
            "white", "orange", "magenta", "light_blue", "yellow", "lime",
            "pink", "gray", "light_gray", "cyan", "purple", "blue",
            "brown", "green", "red", "black",
        )
    ]
    bed = find_nearby_block(client, bed_blocks, radius=8)
    if bed is None:
        return False

    state = client.transport.dispatch("get_state", {})
    position = state.get("block_position", state.get("position", {}))
    distance = sum(
        (float(position.get(axis, 0)) - float(bed[index])) ** 2
        for index, axis in enumerate(("x", "y", "z"))
    ) ** 0.5
    if distance > 4.5 and not goto(
        client,
        bed[0],
        bed[1],
        bed[2],
        timeout=20,
        tolerance=2.0,
    ):
        return False

    print(f"Daylight safety: sleeping in nearby bed at {bed}...")
    client.transport.dispatch(
        "look_at",
        {"x": bed[0] + 0.5, "y": bed[1] + 0.5, "z": bed[2] + 0.5},
    )
    client.transport.dispatch(
        "interact_block",
        {"x": bed[0], "y": bed[1], "z": bed[2]},
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.5)
        live_state = client.transport.dispatch("get_state", {})
        if int(live_state.get("world_time", 0)) % 24000 < 12000:
            print("Daylight safety: bed sleep advanced the world to daylight.")
            return True
        if live_state.get("is_dead", False):
            return False
    client.transport.dispatch("close_screen", {})
    return False


_BED_BLOCKS = [
    f"minecraft:{color}_bed"
    for color in (
        "white", "orange", "magenta", "light_blue", "yellow", "lime",
        "pink", "gray", "light_gray", "cyan", "purple", "blue",
        "brown", "green", "red", "black",
    )
]


def try_establish_respawn_anchor_now(client, state) -> bool:
    """Non-blocking: sleep in a nearby bed right now if it's already night.

    Returns immediately (no waiting) if the anchor is already set, no bed is
    nearby, or it isn't night yet. Safe to call on every retry of a hot-path
    phase (e.g. FOOD_AND_IRON) without adding delay to attempts that can't
    succeed yet -- the opportunistic check just waits for a night window to
    happen to line up with when the bed is nearby.
    """
    if state is not None and state.custom_data.get("respawn_anchor_established"):
        return True
    if find_nearby_block(client, _BED_BLOCKS, radius=8) is None:
        return False
    try:
        live_state = client.transport.dispatch("get_state", {})
    except Exception:
        return False
    if live_state.get("is_dead", False):
        return False
    day_time = int(live_state.get("world_time", 0)) % 24000
    if day_time < 12500:
        return False
    established = _sleep_in_nearby_bed(client)
    if established and state is not None:
        state.custom_data["respawn_anchor_established"] = True
    return established


def wait_and_establish_respawn_anchor(client, state, timeout: float = 900.0) -> bool:
    """Block until night falls, then sleep in the just-placed starter bed so
    the player's vanilla respawn point anchors near base.

    Sleeping otherwise only happens incidentally during later night waits,
    and FOOD_AND_IRON's deep mining routinely puts the player too far
    underground from this surface bed to ever reach it before dawn. With no
    respawn point ever set, a death anywhere in that phase (or later) sends
    the player back to world spawn instead of near base -- confirmed live:
    Bot09 died repeatedly far from home with an empty inventory, having
    never once slept in its bed. This is the one deliberate blocking wait,
    meant to run once right after the starter bed is placed; every other
    call site should use the non-blocking try_establish_respawn_anchor_now.
    """
    if state is not None and state.custom_data.get("respawn_anchor_established"):
        return True
    if find_nearby_block(client, _BED_BLOCKS, radius=8) is None:
        print("  No bed placed yet; skipping early respawn-anchor sleep.")
        return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            live_state = client.transport.dispatch("get_state", {})
        except Exception:
            time.sleep(5.0)
            continue
        if live_state.get("is_dead", False):
            return False
        day_time = int(live_state.get("world_time", 0)) % 24000
        if day_time >= 12500:
            established = _sleep_in_nearby_bed(client)
            if established and state is not None:
                state.custom_data["respawn_anchor_established"] = True
            return established
        time.sleep(5.0)
    return False


def _standing_in_liquid(client) -> bool:
    """Return True if player is standing in water or lava at feet or head."""
    try:
        pos = get_player_pos(client)
        x, y, z = int(pos[0]), int(pos[1]), int(pos[2])
        feet_id = client.transport.dispatch(
            "get_block", {"x": x, "y": y, "z": z}
        ).get("id", "")
        head_id = client.transport.dispatch(
            "get_block", {"x": x, "y": y + 1, "z": z}
        ).get("id", "")
        for bid in (feet_id, head_id):
            if "water" in bid or "lava" in bid:
                return True
        return False
    except Exception:
        return False


def establish_dry_footing(client, max_lifts: int = 6) -> bool:
    """Push the player out of any water/lava column, lifting up to max_lifts times."""
    if not _standing_in_liquid(client):
        return True
    # choose material (count_item is imported at module top)
    if count_item(client, "minecraft:cobblestone") >= 1:
        material = "minecraft:cobblestone"
    elif count_item(client, "minecraft:dirt") >= 1:
        material = "minecraft:dirt"
    else:
        return False
    for _ in range(max_lifts):
        pos = get_player_pos(client)
        x, y, z = int(pos[0]), int(pos[1]), int(pos[2])
        robust_place(client, x, y - 1, z, material)
        client.transport.dispatch("goto", {"x": x, "y": y + 1, "z": z})
        time.sleep(0.5)
        if not _standing_in_liquid(client):
            return True
    return False


def build_emergency_shelter(client) -> bool:
    """
    Build a quick 1x2x1 hole or dirt hut to survive the night.
    Strategy: Dig 3 blocks down and place a block above head.
    """
    try:
        print("Building EMERGENCY SHELTER!")
        # Ensure we are not sinking in water
        establish_dry_footing(client)
        # 1. Dig down 3 blocks
        pos = get_player_pos(client)
        x, y, z = int(pos[0]), int(pos[1]), int(pos[2])
        
        # Dig under feet
        client.transport.dispatch("mine", {"blocks": ["minecraft:dirt", "minecraft:grass_block", "minecraft:stone"], "quantity": 1})
        time.sleep(2)
        # Drop down? Baritone might resist. simpler is to build UP walls.
        # Let's simple "surround" via blocks.
        
        # New Strategy: 3x3x3 dirt box around player.
        # Needs blocks.
        if count_item(client, "minecraft:dirt") < 20 and count_item(client, "minecraft:cobblestone") < 20:
             print("Not enough blocks for shelter. Digging down...")
             client.transport.dispatch("mine", {"x": x, "y": y-1, "z": z, "quantity": 1})
             time.sleep(1)
             client.transport.dispatch("mine", {"x": x, "y": y-2, "z": z, "quantity": 1})
             time.sleep(1)
             client.transport.dispatch("mine", {"x": x, "y": y-3, "z": z, "quantity": 1})
             time.sleep(1)
             client.transport.dispatch("goto", {"x": x, "y": y-3, "z": z})
             time.sleep(2)
             # Cover top
             from .automation_utils import place_block as select_and_place
             if not select_and_place(client, x, y, z, "minecraft:cobblestone"):
                 return False
             return True
             
        # Build box
        return build_dirt_shelter(client, x-1, y, z-1, size=1)
        
    except Exception as e:
        print(f"Emergency shelter failed: {e}")
        return False


_ALL_PLANKS = [
    "minecraft:oak_planks", "minecraft:spruce_planks", "minecraft:birch_planks",
    "minecraft:jungle_planks", "minecraft:acacia_planks", "minecraft:dark_oak_planks",
    "minecraft:mangrove_planks", "minecraft:cherry_planks", "minecraft:bamboo_planks",
]

_ALL_DOORS = [
    "minecraft:oak_door", "minecraft:spruce_door", "minecraft:birch_door",
    "minecraft:jungle_door", "minecraft:acacia_door", "minecraft:dark_oak_door",
    "minecraft:crimson_door", "minecraft:warped_door",
]


def wait_for_chunk_loaded(client, x: int, y: int, z: int, timeout: float = 10.0) -> bool:
    """Poll a coordinate until it stops reporting void_air (unloaded chunk).

    Right after joining/reconnecting, a chunk the player is already standing
    in can still report "minecraft:void_air" for a brief window before the
    server streams it in. Treating that as "block missing" produces false
    structural-integrity failures -- confirmed live: right after a
    reconnect, Bot07 surveyed its own intact starter house as 0/49 floor
    blocks, started a from-scratch rebuild with zero tools, and died
    gathering wood for it. Returns True once a real block id is seen (or the
    timeout elapses -- callers still proceed with whatever the last read
    was, matching prior behavior when this call did not exist).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            block_id = client.transport.dispatch(
                "get_block", {"x": int(x), "y": int(y), "z": int(z)}
            ).get("id", "")
        except Exception:
            block_id = ""
        if block_id != "minecraft:void_air":
            return True
        time.sleep(0.5)
    return False


def _house_block_id(client, x: int, y: int, z: int) -> str:
    try:
        return client.transport.dispatch(
            "get_block", {"x": int(x), "y": int(y), "z": int(z)}
        ).get("id", "")
    except Exception:
        return ""


def _clear_wrong_house_target(
    client, x: int, y: int, z: int, requested_block: str
) -> bool:
    """Clear exactly one wrong occupied structure target under a build guard."""
    current = _house_block_id(client, x, y, z)
    air_blocks = {
        "",
        "minecraft:air",
        "minecraft:cave_air",
        "minecraft:void_air",
    }
    if current in air_blocks or current == requested_block:
        return True
    client.transport.dispatch("chat", {"message": "#set allowBreak true"})
    try:
        client.transport.dispatch(
            "break_block", {"x": int(x), "y": int(y), "z": int(z)}
        )
        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            if _house_block_id(client, x, y, z) in air_blocks:
                return True
            time.sleep(0.2)
        print(
            "Good house repair blocked: exact wrong target "
            f"{(x, y, z)} remained {current}"
        )
        return False
    finally:
        client.transport.dispatch("cancel", {})
        client.transport.dispatch("chat", {"message": "#set allowBreak false"})


def _house_door_aligned(client, x: int, y: int, z: int) -> bool:
    """Verify that a north-wall door blocks the north/south passage."""
    try:
        block = client.transport.dispatch(
            "get_block", {"x": int(x), "y": int(y), "z": int(z)}
        )
    except Exception:
        return False
    if block.get("id", "") not in _ALL_DOORS:
        return False
    facing = str(block.get("state", {}).get("facing", "")).lower()
    # Older/fake bridges expose only the id.  When orientation is available,
    # reject east/west doors: opening one rotates its collision panel across
    # this house's one-block north/south doorway.
    return not facing or facing in ("north", "south")


def _ensure_door_support(client, x: int, y: int, z: int) -> bool:
    """Repair a missing floor block immediately below a recovered doorway."""
    support_y = y - 1
    support = _house_block_id(client, x, support_y, z)
    unsupported_tokens = ("air", "water", "lava", "seagrass", "kelp")
    if support and not any(token in support for token in unsupported_tokens):
        return True

    material = first_available_item(
        client,
        (
            "minecraft:cobblestone",
            "minecraft:cobbled_deepslate",
            *_ALL_PLANKS,
            "minecraft:dirt",
        ),
    )
    if material is None:
        print("Door placement blocked: no material available for doorway support")
        return False
    if not robust_place(client, x, support_y, z, material):
        print("Door placement blocked: doorway support could not be repaired")
        return False
    repaired = _house_block_id(client, x, support_y, z)
    return bool(repaired) and not any(
        token in repaired for token in unsupported_tokens
    )


def _place_north_wall_door(client, x: int, y: int, z: int, item_id: str) -> bool:
    """Place a door from outside the north wall with a north/south facing."""
    from .door_recovery import move_to_door_staging

    client.transport.dispatch("close_screen", {})
    client.transport.dispatch("chat", {"message": "#set allowBreak false"})
    try:
        if not move_to_door_staging(client, x, y, z, timeout=20.0):
            print("Door placement blocked: no reachable staging position")
            return False
        client.transport.dispatch("cancel", {})

        # A recovered or overlapping build can leave planks in one or both
        # doorway cells. Minecraft rejects door placement as "target already
        # occupied", so clear the exact 1x2 opening before selecting the door.
        air_blocks = {"", "minecraft:air", "minecraft:cave_air", "minecraft:void_air"}
        client.transport.dispatch("chat", {"message": "#set allowBreak true"})
        for tool_id in (
            "minecraft:netherite_axe",
            "minecraft:diamond_axe",
            "minecraft:iron_axe",
            "minecraft:stone_axe",
            "minecraft:wooden_axe",
        ):
            if select_item(client, tool_id, allow_swap=True):
                break
        for clear_y in (y, y + 1):
            block_id = _house_block_id(client, x, clear_y, z)
            if block_id in _ALL_DOORS or block_id in air_blocks:
                continue
            client.transport.dispatch(
                "break_block", {"x": int(x), "y": int(clear_y), "z": int(z)}
            )
            clear_deadline = time.monotonic() + 12.0
            while time.monotonic() < clear_deadline:
                if _house_block_id(client, x, clear_y, z) in air_blocks:
                    break
                time.sleep(0.2)
            client.transport.dispatch("cancel", {})
        doorway_clear = all(
            _house_block_id(client, x, clear_y, z) in air_blocks
            for clear_y in (y, y + 1)
        )
        client.transport.dispatch("chat", {"message": "#set allowBreak false"})
        if not doorway_clear:
            print("Door placement blocked: doorway cells could not be cleared")
            return False

        if not _ensure_door_support(client, x, y, z):
            return False

        if not select_item(client, item_id, allow_swap=True):
            return False

        # Minecraft derives door orientation from player yaw, not the clicked
        # face.  Yaw 0 faces south, straight into this north-wall doorway.
        client.transport.dispatch("look", {"yaw": 0.0, "pitch": 35.0})
        client.transport.dispatch(
            "place_block",
            {"x": int(x), "y": int(y), "z": int(z), "block": item_id},
        )
        for _ in range(30):
            if _house_door_aligned(client, x, y, z):
                return True
            time.sleep(0.1)
        return False
    except Exception as exc:
        print(f"Door placement failed: {exc}")
        return False
    finally:
        client.transport.dispatch("cancel", {})
        client.transport.dispatch("chat", {"message": "#set allowBreak true"})


def _good_house_plan(x: int, y: int, z: int, size: int = 7, height: int = 4):
    """Return the exact starter-house targets as (x, y, z, role)."""
    targets = []
    for dx in range(size):
        for dz in range(size):
            targets.append((x + dx, y, z + dz, "floor"))

    door_x, door_z = x + size // 2, z
    for dy in range(1, height):
        for dx in range(size):
            if not (x + dx == door_x and dy in (1, 2)):
                targets.append((x + dx, y + dy, z, "shell"))
            targets.append((x + dx, y + dy, z + size - 1, "shell"))
        for dz in range(1, size - 1):
            targets.append((x, y + dy, z + dz, "shell"))
            targets.append((x + size - 1, y + dy, z + dz, "shell"))

    for dx in range(size):
        for dz in range(size):
            targets.append((x + dx, y + height, z + dz, "roof"))
    return targets


def _matches_house_role(block_id: str, role: str) -> bool:
    if role == "floor":
        return block_id == "minecraft:cobblestone"
    if role == "shell" and block_id == "minecraft:cobblestone":
        # A solid cobblestone patch is a valid defensive wall.  Replacing it
        # only for appearance can consume the eight planks reserved for the
        # supply chest and deadlock an otherwise complete base.
        return True
    return block_id in _ALL_PLANKS


def _wait_for_build_window(client, latest_start: int = 9000) -> bool:
    """Do not begin a slow exposed build stage close to sunset."""
    try:
        day_time = int(client.transport.dispatch("get_state", {}).get("world_time", 0)) % 24000
    except Exception:
        return False
    if day_time < latest_start:
        return True

    print(
        f"Good house: time={day_time} is too late for another exposed stage; "
        "waiting for the next safe morning..."
    )
    deadline = time.monotonic() + 900.0
    while time.monotonic() < deadline:
        try:
            day_time = int(client.transport.dispatch("get_state", {}).get("world_time", 0)) % 24000
        except Exception:
            time.sleep(5.0)
            continue
        if day_time < latest_start:
            return True
        if day_time >= 12000:
            remaining = max(30.0, deadline - time.monotonic())
            return wait_for_safe_daylight(client, max_wait=remaining)
        time.sleep(5.0)
    return False


def build_good_house(client, x: int, y: int, z: int) -> bool:
    """
    Build a starter house: cobble floor, plank walls, plank roof, a door.
    Size: 7x7 outer dimensions, 4 high. Uses whatever plank type is in
    inventory. Placement failures are tolerated per-block; the build counts
    as successful when the large majority of blocks verifiably landed.
    """
    try:
        size = 7
        height = 4
        plan = _good_house_plan(x, y, z, size=size, height=height)
        door_x, door_z = x + size // 2, z

        def plank_material():
            return first_available_item(client, _ALL_PLANKS)

        # Inspect the world first.  A stopped/retried build must repair the
        # same shell instead of demanding a second full structure's materials.
        missing_floor = []
        missing_shell = []
        for tx, ty, tz, role in plan:
            if _matches_house_role(_house_block_id(client, tx, ty, tz), role):
                continue
            if role == "floor":
                missing_floor.append((tx, ty, tz, role))
            else:
                missing_shell.append((tx, ty, tz, role))

        door_present = _house_door_aligned(client, door_x, y + 1, door_z)
        existing_door = _house_block_id(client, door_x, y + 1, door_z)
        if existing_door in _ALL_DOORS and not door_present:
            print("Good house: removing misaligned doorway block for replacement")
            client.transport.dispatch(
                "look_at", {"x": door_x + 0.5, "y": y + 1.5, "z": door_z + 0.5}
            )
            client.transport.dispatch(
                "break_block", {"x": door_x, "y": y + 1, "z": door_z}
            )
            for _ in range(30):
                if _house_block_id(client, door_x, y + 1, door_z) not in _ALL_DOORS:
                    break
                time.sleep(0.1)
            time.sleep(0.5)
        print(
            "Good house survey: "
            f"floor {49 - len(missing_floor)}/49, "
            f"shell {119 - len(missing_shell)}/119, "
            f"door={'yes' if door_present else 'no'}"
        )

        # A retry can begin with the player standing inside the shell or on
        # its roof.  If gathering starts from there while Baritone may break
        # blocks, it can tunnel through the structure we are trying to repair.
        # Walk out through the north doorway first with breaking disabled.
        shell_target_count = sum(1 for *_coords, role in plan if role != "floor")
        existing_shell_blocks = shell_target_count - len(missing_shell)
        repair_in_progress = existing_shell_blocks and (
            missing_floor or missing_shell or not door_present
        )
        if repair_in_progress:
            print("Good house repair: staging safely outside before gathering...")
            client.transport.dispatch("chat", {"message": "#set allowBreak false"})
            try:
                from . import harness_ops

                if not harness_ops.move_near(
                    client, door_x, y + 1, door_z - 2, timeout=20.0
                ):
                    # Reconnects can leave the player below a partially built
                    # floor.  A no-break route then has no legal exit and the
                    # house retries forever.  Permit one bounded escape route;
                    # final house verification will repair any block Baritone
                    # had to remove.
                    print("Good house repair: no-break exit failed; carving a bounded escape")
                    client.transport.dispatch("chat", {"message": "#set allowBreak true"})
                    if not harness_ops.move_near(
                        client, door_x, y + 1, door_z - 2, timeout=45.0
                    ):
                        print("Good house repair blocked: could not reach the safe exterior")
                        return False
            except Exception as exc:
                print(f"Good house repair exterior staging failed: {exc}")
                return False
            finally:
                client.transport.dispatch("cancel", {})
                client.transport.dispatch("chat", {"message": "#set allowBreak true"})

        # Material acquisition can be longer and more hazardous than the
        # placement batch itself.  Enter the phase-level bounded food recovery
        # before mining stone or gathering wood, not only after a placement
        # eventually notices the depleted margin.
        if not _has_build_survival_margin(client):
            raise _BuildSurvivalHold(
                "health or hunger is below the material-gathering margin"
            )

        # Keep enough in hand for the missing shell plus a table, door, and
        # chest.  Cobble reserve covers the furnace after floor repair.
        required_planks = len(missing_shell) + (18 if not door_present else 8)
        required_cobblestone = len(missing_floor) + 8

        # Material check (any plank type counts)
        total_planks = sum(count_item(client, p) for p in _ALL_PLANKS)
        if total_planks < required_planks:
            if not _prepare_house_planks(client, required_planks):
                return False
            total_planks = sum(count_item(client, p) for p in _ALL_PLANKS)
        if total_planks < required_planks:
            print(f"Good house needs {required_planks} planks; only {total_planks} available")
            return False

        available_cobblestone = count_item(client, "minecraft:cobblestone")
        available_deepslate = count_item(client, "minecraft:cobbled_deepslate")
        if available_cobblestone + available_deepslate < required_cobblestone:
            print("Gathering stone for good house...")
            from .resources import gather_stone
            gather_stone(client, count=required_cobblestone)
            available_cobblestone = count_item(client, "minecraft:cobblestone")
            available_deepslate = count_item(client, "minecraft:cobbled_deepslate")
        available_floor_blocks = available_cobblestone + available_deepslate
        if missing_floor and available_floor_blocks <= 0:
            print("Good house has no floor material after gathering")
            return False
        if available_floor_blocks < required_cobblestone:
            print(
                "Good house will place an incremental floor batch with "
                f"{available_floor_blocks} available block(s)."
            )

        # Gathering may have consumed most of the day even though the phase
        # itself began safely.  The floor and shell are slow, exposed placement
        # jobs, so re-establish daylight immediately before construction.
        repair_target_count = len(missing_floor) + len(missing_shell)
        latest_start = 11500 if repair_target_count <= 12 else 9000
        if not wait_for_safe_daylight(client) or not _wait_for_build_window(
            client, latest_start=latest_start
        ):
            print("Good house build blocked: safe daylight was not established")
            return False

        repaired = 0
        failed = 0

        @contextlib.contextmanager
        def structure_guard():
            """Hold allowBreak off for a whole batch of structure placements.

            This guard used to wrap each individual block, so a 49-block floor
            spent 49 settings toggles, 49 cancels and 49 restores talking to
            the controller instead of building. Worse, the per-block cancel
            landed between placements and tore down the approach path the very
            next block had just started, so the batch fought itself. One
            guarded region per floor/wall/roof course keeps the protection --
            Baritone still never mines the structure while placing it -- at a
            fraction of the chatter.
            """
            client.transport.dispatch("chat", {"message": "#set allowBreak false"})
            try:
                yield
            finally:
                client.transport.dispatch("cancel", {})
                client.transport.dispatch("chat", {"message": "#set allowBreak true"})

        def guarded_structure_place(px, py, pz, material):
            """Replace one exact wrong target without opening broad mining.

            The surrounding batch owns an ``allowBreak false`` guard. A
            wrong occupied target therefore has to be cleared explicitly;
            otherwise robust placement can never replace it. Temporarily
            enable breaking for only that coordinate, prove it became air,
            and immediately restore the guard before placement.
            """
            if not _clear_wrong_house_target(client, px, py, pz, material):
                return False
            return robust_place(client, px, py, pz, material)

        def put(px, py, pz, role, material):
            nonlocal repaired, failed
            if _matches_house_role(_house_block_id(client, px, py, pz), role):
                return
            if not _has_build_survival_margin(client):
                raise _BuildSurvivalHold(
                    "health or hunger fell below the construction margin"
                )
            if material is None:
                failed += 1
                return
            if guarded_structure_place(px, py, pz, material):
                repaired += 1
            else:
                failed += 1

        # 1. Floor (Cobble)
        if missing_floor:
            print(f"Repairing Good House Floor ({len(missing_floor)} targets)...")
            floor_material = (
                "minecraft:cobblestone"
                if available_cobblestone > 0
                else "minecraft:cobbled_deepslate"
            )
            # One failed target can consume several bridge calls. Keep each
            # retry below the command/circuit-breaker budget and checkpoint
            # incremental progress before attempting the next batch.
            floor_budget = min(len(missing_floor), available_floor_blocks, 12)
            with structure_guard():
                for tx, ty, tz, role in missing_floor[:floor_budget]:
                    if count_item(client, floor_material) <= 0:
                        alternate = (
                            "minecraft:cobbled_deepslate"
                            if floor_material == "minecraft:cobblestone"
                            else "minecraft:cobblestone"
                        )
                        if count_item(client, alternate) <= 0:
                            break
                        floor_material = alternate
                    put(tx, ty, tz, role, floor_material)
            if floor_budget < len(missing_floor):
                print(
                    "Good house incremental floor batch complete; "
                    "remaining targets will resume next retry."
                )
                return False

        # 2. Walls (Planks) - leave a 1x2 doorway in the north wall
        wall_targets = [target for target in missing_shell if target[3] == "shell"]
        roof_targets = [target for target in missing_shell if target[3] == "roof"]
        if wall_targets:
            print(f"Repairing Good House Walls ({len(wall_targets)} targets)...")
            with structure_guard():
                for tx, ty, tz, role in wall_targets:
                    put(tx, ty, tz, role, plank_material())

        # 3. Roof. Recheck the time between slow stages; a brand-new house can
        # take most of a Minecraft day even when it starts just after dawn.
        if roof_targets:
            roof_latest_start = 11500 if len(roof_targets) <= 12 else 9000
            if not _wait_for_build_window(client, latest_start=roof_latest_start):
                return False
            print(f"Repairing Roof ({len(roof_targets)} targets)...")
            with structure_guard():
                for tx, ty, tz, role in roof_targets:
                    put(tx, ty, tz, role, plank_material())

        # 4. Door (doorway was left open; no mining needed)
        door_item = first_available_item(client, _ALL_DOORS) if not door_present else None
        if door_item is None:
            plank_counts = sorted(
                ((count_item(client, item_id), item_id) for item_id in _ALL_PLANKS),
                reverse=True,
            )
            plank_count, planks = plank_counts[0] if plank_counts else (0, None)
            if not door_present and planks and plank_count >= 6:
                wood = planks.split(":")[-1].replace("_planks", "")
                if not craft(client, f"minecraft:{wood}_door", 1):
                    craft(client, "minecraft:oak_door", 1)
                door_item = first_available_item(client, _ALL_DOORS)

        door_placed = door_present
        if door_item:
            print("Placing Door...")
            door_placed = _place_north_wall_door(
                client,
                door_x,
                y + 1,
                door_z,
                door_item,
            )
            time.sleep(0.5)
        elif not door_present:
            print("No door available (doorway left open)")

        # Verify the world, not command return values.  This counts blocks
        # retained from previous attempts and catches commands that returned
        # success without putting the requested block at the target.
        floor_correct = 0
        shell_correct = 0
        roof_correct = 0
        for tx, ty, tz, role in plan:
            correct = _matches_house_role(_house_block_id(client, tx, ty, tz), role)
            if role == "floor":
                floor_correct += int(correct)
            elif role == "roof":
                roof_correct += int(correct)
            else:
                shell_correct += int(correct)
        total_correct = floor_correct + shell_correct + roof_correct
        door_placed = _house_door_aligned(client, door_x, y + 1, door_z)
        print(
            f"Good House verified: {total_correct}/{len(plan)} blocks "
            f"(floor={floor_correct}/49 walls={shell_correct}/70 "
            f"roof={roof_correct}/49 repairs={repaired} failed={failed} "
            f"door={'yes' if door_placed else 'no'})"
        )
        # A starter house is a survival boundary, not a cosmetic build.  The
        # old 95% threshold accepted holes in the floor and exterior wall; the
        # phase checkpoint then permanently skipped the repair path.  Require
        # the complete world-verified shell before recording completion.
        return (
            floor_correct == 49
            and shell_correct == 70
            and roof_correct == 49
            and door_placed
        )

    except _BuildSurvivalHold as e:
        print(f"Good house build paused for survival recovery: {e}")
        return False
    except Exception as e:
        print(f"Good house build failed: {e}")
        return False


def open_chest(client, x: int, y: int, z: int) -> bool:
    """Open a chest at specific coordinates."""
    # Look at it first
    client.transport.dispatch("look_at", {"x": x, "y": y, "z": z})
    time.sleep(0.3)
    
    client.transport.dispatch("interact_block", {"x": x, "y": y, "z": z})
    
    # Wait for screen to open
    print(f"  Waiting for chest screen to open...")
    for i in range(15):
        state = client.transport.dispatch("get_state", {})
        screen = state.get("screen", "none")
        # Accept any screen that's not 'none' as it likely means a container opened
        # Fabric obfuscates screen names, so we can't rely on specific names
        if screen != "none":
            print(f"  Detected screen: {screen}")
            time.sleep(0.5)  # Wait a bit for screen to be fully ready
            return True
        time.sleep(0.2)
    
    print(f"  Timed out waiting for chest screen")
    return False


def loot_nearby_chests(client, radius: int = 16) -> bool:
    from .base_looting import loot_nearby_chests as implementation

    return implementation(client, radius)

