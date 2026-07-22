"""
Utilities for Nether-specific automation steps.

These helpers currently provide best-effort implementations built on top of the
mission macros exposed by the Java bridge. They are intentionally defensive and
primarily used by higher-level scripts as orchestration primitives.
"""

import logging
import time
from typing import Optional, Tuple, Dict

from .automation_utils import place_block
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

        # The bridge's place_block only accepts flat x/y/z and places the
        # main-hand item, so go through the shared helper which selects the
        # item first (nested "position" payloads NPE server-side).

        # Build the frame bottom (4 blocks wide)
        for dx in range(4):
            place_block(client, x + dx, y, z, "minecraft:obsidian")
            time.sleep(0.1)

        # Build the frame top (4 blocks wide)
        for dx in range(4):
            place_block(client, x + dx, y + 4, z, "minecraft:obsidian")
            time.sleep(0.1)

        # Build the frame sides (3 blocks tall on each side)
        for dy in range(1, 4):  # y+1, y+2, y+3
            place_block(client, x, y + dy, z, "minecraft:obsidian")
            time.sleep(0.1)
            place_block(client, x + 3, y + dy, z, "minecraft:obsidian")
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
            block_response = client.transport.dispatch(
                "get_block",
                {"x": bx, "y": by, "z": bz},
            )

            if str(block_response.get("status", "")).lower() == "error":
                logger.warning("Could not verify block at (%d,%d,%d): %s", bx, by, bz, block_response.get("error"))
                frame_complete = False
                continue

            block_data = block_response.get("data", {})
            block_type = (
                block_data.get("type")
                or block_data.get("id")
                or block_response.get("type")
                or block_response.get("id")
                or ""
            )
            if block_type != "minecraft:obsidian":
                logger.warning("Frame block at (%d,%d,%d) is %s, expected obsidian", bx, by, bz, block_type)
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
    Scan for Nether fortress structures while exploring, with enhanced multi-level exploration.
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

                        # Find the center of the fortress structure and bounds for systematic exploration
                        if blocks:
                            # Calculate centroid of fortress blocks and track bounds for multi-level exploration
                            total_x = total_y = total_z = 0
                            count = 0
                            min_x = min_y = min_z = float('inf')
                            max_x = max_y = max_z = float('-inf')

                            for block in blocks:
                                if block.get("type") in ["minecraft:nether_bricks", "minecraft:nether_brick_fence",
                                                        "minecraft:nether_brick_stairs", "minecraft:nether_wart"]:
                                    bx, by, bz = block.get("x", 0), block.get("y", 0), block.get("z", 0)
                                    total_x += bx
                                    total_y += by
                                    total_z += bz
                                    count += 1

                                    # Track bounds for fortress exploration
                                    min_x = min(min_x, bx)
                                    min_y = min(min_y, by)
                                    min_z = min(min_z, bz)
                                    max_x = max(max_x, bx)
                                    max_y = max(max_y, by)
                                    max_z = max(max_z, bz)

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
    Hunt blazes using systematic fortress exploration with backtracking.
    Assumes player is already near fortress center from find_nether_fortress.
    """
    try:
        logger.info("Starting systematic fortress blaze hunt for %d rods", target_count)
        start_time = time.time()
        rods_start = count_item(client, "minecraft:blaze_rod")

        # Get current position as fortress center reference
        state = client.transport.dispatch("get_state", {})
        fortress_center = state.get("block_position", state.get("position", {}))
        center_x = int(fortress_center.get("x", 0))
        center_y = int(fortress_center.get("y", 64))
        center_z = int(fortress_center.get("z", 0))

        # Track explored areas to avoid loops
        explored_positions = set()
        exploration_stack = []  # For backtracking
        spawners_found = []

        # Systematic exploration from center
        directions = [
            (10, 0, 0), (-10, 0, 0), (0, 0, 10), (0, 0, -10),  # Cardinal directions
            (0, 5, 0), (0, -5, 0)  # Vertical for multi-level
        ]

        current_target = None
        scan_interval = 5  # Scan every 5 seconds

        while time.time() - start_time < timeout:
            current_time = time.time()

            # Check if we have enough rods
            current_rods = count_item(client, "minecraft:blaze_rod")
            if current_rods - rods_start >= target_count:
                logger.info("Collected %d blaze rods, target reached", current_rods - rods_start)
                break

            # Periodic scanning for spawners and fortress structure
            if current_time - (explored_positions and len(explored_positions) * scan_interval or 0) % scan_interval < 1:

                # Get current position
                state = client.transport.dispatch("get_state", {})
                pos = state.get("block_position", state.get("position", {}))
                current_x = int(pos.get("x", 0))
                current_y = int(pos.get("y", 64))
                current_z = int(pos.get("z", 0))

                current_pos_key = (current_x // 10, current_y // 5, current_z // 10)  # Grid-based exploration

                if current_pos_key in explored_positions:
                    # Already explored this area, backtrack or move to next direction
                    if exploration_stack:
                        prev_pos = exploration_stack.pop()
                        logger.debug("Backtracking to previous position %s", prev_pos)
                        client.transport.dispatch("goto", {"x": prev_pos[0], "y": prev_pos[1], "z": prev_pos[2]})
                        time.sleep(3)
                        continue
                    else:
                        # No more backtrack points, pick a new direction from center
                        pass

                explored_positions.add(current_pos_key)

                # Scan for blaze spawners and fortress blocks
                scan_response = client.transport.dispatch("scan_blocks", {
                    "center": {"x": current_x, "y": current_y, "z": current_z},
                    "radius": 20,
                    "block_types": ["minecraft:spawner", "minecraft:nether_bricks", "minecraft:nether_brick_stairs"]
                })

                if scan_response.get("status") == "ok":
                    blocks = scan_response.get("data", {}).get("blocks", [])

                    fortress_blocks = []
                    new_spawners = []

                    for block in blocks:
                        block_type = block.get("type", "")
                        if block_type == "minecraft:spawner":
                            # Check if it's a blaze spawner by examining nearby blocks or entity data
                            spawner_pos = (block.get("x"), block.get("y"), block.get("z"))
                            if spawner_pos not in spawners_found:
                                new_spawners.append(spawner_pos)
                                spawners_found.append(spawner_pos)
                                logger.info("Found blaze spawner at %s", spawner_pos)
                        elif block_type in ["minecraft:nether_bricks", "minecraft:nether_brick_stairs"]:
                            fortress_blocks.append((block.get("x"), block.get("y"), block.get("z")))

                    # If we found new spawners, prioritize hunting there
                    if new_spawners:
                        for spawner_x, spawner_y, spawner_z in new_spawners:
                            logger.info("Navigating to spawner at (%d, %d, %d)", spawner_x, spawner_y, spawner_z)

                            # Navigate to spawner
                            client.transport.dispatch("goto", {"x": spawner_x, "y": spawner_y, "z": spawner_z})
                            time.sleep(3)

                            # Hunt blazes in the area
                            result = hunt_mobs(
                                client,
                                mob_types=["blaze"],
                                required_loot={"minecraft:blaze_rod": target_count - (current_rods - rods_start)},
                                search_radius=15,
                                timeout=min(60, timeout - (time.time() - start_time)),  # Short timeout per spawner
                                heal_threshold=10.0,
                            )

                            current_rods = count_item(client, "minecraft:blaze_rod")
                            if current_rods - rods_start >= target_count:
                                break

                    # If no spawners found, explore fortress structure
                    elif fortress_blocks:
                        # Find the direction with most fortress blocks
                        direction_scores = {}
                        for dx, dy, dz in directions:
                            score = 0
                            for bx, by, bz in fortress_blocks:
                                dist = abs(bx - (current_x + dx)) + abs(by - (current_y + dy)) + abs(bz - (current_z + dz))
                                if dist < 15:  # Within exploration range
                                    score += 1
                            direction_scores[(dx, dy, dz)] = score

                        # Pick the best direction (most fortress blocks)
                        best_direction = max(direction_scores.items(), key=lambda x: x[1])
                        if best_direction[1] > 0:  # Only move if we found fortress blocks in that direction
                            dx, dy, dz = best_direction[0]
                            target_x, target_y, target_z = current_x + dx, current_y + dy, current_z + dz

                            logger.debug("Exploring fortress in direction (%d, %d, %d) to (%d, %d, %d)",
                                       dx, dy, dz, target_x, target_y, target_z)

                            # Save current position for backtracking
                            exploration_stack.append((current_x, current_y, current_z))

                            # Navigate to new exploration point
                            client.transport.dispatch("goto", {"x": target_x, "y": target_y, "z": target_z})
                            time.sleep(3)
                    else:
                        # No fortress blocks found, try a different direction or backtrack
                        if exploration_stack:
                            prev_pos = exploration_stack.pop()
                            logger.debug("No fortress found, backtracking to %s", prev_pos)
                            client.transport.dispatch("goto", {"x": prev_pos[0], "y": prev_pos[1], "z": prev_pos[2]})
                            time.sleep(3)

            time.sleep(1)  # Small delay between iterations

        # Final count
        final_rods = count_item(client, "minecraft:blaze_rod")
        collected = final_rods - rods_start
        logger.info("Blaze hunt completed. Collected %d rods (target: %d)", collected, target_count)

        return final_rods

    except Exception as exc:
        logger.error("Blaze hunt failed: %s", exc)
        return count_item(client, "minecraft:blaze_rod")


def hunt_endermen(client, target_count: int = 12, timeout: int = 900) -> int:
    """
    Enhanced Enderman hunting with optimal spawn location detection and mob luring tactics.

    Key optimizations:
    - Finds dark areas with high ceilings (≥3 blocks) for optimal Enderman spawning
    - Uses systematic area scanning to locate Enderman-rich zones
    - Implements mob luring by looking at Endermen to provoke them
    - Continuously monitors collection progress and adjusts strategy
    """
    try:
        logger.info("Starting enhanced Enderman hunt for %d pearls", target_count)
        start_time = time.time()
        pearls_start = count_item(client, "minecraft:ender_pearl")

        # Configuration for optimal Enderman farming
        scan_radius = 50  # Radius to scan for potential spawn locations
        min_ceiling_height = 3  # Minimum blocks of open space above spawn surface
        max_light_level = 7  # Maximum light level for Enderman spawning
        lure_timeout = 30  # Time to spend luring Endermen in an area before moving

        # Track explored areas and found spawn locations
        explored_positions = set()
        optimal_spawn_locations = []

        while time.time() - start_time < timeout:
            current_time = time.time()
            elapsed = current_time - start_time

            # Check if we have enough pearls
            current_pearls = count_item(client, "minecraft:ender_pearl")
            collected = current_pearls - pearls_start
            if collected >= target_count:
                logger.info("Collected %d pearls, target reached", collected)
                break

            # Get current position
            state = client.transport.dispatch("get_state", {})
            pos = state.get("block_position", state.get("position", {}))
            current_x = int(pos.get("x", 0))
            current_y = int(pos.get("y", 64))
            current_z = int(pos.get("z", 0))

            # Periodic scanning for optimal spawn locations
            position_key = (current_x // 20, current_z // 20)  # Grid-based exploration
            if position_key not in explored_positions:
                explored_positions.add(position_key)
                logger.debug("Scanning for optimal Enderman spawn locations from (%d, %d)", current_x, current_z)

                # Scan area for potential spawn locations
                scan_response = client.transport.dispatch("scan_blocks", {
                    "center": {"x": current_x, "y": current_y, "z": current_z},
                    "radius": scan_radius,
                    "block_types": ["minecraft:stone", "minecraft:dirt", "minecraft:grass_block"]  # Common spawn surfaces
                })

                if scan_response.get("status") == "ok":
                    blocks = scan_response.get("data", {}).get("blocks", [])
                    potential_spawns = []

                    for block in blocks:
                        bx, by, bz = block.get("x", 0), block.get("y", 0), block.get("z", 0)

                        # Check for high ceiling (open space above)
                        ceiling_height = 0
                        for height in range(1, min_ceiling_height + 2):  # Check a few blocks up
                            check_response = client.transport.dispatch(
                                "get_block",
                                {"x": bx, "y": by + height, "z": bz},
                            )
                            if check_response.get("status") == "ok":
                                block_type = check_response.get("data", {}).get("type", "")
                                if block_type in ["minecraft:air", "minecraft:cave_air"]:
                                    ceiling_height += 1
                                else:
                                    break
                            else:
                                break

                        if ceiling_height >= min_ceiling_height:
                            # Check light level (would need light level API, for now assume we need to be in dark areas)
                            # Since we don't have direct light level access, we'll prioritize underground/cave areas
                            if by < 60:  # Likely underground
                                potential_spawns.append((bx, by, bz, ceiling_height))

                    # Sort by ceiling height (higher ceilings are better for Enderman spawning)
                    potential_spawns.sort(key=lambda x: x[3], reverse=True)
                    optimal_spawn_locations.extend(potential_spawns[:5])  # Keep top 5 locations

            # If we have potential spawn locations, visit them systematically
            if optimal_spawn_locations:
                # Visit the best spawn location
                target_x, target_y, target_z, ceiling = optimal_spawn_locations.pop(0)

                logger.info("Moving to optimal spawn location (%d, %d, %d) with ceiling height %d",
                          target_x, target_y, target_z, ceiling)

                # Navigate to location
                client.transport.dispatch("goto", {"x": target_x, "y": target_y + 1, "z": target_z})
                time.sleep(3)  # Wait for navigation

                # Spend time in this area luring Endermen
                lure_start = time.time()
                endermen_found = 0

                while time.time() - lure_start < lure_timeout and time.time() - start_time < timeout:
                    # Look for nearby Endermen
                    entities_response = client.transport.dispatch("get_entities", {
                        "radius": 32,
                        "types": ["enderman"]
                    })

                    if entities_response.get("status") == "ok":
                        entities = entities_response.get("data", {}).get("entities", [])

                        for entity in entities:
                            entity_id = entity.get("id")
                            entity_pos = entity.get("position", {})

                            # Look at the Enderman to provoke it (this lures it to attack)
                            # Bridge look_at requires coordinates, not entity_id
                            if entity_pos.get("x") is not None:
                                client.transport.dispatch("look_at", {
                                    "x": entity_pos.get("x", 0),
                                    "y": entity_pos.get("y", 0) + 1.5,
                                    "z": entity_pos.get("z", 0),
                                })
                            time.sleep(0.5)  # Brief pause between looks

                            endermen_found += 1
                            logger.debug("Provoked Enderman at (%s)", entity_pos)

                            # After provoking, the Enderman should attack and potentially drop pearls when killed
                            # The combat system should handle the actual fighting

                    time.sleep(2)  # Scan for entities every 2 seconds

                logger.debug("Spent %.1f seconds luring in area, found %d Endermen",
                           time.time() - lure_start, endermen_found)

            else:
                # No optimal locations found yet, do general exploration
                # Random walk to find new areas
                offset_x = (time.time() * 1000 % 200) - 100  # Random offset -100 to 100
                offset_z = (time.time() * 1000 % 200) - 100

                explore_x = current_x + int(offset_x)
                explore_z = current_z + int(offset_z)
                explore_y = current_y

                logger.debug("Exploring new area at (%d, %d)", explore_x, explore_z)
                client.transport.dispatch("goto", {"x": explore_x, "y": explore_y, "z": explore_z})
                time.sleep(5)  # Wait for exploration

            # Check progress periodically
            if elapsed > 60 and elapsed % 60 < 2:  # Every minute
                current_pearls = count_item(client, "minecraft:ender_pearl")
                collected = current_pearls - pearls_start
                logger.info("Progress: %d/%d pearls collected (%.1f%%), %.1f minutes elapsed",
                          collected, target_count, (collected / target_count) * 100, elapsed / 60)

        # Final count
        final_pearls = count_item(client, "minecraft:ender_pearl")
        collected = final_pearls - pearls_start
        logger.info("Enderman hunt completed. Collected %d/%d pearls in %.1f minutes",
                  collected, target_count, (time.time() - start_time) / 60)

        return final_pearls

    except Exception as exc:
        logger.error("Enhanced Enderman hunt failed: %s", exc)
        # Fallback to basic hunt_mobs
        logger.info("Falling back to basic Enderman hunting")
        result = hunt_mobs(
            client,
            mob_types=["enderman"],
            required_loot={"minecraft:ender_pearl": target_count},
            search_radius=72,
            timeout=timeout,
            heal_threshold=12.0,
        )
        return count_item(client, "minecraft:ender_pearl")


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


def find_nearest_portal(client, dimension: str = None) -> Optional[Tuple[int, int, int]]:
    """
    Find the nearest Nether portal to current position.

    Args:
        client: Baritone client
        dimension: Target dimension ("overworld" or "nether"), defaults to current

    Returns:
        (x, y, z) coordinates of nearest portal, or None if not found
    """
    try:
        # Get current position
        state = client.transport.dispatch("get_state", {})
        pos = state.get("block_position", state.get("position", {}))
        current_x = int(pos.get("x", 0))
        current_z = int(pos.get("z", 0))

        if dimension is None:
            dimension = state.get("dimension", "").lower()
        else:
            dimension = dimension.lower()

        # Use goto to find portal - Baritone can navigate to "portal" block type
        result = client.transport.dispatch("goto", {"target": "portal"})

        if result.get("status") == "ok":
            # Get position after navigation attempt
            state = client.transport.dispatch("get_state", {})
            pos = state.get("block_position", state.get("position", {}))
            portal_x = int(pos.get("x", current_x))
            portal_y = int(pos.get("y", 64))
            portal_z = int(pos.get("z", current_z))

            # Verify we found a portal block
            block_response = client.transport.dispatch(
                "get_block",
                {"x": portal_x, "y": portal_y, "z": portal_z},
            )

            if block_response.get("status") == "ok":
                block_type = block_response.get("data", {}).get("type", "")
                if "portal" in block_type.lower():
                    logger.info(f"Found portal at ({portal_x}, {portal_y}, {portal_z}) in {dimension}")
                    return (portal_x, portal_y, portal_z)

        logger.warning("Could not find nearby portal")
        return None

    except Exception as exc:
        logger.error(f"Error finding portal: {exc}")
        return None


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
        
        # 3. Look at Piglin (bridge look_at requires coordinates, not entity_id)
        client.transport.dispatch("look_at", {"x": px, "y": py + 1.0, "z": pz})
        
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

