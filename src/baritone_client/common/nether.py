"""
Utilities for Nether-specific automation steps.

These helpers currently provide best-effort implementations built on top of the
mission macros exposed by the Java bridge. They are intentionally defensive and
primarily used by higher-level scripts as orchestration primitives.
"""

import logging
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from .automation_utils import (
    clear_placement_volume, has_placement_support, place_block, recover_placed)
from .blaze_spawners import (
    camp_blaze_spawner,
    publish_blaze_spawner as _publish_blaze_spawner,
    shared_blaze_spawners as _shared_blaze_spawners,
)
from .combat import (
    defend_or_flee,
    eat_until_hunger,
    ensure_alive,
    find_entity_by_type,
    heal_if_needed,
    hunt_mobs,
    safe_combat,
)
from .inventory import count_item, find_item_slot
from .surface_egress import try_lower_surface_egress
from .tasks import PlayerDeathDetected

logger = logging.getLogger(__name__)

PortalPosition = Tuple[int, int, int]

# The bridge reports these when a coordinate's chunk is not loaded for this
# client. They mean "unknown", never "empty" -- treating them as absence makes
# any distant landmark permanently unreachable.
_UNLOADED_BLOCKS = {"minecraft:void_air", "", None}

# Compass headings used to push a stalled search outward, in a rotation that
# alternates axes so consecutive legs do not retread the same corridor.
_SEARCH_HEADINGS = (
    (1, 0),
    (0, 1),
    (-1, 0),
    (0, -1),
    (1, 1),
    (-1, -1),
    (1, -1),
    (-1, 1),
)


def _unwrap(payload: Any) -> Dict[str, Any]:
    """Accept both legacy envelopes and the current unwrapped transport ABI."""
    if not isinstance(payload, dict):
        return {}
    nested = payload.get("data")
    if isinstance(nested, dict):
        merged = dict(payload)
        merged.pop("data", None)
        merged.update(nested)
        return merged
    return payload


def _position(payload: Mapping[str, Any]) -> PortalPosition:
    pos = payload.get("block_position", payload.get("position", payload))
    if not isinstance(pos, Mapping):
        pos = {}
    return (
        int(pos.get("x", 0)),
        int(pos.get("y", 64)),
        int(pos.get("z", 0)),
    )


def _dimension(payload: Mapping[str, Any]) -> str:
    return str(payload.get("dimension", "")).lower().removeprefix("minecraft:")


def _dimension_matches(current: str, target: str) -> bool:
    aliases = {
        "nether": "the_nether",
        "the_nether": "the_nether",
        "overworld": "overworld",
        "end": "the_end",
        "the_end": "the_end",
    }
    current_name = aliases.get(current.lower().removeprefix("minecraft:"), current)
    target_name = aliases.get(target.lower().removeprefix("minecraft:"), target)
    return current_name == target_name


def _found_blocks(payload: Any) -> List[Dict[str, Any]]:
    data = _unwrap(payload)
    blocks = data.get("found", data.get("blocks", []))
    return [block for block in blocks if isinstance(block, dict)]


def _entities(payload: Any) -> List[Dict[str, Any]]:
    entities = _unwrap(payload).get("entities", [])
    return [entity for entity in entities if isinstance(entity, dict)]


def _block_id(payload: Any) -> str:
    data = _unwrap(payload)
    return str(data.get("id") or data.get("type") or data.get("block") or "")


def _wait_for_dimension(client, target: str, timeout: int) -> bool:
    """Poll bridge state until the exact canonical target dimension is active."""
    start = time.time()
    while time.time() - start < timeout:
        state = _unwrap(client.transport.dispatch("get_state", {}))
        if _dimension_matches(_dimension(state), target):
            return True
        time.sleep(1)
    return False


def _frame_positions(x: int, y: int, z: int) -> Tuple[PortalPosition, ...]:
    """The ten load-bearing frame blocks -- vanilla never checks the corners."""
    positions = [(x + dx, y, z) for dx in (1, 2)]
    positions.extend((x + dx, y + 4, z) for dx in (1, 2))
    for dy in range(1, 4):
        positions.extend(((x, y + dy, z), (x + 3, y + dy, z)))
    return tuple(positions)


def _portal_interior(x: int, y: int, z: int) -> Tuple[PortalPosition, ...]:
    return tuple((x + dx, y + dy, z) for dx in (1, 2) for dy in (1, 2, 3))


def verify_portal(
    client,
    portal: PortalPosition,
    *,
    require_active: bool = True,
) -> bool:
    """Verify a constructed frame and, optionally, active portal blocks."""
    x, y, z = portal
    if any(
        _block_id(
            client.transport.dispatch(
                "get_block", {"x": bx, "y": by, "z": bz}
            )
        )
        != "minecraft:obsidian"
        for bx, by, bz in _frame_positions(x, y, z)
    ):
        return False
    if not require_active:
        return True
    return all(
        _block_id(
            client.transport.dispatch(
                "get_block", {"x": bx, "y": by, "z": bz}
            )
        )
        == "minecraft:nether_portal"
        for bx, by, bz in _portal_interior(x, y, z)
    )


def ignite_portal(
    client,
    portal: PortalPosition,
    timeout: int = 15,
) -> bool:
    """Ignite a verified frame and wait for active portal blocks."""
    if verify_portal(client, portal, require_active=True):
        return True
    if not verify_portal(client, portal, require_active=False):
        return False
    x, y, z = portal
    target = (x + 1, y + 1, z)
    try:
        from .portal_construction import prepare_ignition_pose
        if not prepare_ignition_pose(client, portal):
            return False
        from .inventory import select_item
        if not select_item(client, "minecraft:flint_and_steel", allow_swap=True):
            return False
        client.transport.dispatch(
            "place_fire", {"x": target[0], "y": target[1], "z": target[2]}
        )
    except Exception as exc:
        logger.warning("Portal ignition command failed: %s", exc)
        # A timed-out ignition may have lit the frame. Observe without replay.
    start = time.time()
    while time.time() - start < timeout:
        if verify_portal(client, portal, require_active=True):
            return True
        time.sleep(0.5)
    return False


def build_nether_portal(client, x: int, y: int, z: int, obsidian: int = 10) -> bool:
    """
    Build a complete Nether portal frame at the specified coordinates.
    Validates materials and constructs the obsidian frame with proper dimensions.
    """
    from .portal_construction import construct_frame
    try:
        if verify_portal(client, (x, y, z), require_active=False):
            return True
        if not construct_frame(client, (x, y, z), _frame_positions(x, y, z), _portal_interior(x, y, z)):
            return False
        if not verify_portal(client, (x, y, z), require_active=False):
            return False
        client.mission.checkpoint("nether_portal_built", f"Verified frame at {x},{y},{z}")
        return True
    except Exception as exc:
        logger.error("Portal build interrupted at %s; preserve and reconcile: %s", (x, y, z), exc)
        return False


def _portal_standing_level(client, portal: PortalPosition) -> int:
    """Return the lowest contiguous portal block Y -- where a player stands.

    A portal interior is three blocks tall, and find_nearest_portal returns
    whichever block the bridge scan happened to hit first, which is often the
    middle or top one. The caller then asks Baritone for a GoalBlock at that
    height, i.e. to stand in mid-air inside the portal, which is unreachable:
    the bot paths toward it, never arrives, and the whole objective times out.
    Feet belong at the bottom block.
    """
    x, y, z = portal
    lowest = int(y)
    for candidate in range(int(y) - 1, int(y) - 4, -1):
        block = _block_id(
            client.transport.dispatch("get_block", {"x": x, "y": candidate, "z": z})
        )
        if block != "minecraft:nether_portal":
            break
        lowest = candidate
    return lowest


def _locate_portal_interior(client, portal: PortalPosition):
    """Find the lowest real portal block near ``portal``, or None.

    A persisted coordinate names *a* block of the portal -- often a frame
    corner rather than the purple interior, and callers cannot know the
    portal's axis from the coordinate alone. Guessing an interior offset
    picks a block outside the portal half the time, and asking Baritone to
    stand on a coordinate inside the obsidian frame is an unsatisfiable
    GoalBlock: it gives up without taking a step. Probe for the real thing
    instead. Returns the lowest portal block found, which is where feet go.
    """
    x, y, z = portal
    candidates = []

    # One find_blocks sweep beats probing a 5x7x5 box a block at a time: the
    # bridge scans around the player, which is exactly the region we care
    # about, and 175 round-trips per portal per attempt is not affordable.
    try:
        found = _found_blocks(
            client.transport.dispatch(
                "find_blocks",
                {"blocks": ["minecraft:nether_portal"], "radius": 32, "limit": 64},
            )
        )
    except Exception:
        found = []
    for block in found:
        try:
            candidate = (int(block["x"]), int(block["y"]), int(block["z"]))
        except (KeyError, TypeError, ValueError):
            continue
        if max(abs(candidate[0] - x), abs(candidate[2] - z)) <= 4 and abs(
            candidate[1] - y
        ) <= 4:
            candidates.append(candidate)

    # find_blocks only reports loaded chunks, so fall back to the named
    # column: it is the one coordinate we were explicitly told about.
    if not candidates:
        for dy in range(-3, 4):
            candidate = (x, y + dy, z)
            if (
                _block_id(
                    client.transport.dispatch(
                        "get_block",
                        {"x": candidate[0], "y": candidate[1], "z": candidate[2]},
                    )
                )
                == "minecraft:nether_portal"
            ):
                candidates.append(candidate)

    if not candidates:
        return None
    # Lowest block first -- that is where feet go -- then nearest the named
    # column, so a bot beside a wide portal does not walk across it.
    return min(
        candidates,
        key=lambda c: (c[1], abs(c[0] - x) + abs(c[2] - z)),
    )


def _leave_water_before_travelling(client) -> None:
    """Get onto dry land before asking Baritone for a long overland route.

    Baritone will not produce a several-hundred-block route from inside deep
    water, so the approach simply never starts and every candidate burns its
    full timeout. The existing surface helpers only run in drowning contexts,
    so a bot floating at full health never reaches them. Live 2026-08-03:
    Bot16 sat submerged at (406, 62, -22), water on all four sides and full
    health, 617 blocks from its portal, at exactly zero movement.
    """
    try:
        from .surface_recovery import position_is_aquatic, reach_dry_surface
        from .navigation import goto as _goto
    except Exception:
        return
    try:
        state = _unwrap(client.transport.dispatch("get_state", {}))
        here = _position(state)
        if not position_is_aquatic(client, here):
            return
        logger.info("In water at %s; reaching dry land before travelling", here)
        reach_dry_surface(
            client,
            origin=here,
            expected_y=here[1] + 2,
            goto=_goto,
            search_radius=48,
            attempt_limit=6,
            command_timeout=60.0,
        )
    except Exception as exc:
        logger.warning("Could not leave water before travelling: %s", exc)


def _approach_and_relocate(client, portal: PortalPosition, radius: int = 3) -> bool:
    """Walk within ``radius`` of ``portal`` so its chunk loads and we can see it.

    Uses a GoalNear rather than a GoalBlock: the named coordinate is often
    obsidian or an unloaded guess, and Baritone abandons an unsatisfiable
    GoalBlock immediately instead of getting closer.
    """
    x, y, z = portal
    _leave_water_before_travelling(client)
    # Use the same progress-aware primitive as fortress travel. Besides
    # avoiding a blind 90-second wait, this gives an idle bot one bounded
    # shelf/pillar escape before the portal candidate is rejected.
    return _travel_to(
        client,
        (int(x), int(y), int(z)),
        radius=int(radius),
        timeout=90.0,
    )


def enter_portal(
    client,
    portal: PortalPosition,
    *,
    target_dimension: str,
    timeout: int = 60,
) -> bool:
    """Walk into an active portal and verify the requested destination."""
    state = _unwrap(client.transport.dispatch("get_state", {}))
    if _dimension_matches(_dimension(state), target_dimension):
        return True
    x, y, z = portal
    target = _locate_portal_interior(client, portal)
    current_block = _block_id(
        client.transport.dispatch("get_block", {"x": x, "y": y, "z": z})
    )
    if target is None and current_block != "minecraft:nether_portal":
        if verify_portal(client, portal, require_active=True):
            # An active frame whose interior we cannot see from here. Walk
            # into range with a GoalNear -- a radius-0 GoalBlock on a frame
            # coordinate is inside obsidian and unsatisfiable -- then look
            # again now that the chunk is loaded and we are close enough.
            # Live 2026-08-03: Bot17 stood 13 blocks from a live portal for
            # 150s without moving because the old (x+1, y+1, z) guess landed
            # outside it.
            if not _approach_and_relocate(client, portal):
                logger.warning("Could not reach the portal interior at %s", portal)
                return False
            target = _locate_portal_interior(client, portal)
            if target is None:
                logger.warning("No portal interior found near %s after approach", portal)
                return False
        elif current_block in _UNLOADED_BLOCKS:
            # void_air means the chunk is not loaded, which is not evidence
            # that the portal is gone. Refusing here was self-defeating: the
            # bot cannot load the chunk without walking there, so a portal it
            # had never stood near could never be entered. Live 2026-08-03:
            # Bot07 sat 98 blocks from a verified-live portal, read all three
            # of its blocks as void_air, and refused entry 96 times.
            logger.info(
                "Portal at %s is in an unloaded chunk; approaching to verify",
                portal,
            )
            if not _approach_and_relocate(client, portal):
                logger.warning("Could not approach the portal at %s", portal)
                return False
            target = _locate_portal_interior(client, portal)
            if target is None:
                logger.warning(
                    "No portal at %s once the chunk loaded; treating it as gone",
                    portal,
                )
                return False
        else:
            # Persisted and shared portal coordinates can outlive the actual
            # blocks. Walking to an unverified coordinate wastes the entire
            # timeout and can strand the next objective in the wrong
            # dimension. Only enter a live portal block or a verified active
            # frame.
            logger.warning("Refusing to enter inactive portal at %s", portal)
            return False
    # Stand at the portal's base. radius 0 becomes a Baritone GoalBlock, which
    # demands the player occupy exactly this position, and a portal interior is
    # three blocks tall -- targeting the middle or top asks the bot to stand in
    # mid-air and can never be satisfied. Live 2026-08-02: Bot07 aimed at y=65
    # and Bot17 at y=66 of a y=64..66 portal; both burned all three retries
    # 8-14 blocks short, while a teleport into the same portal transitioned
    # instantly.
    base_y = _portal_standing_level(client, target)
    if base_y != target[1]:
        logger.info("Entering portal at its base y=%s (was %s)", base_y, target[1])
    target = (target[0], base_y, target[2])
    try:
        client.transport.dispatch(
            "goto",
            {"x": target[0], "y": target[1], "z": target[2], "radius": 0},
        )
    except Exception as exc:
        logger.warning("Portal approach failed: %s", exc)
        return False
    return _wait_for_dimension(client, target_dimension, timeout)


def enter_nether_portal(
    client,
    timeout: int = 60,
    *,
    target_dimension: str = "minecraft:the_nether",
    portal: Optional[PortalPosition] = None,
) -> bool:
    """Backward-compatible portal traversal with an explicit destination."""
    portal = portal or find_nearest_portal(client)
    if portal is None:
        return False
    return enter_portal(
        client,
        portal,
        target_dimension=target_dimension,
        timeout=timeout,
    )


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
        last_position = None
        stationary_scans = 0
        heading_index = 0
        failed_pushes = 0

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

                # explore is dispatched once, before this loop. Baritone ends
                # it on its own -- goal reached, path failure, or nothing left
                # it wants to explore -- and nothing here noticed, so the bot
                # stood still rescanning one spot for the rest of the timeout.
                # Live 2026-08-03: Bot07, Bot16 and Bot18 all sat at exactly
                # zero movement for 210s inside this loop after a productive
                # first few minutes. Scanning is not searching; if we have not
                # moved, push outward in a new direction.
                if (
                    last_position is not None
                    and abs(current_x - last_position[0]) <= 3
                    and abs(current_z - last_position[1]) <= 3
                ):
                    stationary_scans += 1
                else:
                    stationary_scans = 0
                last_position = (current_x, current_z)

                if stationary_scans >= 2:
                    stationary_scans = 0
                    failed_pushes += 1
                    # Rotating the heading only helps if *some* direction is
                    # walkable. A bot marooned on a ledge with air on every
                    # side refuses all eight, because each one is a fatal
                    # fall -- being stuck is a property of the terrain, not
                    # of the bearing. Live 2026-08-03: Bot16 and Bot18 sat on
                    # the same glowstone blob at (-132, 78, -7) in a basalt
                    # delta, cycling headings every 20s for over an hour.
                    if failed_pushes >= len(_SEARCH_HEADINGS):
                        failed_pushes = 0
                        logger.warning(
                            "No heading is walkable from (%d, %d, %d); "
                            "treating the bot as marooned",
                            current_x,
                            current_y,
                            current_z,
                        )
                        # minimum_altitude=0: the default guard exists for
                        # Overworld high-shelf work and would refuse to help
                        # here purely because y=78 is not high enough.
                        #
                        # Press the descent until it stops making progress.
                        # One call only buys a step or two, and returning to
                        # the heading rotation resets the counter, so a single
                        # attempt per circuit means one block of progress
                        # every few minutes. Live 2026-08-03: Bot16 moved
                        # exactly one block in five minutes that way.
                        escaped = False
                        for _ in range(8):
                            landing = try_lower_surface_egress(
                                client, state, minimum_altitude=0
                            )
                            if not landing:
                                break
                            escaped = True
                            state = _unwrap(
                                client.transport.dispatch("get_state", {})
                            )
                            logger.info("Marooned descent reached %s", landing)
                        if escaped:
                            last_position = None
                            continue
                    heading = _SEARCH_HEADINGS[
                        heading_index % len(_SEARCH_HEADINGS)
                    ]
                    heading_index += 1
                    leg = min(192, max(64, int(max_distance) // 4))
                    waypoint = (
                        start_x + int(heading[0] * leg),
                        current_y,
                        start_z + int(heading[1] * leg),
                    )
                    logger.info(
                        "Fortress search stalled at (%d, %d); pushing to %s",
                        current_x,
                        current_z,
                        waypoint,
                    )
                    try:
                        client.transport.dispatch(
                            "goto",
                            {
                                "x": waypoint[0],
                                "y": waypoint[1],
                                "z": waypoint[2],
                                "radius": 12,
                            },
                        )
                        client.transport.dispatch(
                            "explore", {"x": waypoint[0], "z": waypoint[2]}
                        )
                    except Exception as exc:
                        logger.warning("Could not restart fortress search: %s", exc)

                # FindBlocksCommandHandler scans around the current player; the
                # transport returns its data dictionary directly.
                fortress_blocks = [
                    "minecraft:nether_bricks",
                    "minecraft:nether_brick_fence",
                    "minecraft:nether_brick_stairs",
                    "minecraft:nether_wart",
                    "minecraft:spawner",
                ]

                blocks = _found_blocks(
                    client.transport.dispatch(
                        "find_blocks",
                        {"blocks": fortress_blocks, "radius": 32, "limit": 512},
                    )
                )

                if blocks:
                    # Count fortress-specific blocks
                    fortress_block_counts = {}
                    for block in blocks:
                        block_type = str(
                            block.get("block") or block.get("type") or block.get("id") or ""
                        )
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
                    if (
                        nether_brick_count >= 40
                        or nether_wart_count >= 1
                        or spawner_count >= 1
                    ):

                        # Find the center of the fortress structure and bounds for systematic exploration
                        if blocks:
                            # Calculate centroid of fortress blocks and track bounds for multi-level exploration
                            total_x = total_y = total_z = 0
                            count = 0
                            min_x = min_y = min_z = float('inf')
                            max_x = max_y = max_z = float('-inf')

                            for block in blocks:
                                block_type = str(
                                    block.get("block")
                                    or block.get("type")
                                    or block.get("id")
                                    or ""
                                )
                                if block_type in [
                                    "minecraft:nether_bricks",
                                    "minecraft:nether_brick_fence",
                                    "minecraft:nether_brick_stairs",
                                    "minecraft:nether_wart",
                                ]:
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


def _travel_to(
    client,
    target: PortalPosition,
    *,
    radius: int,
    timeout: float = 45.0,
    on_defense: Optional[Callable[[], bool]] = None,
    defense_check_interval: float = 0.5,
) -> bool:
    """Wait for a Nether goal with survival and defense supervision."""
    from .nether_travel import execute_nether_travel

    return execute_nether_travel(
        client,
        target,
        radius=radius,
        timeout=timeout,
        on_defense=on_defense,
        defense_check_interval=defense_check_interval,
    )

def _advance_blaze_frontier(
    client,
    fortress_blocks,
    explored_positions,
    exploration_stack,
    *,
    current: tuple[int, int, int],
    center: tuple[int, int, int],
    frontier_index: int,
) -> tuple[int, bool]:
    """Advance to an unvisited fortress cell or widen the search ring."""
    current_x, current_y, current_z = current
    center_x, center_y, center_z = center
    frontier = [
        block
        for block in fortress_blocks
        if (
            int(block[0]) // 10,
            int(block[1]) // 5,
            int(block[2]) // 10,
        ) not in explored_positions
    ]
    if frontier:
        # Nearest unvisited cell, not the farthest. Picking the most distant
        # frontier block guaranteed the bot could not reach it inside one scan
        # interval, so the cell was never marked explored and the very same
        # coordinate was chosen again on the next pass. Live 2026-08-03: Bot17
        # re-issued a goto to (-260, 47, 131) every 10 seconds and covered 10
        # blocks in three minutes. Frontier search expands from where you are.
        target_x, target_y, target_z = min(
            frontier,
            key=lambda block: (
                (block[0] - current_x) ** 2
                + (block[1] - current_y) ** 2
                + (block[2] - current_z) ** 2
            ),
        )
        exploration_stack.append(current)
        logger.info(
            "Advancing blaze search to fortress frontier (%d, %d, %d)",
            target_x,
            target_y,
            target_z,
        )
        reached = _travel_to(client, (target_x, target_y, target_z), radius=4)
        # A cell is normally marked explored by standing in it, so a frontier
        # target the bot cannot reach stays on the list and is chosen again on
        # every pass -- an endless retry against an impossible goal. Retire the
        # cell either way: arriving is what the main loop records anyway, and
        # failing to arrive is exactly what must not be repeated. Live
        # 2026-08-03: Bot07 logged "Advancing blaze search to fortress frontier
        # (709, 74, 550)" every ten seconds at precisely zero movement.
        explored_positions.add(
            (int(target_x) // 10, int(target_y) // 5, int(target_z) // 10)
        )
        if not reached:
            logger.info(
                "Frontier cell (%d, %d, %d) is unreachable; retiring it",
                target_x,
                target_y,
                target_z,
            )
        return frontier_index, True

    heading = _SEARCH_HEADINGS[frontier_index % len(_SEARCH_HEADINGS)]
    ring = 1 + frontier_index // len(_SEARCH_HEADINGS)
    distance = min(160, 24 * ring)
    target_x = center_x + heading[0] * distance
    target_z = center_z + heading[1] * distance
    logger.info(
        "Widening blaze search to (%d, %d, %d) [ring=%d]",
        target_x,
        center_y,
        target_z,
        ring,
    )
    client.transport.dispatch("explore", {"x": target_x, "z": target_z})
    # A widening ring is a long walk; wait it out rather than incrementing the
    # ring every few seconds. Live 2026-08-03: Bot18 raced from ring 14 to 15
    # through six different coordinates inside 30 seconds without arriving at
    # any of them, so the "search" only ever explored its starting area.
    _travel_to(client, (target_x, center_y, target_z), radius=12, timeout=90.0)
    return frontier_index + 1, False


def _camp_blaze_spawner(
    client,
    position: PortalPosition,
    *,
    target_count: int,
    deadline: float,
) -> bool:
    return camp_blaze_spawner(
        client,
        position,
        target_count=target_count,
        deadline=deadline,
        travel_to=_travel_to,
    )


def _world_difficulty(client, state: Dict) -> str:
    """Read difficulty from state or the bridge's world-info capability."""
    difficulty = str(state.get("difficulty") or "").lower()
    if difficulty:
        return difficulty
    try:
        world = client.transport.dispatch("get_world_info", {})
    except Exception:
        return ""
    return str(world.get("difficulty") or "").lower() if isinstance(world, dict) else ""


def hunt_blazes(
    client,
    target_count: int = 8,
    timeout: int = 600,
    *,
    state=None,
) -> int:
    """
    Hunt blazes using systematic fortress exploration with backtracking.
    ``target_count`` is the desired total rod count, not additional rods.
    Assumes the player is near the center returned by find_nether_fortress.
    """
    try:
        logger.info("Starting systematic fortress blaze hunt for %d rods", target_count)
        start_time = time.time()
        deadline = start_time + timeout
        rods_start = count_item(client, "minecraft:blaze_rod")
        world_state = client.transport.dispatch("get_state", {})
        difficulty = _world_difficulty(client, world_state)
        if difficulty == "peaceful":
            logger.error(
                "Cannot hunt blazes while world difficulty is Peaceful; "
                "hostile spawning is disabled"
            )
            return rods_start

        # Shared sightings are normal world knowledge. Reuse them before
        # making every controller independently rediscover the same fortress.
        state_manager = state
        for known_spawner in _shared_blaze_spawners(client, state_manager):
            logger.info("Trying shared blaze spawner at %s", known_spawner)
            if _camp_blaze_spawner(
                client,
                known_spawner,
                target_count=target_count,
                deadline=deadline,
            ):
                return count_item(client, "minecraft:blaze_rod")

        # Get current position as fortress center reference
        snapshot = client.transport.dispatch("get_state", {})
        fortress_center = snapshot.get(
            "block_position", snapshot.get("position", {})
        )
        center_x = int(fortress_center.get("x", 0))
        center_y = int(fortress_center.get("y", 64))
        center_z = int(fortress_center.get("z", 0))

        # Track explored areas to avoid loops
        explored_positions = set()
        exploration_stack = []  # For backtracking
        spawners_found = []
        frontier_index = 0

        current_target = None
        scan_interval = 5  # Scan every 5 seconds
        last_scan = 0.0

        while time.time() - start_time < timeout:
            current_time = time.time()

            # Check if we have enough rods
            current_rods = count_item(client, "minecraft:blaze_rod")
            if current_rods >= target_count:
                logger.info("Blaze rod target reached: %d/%d", current_rods, target_count)
                break

            # Periodic scanning for spawners and fortress structure
            if current_time - last_scan >= scan_interval:
                last_scan = current_time

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
                        _travel_to(client, prev_pos, radius=4)
                        continue
                    else:
                        # No more backtrack points, pick a new direction from center
                        pass

                explored_positions.add(current_pos_key)

                # Scan for blaze spawners and fortress blocks
                blocks = _found_blocks(
                    client.transport.dispatch(
                        "find_blocks",
                        {
                            "blocks": [
                                "minecraft:spawner",
                                "minecraft:nether_bricks",
                                "minecraft:nether_brick_stairs",
                            ],
                            "radius": 20,
                            "limit": 256,
                        },
                    )
                )

                if blocks:
                    fortress_blocks = []
                    new_spawners = []

                    for block in blocks:
                        block_type = str(
                            block.get("block") or block.get("type") or block.get("id") or ""
                        )
                        if block_type == "minecraft:spawner":
                            # Check if it's a blaze spawner by examining nearby blocks or entity data
                            spawner_pos = (block.get("x"), block.get("y"), block.get("z"))
                            if spawner_pos not in spawners_found:
                                new_spawners.append(spawner_pos)
                                spawners_found.append(spawner_pos)
                                logger.info("Found blaze spawner at %s", spawner_pos)
                                _publish_blaze_spawner(
                                    client, state_manager, spawner_pos
                                )
                        elif block_type in ["minecraft:nether_bricks", "minecraft:nether_brick_stairs"]:
                            fortress_blocks.append((block.get("x"), block.get("y"), block.get("z")))

                    # If we found new spawners, prioritize hunting there
                    if new_spawners:
                        for spawner_x, spawner_y, spawner_z in new_spawners:
                            logger.info("Navigating to spawner at (%d, %d, %d)", spawner_x, spawner_y, spawner_z)

                            if _camp_blaze_spawner(
                                client,
                                (spawner_x, spawner_y, spawner_z),
                                target_count=target_count,
                                deadline=deadline,
                            ):
                                return count_item(client, "minecraft:blaze_rod")

                    frontier_index, advanced = _advance_blaze_frontier(
                        client,
                        fortress_blocks,
                        explored_positions,
                        exploration_stack,
                        current=(current_x, current_y, current_z),
                        center=(center_x, center_y, center_z),
                        frontier_index=frontier_index,
                    )
                    if advanced:
                        continue
                else:
                    # The fortress detector can see a structure outside this
                    # smaller hunt scan (most often on another Y level).  An
                    # empty local result still needs to move the search; the
                    # old loop simply slept and rescanned the same empty area
                    # until the full hunt timeout expired.
                    frontier_index, _ = _advance_blaze_frontier(
                        client,
                        [],
                        explored_positions,
                        exploration_stack,
                        current=(current_x, current_y, current_z),
                        center=(center_x, center_y, center_z),
                        frontier_index=frontier_index,
                    )

            time.sleep(1)  # Small delay between iterations

        # Final count
        final_rods = count_item(client, "minecraft:blaze_rod")
        collected = final_rods - rods_start
        logger.info("Blaze hunt completed. Collected %d rods (target: %d)", collected, target_count)

        return final_rods

    except Exception as exc:
        logger.error("Blaze hunt failed: %s", exc)
        return count_item(client, "minecraft:blaze_rod")


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

        blocks = _found_blocks(
            client.transport.dispatch(
                "find_blocks",
                {"blocks": ["minecraft:nether_portal"], "radius": 32, "limit": 64},
            )
        )
        if blocks:
            nearest = min(
                blocks,
                key=lambda block: float(
                    block.get(
                        "distance",
                        (int(block.get("x", 0)) - current_x) ** 2
                        + (int(block.get("z", 0)) - current_z) ** 2,
                    )
                ),
            )
            portal = (
                int(nearest.get("x", current_x)),
                int(nearest.get("y", 64)),
                int(nearest.get("z", current_z)),
            )
            logger.info("Found portal at %s in %s", portal, dimension)
            return portal

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
    start_inventory = _unwrap(
        client.transport.dispatch("get_inventory", {})
    ).get("inventory", [])

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
    end_inventory = _unwrap(
        client.transport.dispatch("get_inventory", {})
    ).get("inventory", [])
    
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

