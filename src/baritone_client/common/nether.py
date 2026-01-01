"""
Utilities for Nether-specific automation steps.

These helpers currently provide best-effort implementations built on top of the
mission macros exposed by the Java bridge. They are intentionally defensive and
primarily used by higher-level scripts as orchestration primitives.
"""

import logging
import time
from typing import Optional, Tuple, Dict

from .combat import hunt_mobs, find_entity_by_type
from .inventory import count_item, find_item_slot

logger = logging.getLogger(__name__)


def _wait_for_dimension(client, target: str, timeout: int) -> bool:
    """Poll bridge state until the dimension string contains ``target``."""
    target = target.lower()
    start = time.time()
    while time.time() - start < timeout:
        state = client.transport.dispatch("get_state", {})
        dimension = state.get("dimension", "")
        if target in dimension.lower():
            return True
        time.sleep(2)
    return False


def build_nether_portal(client, x: int, y: int, z: int, obsidian: int = 14) -> bool:
    """
    Build a complete Nether portal frame at the specified coordinates.
    Validates materials and constructs the obsidian frame with proper dimensions.
    """
    try:
        # First validate we have enough obsidian
        response = client.mission.macro("enter_nether", {"obsidian": obsidian})
        result = response.get("result", {})
        ready = result.get("ready", False)
        if not ready:
            logger.warning("Portal materials missing: %s", result)
            return False

        logger.info("Building Nether portal frame at (%d, %d, %d)", x, y, z)

        # Standard Nether portal dimensions: 4x5 obsidian frame with 2x3 interior
        # Portal frame is 4 blocks wide (x), 5 blocks tall (y), 1 block deep (z)
        # Interior is 2x3 (bottom 3 rows, middle 2 columns)

        # Build the frame bottom (4 blocks wide)
        for dx in range(4):
            client.transport.dispatch("place_block", {
                "block": "minecraft:obsidian",
                "position": {"x": x + dx, "y": y, "z": z}
            })
            time.sleep(0.1)

        # Build the frame top (4 blocks wide)
        for dx in range(4):
            client.transport.dispatch("place_block", {
                "block": "minecraft:obsidian",
                "position": {"x": x + dx, "y": y + 4, "z": z}
            })
            time.sleep(0.1)

        # Build the frame sides (3 blocks tall on each side)
        for dy in range(1, 4):  # y+1, y+2, y+3
            # Left side
            client.transport.dispatch("place_block", {
                "block": "minecraft:obsidian",
                "position": {"x": x, "y": y + dy, "z": z}
            })
            time.sleep(0.1)

            # Right side
            client.transport.dispatch("place_block", {
                "block": "minecraft:obsidian",
                "position": {"x": x + 3, "y": y + dy, "z": z}
            })
            time.sleep(0.1)

        # Verify the frame is complete by checking block placement
        frame_complete = True
        blocks_to_check = []

        # Check bottom row
        for dx in range(4):
            blocks_to_check.append((x + dx, y, z))

        # Check top row
        for dx in range(4):
            blocks_to_check.append((x + dx, y + 4, z))

        # Check sides
        for dy in range(1, 4):
            blocks_to_check.append((x, y + dy, z))      # left
            blocks_to_check.append((x + 3, y + dy, z))  # right

        # Verify all frame blocks are obsidian
        for bx, by, bz in blocks_to_check:
            block_response = client.transport.dispatch("get_block", {
                "position": {"x": bx, "y": by, "z": bz}
            })
            if block_response.get("status") == "ok":
                block_type = block_response.get("data", {}).get("type", "")
                if block_type != "minecraft:obsidian":
                    logger.warning("Frame block at (%d,%d,%d) is %s, expected obsidian", bx, by, bz, block_type)
                    frame_complete = False
            else:
                logger.warning("Could not verify block at (%d,%d,%d)", bx, by, bz)
                frame_complete = False

        if frame_complete:
            client.mission.checkpoint("nether_portal_built", f"Portal frame completed at {x},{y},{z}")
            logger.info("Nether portal frame successfully built")
            return True
        else:
            logger.error("Portal frame construction failed - blocks not placed correctly")
            return False

    except Exception as exc:
        logger.error("Portal construction failed: %s", exc)
        return False


def enter_nether_portal(client, timeout: int = 60) -> bool:
    """
    Wait for the player to transition into the Nether. The caller is expected to
    step into an existing portal before invoking this helper.
    """
    return _wait_for_dimension(client, "nether", timeout)


def find_nether_fortress(
    client,
    max_distance: int = 800,
    timeout: int = 600,
) -> Optional[Tuple[int, int, int]]:
    """
    Scan for Nether fortress structures while exploring.
    Uses block scanning to detect fortress characteristic blocks like nether bricks.
    """
    try:
        logger.info("Starting Nether fortress scan with max_distance=%d, timeout=%d", max_distance, timeout)

        state = client.transport.dispatch("get_state", {})
        origin = state.get("block_position", state.get("position", {}))
        start_x = int(origin.get("x", 0))
        start_z = int(origin.get("z", 0))

        # Start exploration
        client.transport.dispatch("explore", {"x": start_x, "z": start_z})

        start_time = time.time()
        scan_interval = 10  # Scan every 10 seconds
        last_scan = 0

        while time.time() - start_time < timeout:
            current_time = time.time()

            # Periodic scanning for fortress structures
            if current_time - last_scan >= scan_interval:
                state = client.transport.dispatch("get_state", {})
                position = state.get("block_position", state.get("position", {}))
                current_x = int(position.get("x", 0))
                current_y = int(position.get("y", 64))  # Nether fortresses are usually around Y=64
                current_z = int(position.get("z", 0))

                # Check distance from start
                distance = ((current_x - start_x) ** 2 + (current_z - start_z) ** 2) ** 0.5
                if distance > max_distance:
                    logger.info("Reached max distance %.1f, stopping exploration", distance)
                    break

                logger.debug("Scanning for fortress at (%d, %d, %d)", current_x, current_y, current_z)

                # Scan a large area around current position for fortress blocks
                fortress_blocks = [
                    "minecraft:nether_bricks",
                    "minecraft:nether_brick_fence",
                    "minecraft:nether_brick_stairs",
                    "minecraft:nether_wart",
                    "minecraft:chest",  # Often found in fortresses
                    "minecraft:spawner"  # Blaze spawners are fortress hallmarks
                ]

                scan_response = client.transport.dispatch("scan_blocks", {
                    "center": {"x": current_x, "y": current_y, "z": current_z},
                    "radius": 50,  # Large scan radius to detect fortress structures
                    "block_types": fortress_blocks
                })

                if scan_response.get("status") == "ok":
                    blocks = scan_response.get("data", {}).get("blocks", [])

                    # Count fortress-specific blocks
                    fortress_block_counts = {}
                    for block in blocks:
                        block_type = block.get("type", "")
                        if block_type in fortress_blocks:
                            fortress_block_counts[block_type] = fortress_block_counts.get(block_type, 0) + 1

                    # Fortress detection criteria:
                    # - Significant number of nether bricks (main building material)
                    # - Presence of nether wart (only grows in fortresses)
                    # - Blaze spawners (unique to fortresses)
                    nether_brick_count = fortress_block_counts.get("minecraft:nether_bricks", 0)
                    nether_wart_count = fortress_block_counts.get("minecraft:nether_wart", 0)
                    spawner_count = fortress_block_counts.get("minecraft:spawner", 0)

                    logger.debug("Found fortress blocks: bricks=%d, wart=%d, spawners=%d",
                               nether_brick_count, nether_wart_count, spawner_count)

                    # Thresholds for fortress detection
                    if (nether_brick_count >= 100 or  # Large concentration of nether bricks
                        nether_wart_count >= 5 or    # Nether wart is fortress-exclusive
                        spawner_count >= 2):         # Multiple spawners indicate fortress

                        # Find the center of the fortress structure
                        if blocks:
                            # Calculate centroid of fortress blocks
                            total_x = total_y = total_z = 0
                            count = 0

                            for block in blocks:
                                if block.get("type") in ["minecraft:nether_bricks", "minecraft:nether_brick_fence",
                                                        "minecraft:nether_brick_stairs", "minecraft:nether_wart"]:
                                    total_x += block.get("x", 0)
                                    total_y += block.get("y", 0)
                                    total_z += block.get("z", 0)
                                    count += 1

                            if count > 0:
                                fortress_x = total_x // count
                                fortress_y = total_y // count
                                fortress_z = total_z // count

                                logger.info("Nether fortress detected at (%d, %d, %d) with %d fortress blocks",
                                          fortress_x, fortress_y, fortress_z, count)

                                client.transport.dispatch("cancel", {})  # Stop exploration
                                return (fortress_x, fortress_y, fortress_z)

                last_scan = current_time

            time.sleep(2)  # Small delay between checks

        # Timeout reached without finding fortress
        client.transport.dispatch("cancel", {})
        logger.info("Fortress scan timed out after %.1f seconds", time.time() - start_time)
        return None

    except Exception as exc:
        logger.error("Fortress scanning failed: %s", exc)
        try:
            client.transport.dispatch("cancel", {})
        except:
            pass
        return None


def hunt_blazes(client, target_count: int = 8, timeout: int = 600) -> int:
    """
    Use the generic mob-hunting helper to obtain blaze rods.
    """
    result = hunt_mobs(
        client,
        mob_types=["blaze"],
        required_loot={"minecraft:blaze_rod": target_count},
        search_radius=64,
        timeout=timeout,
        heal_threshold=10.0,
    )
    rods = count_item(client, "minecraft:blaze_rod")
    if not result.success:
        logger.warning("Blaze hunt incomplete: %s", result.data.get("missing"))
    return rods


def hunt_endermen(client, target_count: int = 12, timeout: int = 900) -> int:
    """
    Hunt endermen for pearls using the shared combat helper.
    """
    result = hunt_mobs(
        client,
        mob_types=["enderman"],
        required_loot={"minecraft:ender_pearl": target_count},
        search_radius=72,
        timeout=timeout,
        heal_threshold=12.0,
    )
    pearls = count_item(client, "minecraft:ender_pearl")
    if not result.success:
        logger.warning("Enderman hunt incomplete: %s", result.data.get("missing"))
    return pearls


def craft_eyes_of_ender(client, required: int = 12) -> bool:
    """Delegate to the mission macro that verifies blaze powder + pearls."""
    try:
        response = client.mission.macro("craft_eyes", {"required": required})
        result = response.get("result", {})
        ready = result.get("ready", False)
        if ready:
            client.mission.checkpoint("eyes_ready", "Eyes crafted")
        return ready
    except Exception as exc:
        logger.error("Eye crafting failed: %s", exc)
        return False


def mine_nether_gold(client, count: int = 64, timeout: int = 300) -> bool:
    """
    Mine Nether Gold Ore to collect gold nuggets.
    One ore drops 2-6 nuggets. 9 nuggets = 1 Gold Ingot.
    """
    try:
        # Mine nether gold ores
        client.transport.dispatch("mine", {
            "blocks": ["minecraft:nether_gold_ore"], 
            "quantity": count // 2  # conservative estimate
        })
        
        start = time.time()
        while time.time() - start < timeout:
            total = count_item(client, "minecraft:gold_nugget")
            if total >= count:
                client.transport.dispatch("cancel", {})
                return True
            time.sleep(3)
            
        client.transport.dispatch("cancel", {})
        return False
    except Exception as exc:
        logger.error("Gold mining failed: %s", exc)
        return False


def barter_with_piglins(client, gold_ingots_count: int, timeout: int = 300) -> Dict[str, int]:
    """
    Find Piglins and barter gold ingots.
    Returns dict of gained items.
    """
    start_inventory = client.transport.dispatch("get_inventory", {}).get("inventory", [])
    initial_counts = {} # track gained items

    bartered = 0
    start_time = time.time()
    
    while bartered < gold_ingots_count and time.time() - start_time < timeout:
        # 1. Find Piglin
        piglin = find_entity_by_type(client, ["piglin"], radius=32)
        if not piglin:
            logger.info("No Piglins found nearby to barter with")
            time.sleep(2)
            # Maybe move around?
            continue
            
        # 2. Goto Piglin
        px, py, pz = piglin.get("position", {}).values()
        client.transport.dispatch("goto", {"x": int(px), "y": int(py), "z": int(pz)})
        
        # Wait until close
        time.sleep(2)
        
        # 3. Look at Piglin
        client.transport.dispatch("look_at", {"entity_id": piglin["id"]})
        
        # 4. Drop Gold Ingot
        # Find slot
        slot = find_item_slot(client, "minecraft:gold_ingot")
        if slot is None:
            logger.warning("No gold ingots left to barter")
            break
            
        # Select and drop
        client.transport.dispatch("select_slot", {"slot": slot})
        time.sleep(0.5)
        # Drop one item
        client.transport.dispatch("drop_item", {"count": 1})
        bartered += 1
        
        # 5. Wait for barter (Piglin examines gold for ~6-8 seconds)
        logger.info(f"Bartered ingot {bartered}/{gold_ingots_count}. Waiting for return...")
        time.sleep(8) 
        
        # 6. Collect items (walk forward a bit?)
        # Baritone generic "pickup" might auto-happen if close, but let's ensure we are close
        client.transport.dispatch("goto", {"x": int(px), "y": int(py), "z": int(pz)})
    
    # Calculate gained items
    # (Simplified: just returning what we think we got based on inventory diff if we implemented that, 
    # but for now just return empty dict or implement diff logic properly)
    # Calculate gained items
    end_inventory = client.transport.dispatch("get_inventory", {}).get("inventory", [])
    
    # Simple diff: Check what increased
    # Needs to handle stacking
    
    def agg_inv(inv):
        counts = {}
        for item in inv:
            if not item: continue
            iid = item.get("id", "minecraft:air")
            if iid == "minecraft:air": continue
            counts[iid] = counts.get(iid, 0) + item.get("count", 0)
        return counts
        
    start_counts = agg_inv(start_inventory)
    end_counts = agg_inv(end_inventory)
    
    diff = {}
    for iid, count in end_counts.items():
        start_count = start_counts.get(iid, 0)
        if count > start_count:
            diff[iid] = count - start_count
            
    return diff

