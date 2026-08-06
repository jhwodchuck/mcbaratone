"""Ender pearl acquisition via Enderman hunting.

Split out of :mod:`nether` so the hunt's safety logic has room to live under
the repository size budgets.

This hunt is deliberately provocative -- looking at an Enderman is what aggroes
it -- and it parks the bot in dark, low-altitude cave pockets. Bot07 died three
times in one session here, every time shot by a skeleton sharing those caves,
because the loop had no health check, no hostile defense, never fought what it
provoked, and could not perceive its own death. Keep the defensive tick in
every wait path.
"""

import logging
import time
from typing import Any, Dict

from .combat import (
    defend_or_flee,
    eat_until_hunger,
    ensure_alive,
    heal_if_needed,
    hunt_mobs,
    safe_combat,
)
from .inventory import count_item, get_equipped_armor
from .navigation import goto as _supervised_goto
from .nether import _block_id, _entities, _found_blocks, _world_difficulty
from .tasks import PlayerDeathDetected

logger = logging.getLogger(__name__)


# Enderman hunting deliberately provokes a 40-health mob and parks the bot in
# dark, low-altitude cave pockets. In practice the fatal damage came from the
# unrelated hostiles sharing those caves (skeletons every time), so the hunt
# needs the ordinary defensive reflexes rather than Enderman-specific tactics.
# An unarmored bot takes full damage from every hostile sharing the cave. The
# fleet's 20 deaths on 2026-08-06 were 8 zombies, 7 skeletons, 4 creepers and
# only 1 Enderman, and the defense telemetry read "only 0/4 armor pieces" every
# time. Provoking a 40-health mob while naked cannot be made survivable by any
# amount of combat tuning.
_ENDERMAN_MIN_ARMOR_PIECES = 3
_ENDERMAN_MIN_HEALTH = 12.0
# Natural regeneration stops entirely below 18 food, so a hunt started under it
# cannot heal from the fight it is about to start.
_ENDERMAN_MIN_FOOD = 18
_ENDERMAN_RETREAT_HEALTH = 8.0
# Consecutive visited spawn locations that yield no pearl before the hunt is
# abandoned. Overworld cave Enderman rates are low enough that an unbounded
# hunt burns its whole timeout at 0/12 without this.
_ENDERMAN_BARREN_VISITS = 6
# Travel legs run through the supervised navigator so hostile defense and the
# death check fire every half second while walking, not only between loop
# iterations.
_ENDERMAN_TRAVEL_TIMEOUT = 60


def _enderman_safety_tick(client) -> bool:
    """Run one defensive/recovery poll; return False when the hunt must stop.

    ``defend_or_flee`` is the canonical handler for the skeletons and creepers
    that actually kill a bot standing in a cave, and it raises
    ``PlayerDeathDetected`` so a dead player can never keep hunting.
    """
    defend_or_flee(client)
    heal_if_needed(client, threshold=_ENDERMAN_MIN_HEALTH)
    eat_until_hunger(client, minimum_food=_ENDERMAN_MIN_FOOD)

    state = client.transport.dispatch("get_state", {})
    ensure_alive(client, state)

    armor_pieces = len(get_equipped_armor(client))
    if armor_pieces < _ENDERMAN_MIN_ARMOR_PIECES:
        logger.warning(
            "Pausing Enderman hunt: only %d/4 armor pieces equipped (need %d). "
            "An unarmored bot cannot survive the fight it is about to start.",
            armor_pieces,
            _ENDERMAN_MIN_ARMOR_PIECES,
        )
        return False

    health = float(state.get("health", 20) or 0)
    food = int(state.get("food", 20) or 0)
    if health < _ENDERMAN_MIN_HEALTH or food < _ENDERMAN_MIN_FOOD:
        logger.warning(
            "Pausing Enderman hunt: health %.1f food %d below the %.1f/%d "
            "floor required to survive a provoked Enderman",
            health,
            food,
            _ENDERMAN_MIN_HEALTH,
            _ENDERMAN_MIN_FOOD,
        )
        return False
    return True


def _engage_enderman(client, entity: Dict[str, Any]) -> bool:
    """Provoke one Enderman and then actually fight it.

    Looking at an Enderman is what aggroes it, so the previous provoke-then-
    sleep loop reliably started fights it never joined.
    """
    entity_pos = entity.get("position", {})
    entity_id = entity.get("id")
    if entity_pos.get("x") is None or entity_id is None:
        return False

    client.transport.dispatch(
        "look_at",
        {
            "x": entity_pos.get("x", 0),
            "y": entity_pos.get("y", 0) + 1.5,
            "z": entity_pos.get("z", 0),
        },
    )
    return bool(
        safe_combat(
            client,
            entity_id,
            retreat_health=_ENDERMAN_RETREAT_HEALTH,
            purpose="ender_pearl_hunt",
            source="hunt_endermen",
            target_metadata=entity,
        )
    )


def _scan_spawn_locations(
    client,
    current_x: int,
    current_z: int,
    *,
    scan_radius: int,
    min_ceiling_height: int,
) -> list:
    """Return up to five candidate Enderman spawn spots near the player.

    Candidates need open space overhead (Endermen are three blocks tall) and,
    lacking a light-level route on the bridge, are restricted to low altitudes
    as a proxy for darkness.
    """
    logger.debug(
        "Scanning for optimal Enderman spawn locations from (%d, %d)",
        current_x,
        current_z,
    )
    blocks = _found_blocks(
        client.transport.dispatch(
            "find_blocks",
            {
                "blocks": [
                    "minecraft:stone",
                    "minecraft:dirt",
                    "minecraft:grass_block",
                ],
                "radius": min(scan_radius, 32),
                "limit": 256,
            },
        )
    )
    if not blocks:
        return []

    potential_spawns = []
    for block in blocks:
        bx, by, bz = block.get("x", 0), block.get("y", 0), block.get("z", 0)

        # Check for high ceiling (open space above)
        ceiling_height = 0
        for height in range(1, min_ceiling_height + 2):
            block_type = _block_id(
                client.transport.dispatch(
                    "get_block",
                    {"x": bx, "y": by + height, "z": bz},
                )
            )
            if block_type in ["minecraft:air", "minecraft:cave_air"]:
                ceiling_height += 1
            else:
                break

        if ceiling_height >= min_ceiling_height and by < 60:
            potential_spawns.append((bx, by, bz, ceiling_height))

    # Higher ceilings are better for Enderman spawning.
    potential_spawns.sort(key=lambda spawn: spawn[3], reverse=True)
    return potential_spawns[:5]


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

        # Check the cheap local precondition before any bridge traffic: an
        # already-satisfied target must not start a hunt at all.
        if pearls_start >= target_count:
            logger.info(
                "Pearl target already satisfied: %d/%d", pearls_start, target_count
            )
            return pearls_start

        world_state = client.transport.dispatch("get_state", {})
        if _world_difficulty(client, world_state) == "peaceful":
            logger.error(
                "Cannot hunt Endermen while world difficulty is Peaceful; "
                "hostile spawning is disabled"
            )
            return pearls_start

        # Configuration for optimal Enderman farming
        scan_radius = 50  # Radius to scan for potential spawn locations
        min_ceiling_height = 3  # Minimum blocks of open space above spawn surface
        max_light_level = 7  # Maximum light level for Enderman spawning
        lure_timeout = 30  # Time to spend luring Endermen in an area before moving

        # Track explored areas and found spawn locations
        explored_positions = set()
        optimal_spawn_locations = []
        barren_visits = 0

        while time.time() - start_time < timeout:
            current_time = time.time()
            elapsed = current_time - start_time

            if not _enderman_safety_tick(client):
                break

            # Check if we have enough pearls
            current_pearls = count_item(client, "minecraft:ender_pearl")
            collected = current_pearls - pearls_start
            if current_pearls >= target_count:
                logger.info("Pearl inventory target reached: %d/%d", current_pearls, target_count)
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
                optimal_spawn_locations.extend(
                    _scan_spawn_locations(
                        client,
                        current_x,
                        current_z,
                        scan_radius=scan_radius,
                        min_ceiling_height=min_ceiling_height,
                    )
                )

            # If we have potential spawn locations, visit them systematically
            if optimal_spawn_locations:
                # Visit the best spawn location
                target_x, target_y, target_z, ceiling = optimal_spawn_locations.pop(0)

                logger.info("Moving to optimal spawn location (%d, %d, %d) with ceiling height %d",
                          target_x, target_y, target_z, ceiling)

                # Navigate under supervision. A raw goto plus a fixed sleep
                # leaves the whole travel leg undefended, and that is where
                # Bot07 actually died: 18 damage taken between two loop
                # iterations while walking to a spawn pocket.
                _supervised_goto(
                    client,
                    target_x,
                    target_y + 1,
                    target_z,
                    timeout=_ENDERMAN_TRAVEL_TIMEOUT,
                )

                # Spend time in this area engaging Endermen
                lure_start = time.time()
                endermen_found = 0
                pearls_before_visit = count_item(client, "minecraft:ender_pearl")

                while time.time() - lure_start < lure_timeout and time.time() - start_time < timeout:
                    if not _enderman_safety_tick(client):
                        break

                    # Look for nearby Endermen
                    entities_response = client.transport.dispatch("get_entities", {
                        "radius": 32,
                        "types": ["enderman"]
                    })

                    entities = _entities(entities_response)

                    for entity in entities:
                        endermen_found += 1
                        # Provoke and fight one at a time; aggroing every
                        # Enderman in range at once was unwinnable.
                        _engage_enderman(client, entity)
                        if not _enderman_safety_tick(client):
                            break
                        if count_item(client, "minecraft:ender_pearl") >= target_count:
                            break

                    time.sleep(2)  # Scan for entities every 2 seconds

                logger.debug("Spent %.1f seconds engaging in area, found %d Endermen",
                           time.time() - lure_start, endermen_found)

                if count_item(client, "minecraft:ender_pearl") > pearls_before_visit:
                    barren_visits = 0
                else:
                    barren_visits += 1
                    if barren_visits >= _ENDERMAN_BARREN_VISITS:
                        logger.warning(
                            "Abandoning Enderman hunt: %d consecutive spawn "
                            "locations yielded no pearl. This location is not "
                            "producing Endermen.",
                            barren_visits,
                        )
                        break

            else:
                # No optimal locations found yet, do general exploration
                # Random walk to find new areas
                offset_x = (time.time() * 1000 % 200) - 100  # Random offset -100 to 100
                offset_z = (time.time() * 1000 % 200) - 100

                explore_x = current_x + int(offset_x)
                explore_z = current_z + int(offset_z)
                explore_y = current_y

                logger.debug("Exploring new area at (%d, %d)", explore_x, explore_z)
                _supervised_goto(
                    client,
                    explore_x,
                    explore_y,
                    explore_z,
                    timeout=_ENDERMAN_TRAVEL_TIMEOUT,
                )

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

    except PlayerDeathDetected:
        # Death belongs to top-level recovery. Swallowing it here is what let a
        # dead bot keep logging hunt progress for minutes and then restart the
        # same hunt through the fallback below.
        raise
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
