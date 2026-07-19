"""
Resource gathering utilities - Wood, stone, ores, and materials.
"""

import math
import time
from typing import Any, Callable, Dict, Optional, Set
from .inventory import count_item, craft, ensure_tool_sticks, select_item
from .tasks import TaskResult
from .combat import hunt_mobs
from .navigation import find_nearby_block, goto

# Log block types (full IDs)
LOG_BLOCKS = [
    "minecraft:oak_log",
    "minecraft:birch_log",
    "minecraft:spruce_log",
    "minecraft:dark_oak_log",
    "minecraft:acacia_log",
    "minecraft:jungle_log",
    "minecraft:mangrove_log",
    "minecraft:cherry_log",
    "minecraft:pale_oak_log", # 1.21.4
]

# Stone block types
STONE_BLOCKS = [
    "minecraft:stone",
    "minecraft:cobblestone",
    "minecraft:deepslate",
    "minecraft:cobbled_deepslate",
]

PLANK_ITEMS = [
    "minecraft:oak_planks",
    "minecraft:birch_planks",
    "minecraft:spruce_planks",
    "minecraft:dark_oak_planks",
    "minecraft:acacia_planks",
    "minecraft:jungle_planks",
    "minecraft:mangrove_planks",
    "minecraft:cherry_planks",
    "minecraft:pale_oak_planks",
]

LOG_TO_PLANKS = {
    "minecraft:oak_log": "minecraft:oak_planks",
    "minecraft:birch_log": "minecraft:birch_planks",
    "minecraft:spruce_log": "minecraft:spruce_planks",
    "minecraft:dark_oak_log": "minecraft:dark_oak_planks",
    "minecraft:acacia_log": "minecraft:acacia_planks",
    "minecraft:jungle_log": "minecraft:jungle_planks",
    "minecraft:mangrove_log": "minecraft:mangrove_planks",
    "minecraft:cherry_log": "minecraft:cherry_planks",
    "minecraft:pale_oak_log": "minecraft:pale_oak_planks",
}

# Number of furnace operations provided by one item.  Planks are deliberately
# preferred over logs: one log crafts into four planks and therefore smelts
# four times as much material.  Values are stable vanilla recipe data.
FURNACE_FUEL_SMELTS = {
    "minecraft:coal": 8.0,
    "minecraft:charcoal": 8.0,
    **{item_id: 1.5 for item_id in PLANK_ITEMS},
    **{item_id: 1.5 for item_id in LOG_TO_PLANKS},
}

PICKAXE_ITEMS = [
    "minecraft:wooden_pickaxe",
    "minecraft:stone_pickaxe",
    "minecraft:iron_pickaxe",
    "minecraft:diamond_pickaxe",
    "minecraft:netherite_pickaxe",
]

PICKAXE_MAX_DAMAGE = {
    "minecraft:wooden_pickaxe": 59,
    "minecraft:stone_pickaxe": 131,
    "minecraft:iron_pickaxe": 250,
    "minecraft:diamond_pickaxe": 1561,
    "minecraft:netherite_pickaxe": 2031,
}
_PICKAXE_TIERS = [
    "minecraft:wooden_pickaxe",
    "minecraft:stone_pickaxe",
    "minecraft:iron_pickaxe",
    "minecraft:diamond_pickaxe",
    "minecraft:netherite_pickaxe",
]
_PICKAXE_TIER_RANK = {item_id: rank for rank, item_id in enumerate(_PICKAXE_TIERS)}

# Ore types by tier
ORES = {
    "coal": ["minecraft:coal_ore", "minecraft:deepslate_coal_ore"],
    "iron": ["minecraft:iron_ore", "minecraft:deepslate_iron_ore"],
    "gold": ["minecraft:gold_ore", "minecraft:deepslate_gold_ore", "minecraft:nether_gold_ore"],
    "diamond": ["minecraft:diamond_ore", "minecraft:deepslate_diamond_ore"],
    "copper": ["minecraft:copper_ore", "minecraft:deepslate_copper_ore"],
    "lapis": ["minecraft:lapis_ore", "minecraft:deepslate_lapis_ore"],
    "redstone": ["minecraft:redstone_ore", "minecraft:deepslate_redstone_ore"],
    "emerald": ["minecraft:emerald_ore", "minecraft:deepslate_emerald_ore"],
}

_BARITONE_COMMAND_SPACING_SECONDS = 0.25
_BARITONE_CANCEL_GRACE_SECONDS = 0.75
_BARITONE_MINE_SETUP_SECONDS = 0.25
_BARITONE_GOTO_TO_BREAK_GUARD_SECONDS = 0.25
_BARITONE_BUILD_CLEANUP_SECONDS = 1.0

_ORE_FALLBACK_RADII = (8, 10, 12, 14, 16)
_ORE_FALLBACK_LIMIT = 3


def _serialized_dispatch(
    client,
    route: str,
    payload: Dict[str, Any],
    post_delay_seconds: float = 0.0,
) -> Any:
    """Serialize control commands to reduce render-thread mutation overlap."""
    response = client.transport.dispatch(route, payload)
    if route in {"cancel", "mine", "goto", "break_block"}:
        time.sleep(max(post_delay_seconds, _BARITONE_COMMAND_SPACING_SECONDS))
    elif post_delay_seconds:
        time.sleep(post_delay_seconds)
    return response


def _ensure_outdoor_daylight(client, state: Dict) -> bool:
    """Pause an exposed gatherer at dusk and resume only after dawn."""
    day_time = int(state.get("world_time", 0)) % 24000
    if day_time < 12000:
        return True
    print(f"GATHER: night boundary reached (time={day_time}); seeking shelter")
    client.transport.dispatch("cancel", {})
    from .base import wait_for_safe_daylight
    return wait_for_safe_daylight(client)


def _ensure_mining_pickaxe(client) -> bool:
    """Ensure stone gathering has a live pickaxe, including mid-task wear."""
    if sum(count_item(client, item_id) for item_id in PICKAXE_ITEMS) > 0:
        return True

    # Replacing a broken tool can open a crafting table, place one nearby, or
    # move toward its saved waypoint.  Doing that after dark exposed a live
    # run to a zombie while the crafting screen was open.  Stay at the mining
    # position until daylight before beginning any replacement workflow.
    state = client.transport.dispatch("get_state", {})
    day_time = int(state.get("world_time", 0)) % 24000
    if day_time >= 12000:
        print(
            f"DEBUG: Pickaxe broke at night (time={day_time}); "
            "waiting for safe daylight before crafting"
        )
        client.transport.dispatch("cancel", {})
        from .base import wait_for_safe_daylight

        if not wait_for_safe_daylight(client):
            print("DEBUG: Could not establish daylight for pickaxe replacement")
            return False

    print("DEBUG: Mining pickaxe missing or broken; crafting a replacement...")
    replacement = (
        "minecraft:stone_pickaxe"
        if count_item(client, "minecraft:cobblestone") >= 3
        else "minecraft:wooden_pickaxe"
    )
    return ensure_supplies(client, {replacement: 1}).success


def remaining_pickaxe_durability(
    client,
    pickaxe_ids: Optional[list[str]] = None,
) -> int:
    """Return the total remaining durability of selected carried pickaxes.

    The inventory bridge exposes per-stack ``damage`` but its current
    ``get_inventory`` handler omits ``max_damage``.  Tool durability is stable
    Minecraft recipe data, so use the known maximums as a fallback.  Counting
    durability instead of just item stacks prevents a nearly-broken pickaxe
    from being treated as a complete deep-mining kit.
    """
    allowed = set(pickaxe_ids or PICKAXE_ITEMS)
    response = client.transport.dispatch("get_inventory", {})
    data = response.get("data", response)
    remaining = 0
    for section in ("inventory", "offhand"):
        for item in data.get(section, []):
            item_id = str(item.get("id", ""))
            if item_id not in allowed or int(item.get("count", 0)) <= 0:
                continue
            maximum = int(
                item.get("max_damage", PICKAXE_MAX_DAMAGE.get(item_id, 0)) or 0
            )
            damage = max(0, int(item.get("damage", 0) or 0))
            remaining += max(0, maximum - damage)
    return remaining


def _start_mine_process(client, blocks: list[str], quantity: int) -> None:
    """Replace a Baritone mining process only after the previous tick settles.

    Baritone 1.15.0 crashed in a live run while its render-thread tick iterated
    an internal ``ArrayList`` that a bridge request changed concurrently.
    Cancelling and immediately starting/searching was the trigger pattern.
    Give the client several ticks to finish processing cancellation before a
    replacement process is installed.
    """
    _serialized_dispatch(
        client,
        "cancel",
        {},
        post_delay_seconds=_BARITONE_CANCEL_GRACE_SECONDS,
    )
    _serialized_dispatch(
        client,
        "mine",
        {"blocks": blocks, "quantity": quantity},
        post_delay_seconds=_BARITONE_MINE_SETUP_SECONDS,
    )


def _approach_and_break_log(client, log_pos: tuple[int, int, int]) -> bool:
    """Approach a discovered log, then start a coordinate-specific break.

    A raw ``goto`` goal at the log coordinates is impossible because the goal
    is inside a solid block.  Use the navigation helper's arrival tolerance
    to stop beside the tree, then hand that exact block to Baritone's builder
    process.  The surrounding gather loop verifies the inventory increase.
    """
    lx, ly, lz = log_pos
    if not goto(
        client,
        lx,
        ly,
        lz,
        timeout=45,
        check_interval=1.0,
        tolerance=3.0,
    ):
        return False
    select_item(client, "minecraft:stone_axe", allow_swap=True)
    response = client.transport.dispatch(
        "break_block", {"x": lx, "y": ly, "z": lz}
    )
    return not bool(response.get("error"))


def _find_safe_nearby_stone(client, radius: int = 8) -> Optional[tuple[int, int, int]]:
    """Return a reachable-looking natural stone face without mining underfoot.

    The bridge's broad ``mine`` process can stop even while exposed stone is
    immediately beside the player.  Its ``find_blocks`` response is not sorted,
    so request a batch and rank it locally.  Cobblestone is deliberately omitted
    here so this recovery path cannot dismantle the starter house or utilities.
    """
    state = client.transport.dispatch("get_state", {})
    position = state.get("block_position", state.get("position", {}))
    px = int(position.get("x", state.get("x", 0)))
    py = int(position.get("y", state.get("y", 0)))
    pz = int(position.get("z", state.get("z", 0)))

    response = client.transport.dispatch(
        "find_blocks",
        {
            "blocks": ["minecraft:stone", "minecraft:deepslate"],
            "radius": max(1, min(int(radius), 16)),
            "limit": 4096,
        },
    )
    candidates = []
    for block in response.get("found", []):
        x, y, z = int(block["x"]), int(block["y"]), int(block["z"])
        # Never remove the block directly beneath the player.  Prefer exposed
        # blocks around torso/foot level and reject deep targets that require a
        # blind vertical dig from the surface.
        if x == px and z == pz and y < py:
            continue
        if y < py - 6 or y > py + 3:
            continue
        distance = ((x - px) ** 2 + (y - py) ** 2 + (z - pz) ** 2) ** 0.5
        candidates.append((abs(y - py), distance, x, y, z))

    if not candidates:
        return None
    _, _, x, y, z = min(candidates)
    return (x, y, z)


def _approach_and_break_stone(client, stone_pos: tuple[int, int, int]) -> bool:
    """Break one exact natural-stone block and verify the world changed."""
    sx, sy, sz = stone_pos
    if not goto(
        client,
        sx,
        sy,
        sz,
        timeout=30,
        check_interval=0.5,
        tolerance=3.0,
    ):
        return False
    time.sleep(_BARITONE_GOTO_TO_BREAK_GUARD_SECONDS)

    # Prefer the strongest available pickaxe.  ``allow_swap`` makes this work
    # even when the only pickaxe is outside the hotbar.
    if not any(
        select_item(client, item_id, allow_swap=True)
        for item_id in reversed(PICKAXE_ITEMS)
    ):
        return False

    response = _serialized_dispatch(
        client,
        "break_block",
        {"x": sx, "y": sy, "z": sz},
    )
    if response.get("error"):
        return False

    deadline = time.time() + 12
    while time.time() < deadline:
        block = client.transport.dispatch(
            "get_block", {"x": sx, "y": sy, "z": sz}
        ).get("id", "")
        if block in ("", "minecraft:air"):
            # Give the nearby drop a moment to enter the inventory before the
            # caller recounts cobblestone.
            time.sleep(1.0)
            _serialized_dispatch(client, "cancel", {}, post_delay_seconds=0.0)
            return True
        time.sleep(0.5)

    _serialized_dispatch(client, "cancel", {}, post_delay_seconds=0.0)
    return False


def _find_safe_nearby_ore(
    client,
    block_types: list[str],
    radius: int = 8,
    excluded_positions: Optional[Set[tuple[int, int, int]]] = None,
) -> Optional[tuple[int, int, int]]:
    """Find a nearby ore face while refusing to remove the block underfoot."""
    state = client.transport.dispatch("get_state", {})
    position = state.get("block_position", state.get("position", {}))
    px = int(position.get("x", state.get("x", 0)))
    py = int(position.get("y", state.get("y", 0)))
    pz = int(position.get("z", state.get("z", 0)))
    response = client.transport.dispatch(
        "find_blocks",
        {
            "blocks": block_types,
            "radius": max(1, min(int(radius), 16)),
            "limit": 4096,
        },
    )
    candidates = []
    excluded = set(excluded_positions or ())
    for block in response.get("found", []):
        x, y, z = int(block["x"]), int(block["y"]), int(block["z"])
        if x == px and z == pz and y < py:
            continue
        if y < py - 8 or y > py + 4:
            continue
        if (x, y, z) in excluded:
            continue
        distance = ((x - px) ** 2 + (y - py) ** 2 + (z - pz) ** 2) ** 0.5
        candidates.append((abs(y - py), distance, x, y, z))
    if not candidates:
        return None
    _, _, x, y, z = min(candidates)
    return (x, y, z)


def gather_wood(
    client,
    count: int = 16,
    timeout: int = 180,
    latest_world_time: Optional[int] = None,
    max_distance_from_origin: Optional[float] = None,
    abort_on_threats: bool = False,
    minimum_health: float = 12.0,
) -> bool:
    """Gather wood logs until count reached.
    
    Smart behavior: Checks for existing planks first. If we have enough planks
    to satisfy the wood requirement (4 planks = 1 log equivalent), skip gathering.
    """
    # Check existing logs first
    total_logs = sum(count_item(client, block) for block in LOG_BLOCKS)
    if total_logs >= count:
        print(f"DEBUG: Already have {total_logs} logs, skipping gather")
        return True
    
    # Check existing planks - if we have enough planks, we don't need logs
    # 1 log = 4 planks, so planks/4 = equivalent logs
    PLANK_TYPES = [
        "minecraft:oak_planks", "minecraft:birch_planks", "minecraft:spruce_planks",
        "minecraft:dark_oak_planks", "minecraft:acacia_planks", "minecraft:jungle_planks",
        "minecraft:mangrove_planks", "minecraft:cherry_planks"
    ]
    total_planks = sum(count_item(client, p) for p in PLANK_TYPES)
    equivalent_logs = total_planks // 4  # 4 planks = 1 log
    
    if total_logs + equivalent_logs >= count:
        print(f"DEBUG: Have {total_logs} logs + {total_planks} planks ({equivalent_logs} log equiv) = enough! Skipping gather")
        return True
    
    # Calculate how many more logs we actually need
    needed = count - total_logs - equivalent_logs
    print(f"DEBUG: Need {needed} more logs (have {total_logs} logs, {total_planks} planks)")
    
    try:
        # Check the safety boundary before creating a path.  Starting the mine
        # process first gave Baritone enough time to walk out of the starter
        # house at night before the first loop iteration could cancel it.
        initial_state = client.transport.dispatch("get_state", {})
        start_position = initial_state.get(
            "block_position",
            initial_state.get("position", {}),
        )
        origin_x = float(start_position.get("x", 0))
        origin_z = float(start_position.get("z", 0))
        if (
            latest_world_time is not None
            and int(initial_state.get("world_time", 0)) % 24000
            >= int(latest_world_time)
        ):
            return False
        if not _ensure_outdoor_daylight(client, initial_state):
            return False
        _start_mine_process(client, LOG_BLOCKS, needed + 4)
        start = time.time()
        idle_checks = 0
        last_count = total_logs + equivalent_logs
        stalled_checks = 0
        exploring = False
        
        while time.time() - start < timeout:
            state = client.transport.dispatch("get_state", {})
            day_time = int(state.get("world_time", 0)) % 24000
            if latest_world_time is not None and day_time >= int(
                latest_world_time
            ):
                print("DEBUG: Wood gathering reached its return-home boundary")
                client.transport.dispatch("cancel", {})
                return False
            if float(state.get("health", 20.0)) < float(minimum_health):
                print("DEBUG: Wood gathering stopped below safe health")
                client.transport.dispatch("cancel", {})
                return False
            position = state.get("block_position", state.get("position", {}))
            distance = (
                (float(position.get("x", 0)) - origin_x) ** 2
                + (float(position.get("z", 0)) - origin_z) ** 2
            ) ** 0.5
            if (
                max_distance_from_origin is not None
                and distance > float(max_distance_from_origin)
            ):
                print("DEBUG: Wood gathering reached its expedition radius")
                client.transport.dispatch("cancel", {})
                return False
            food_level = int(state.get("food_level", state.get("food", 20)))
            if food_level <= 10:
                from .combat import eat_until_hunger

                client.transport.dispatch("cancel", {})
                if not eat_until_hunger(client, minimum_food=14):
                    print("DEBUG: Wood gathering stopped because food ran out")
                    return False
                _start_mine_process(client, LOG_BLOCKS, needed + 4)
                continue
            if not _ensure_outdoor_daylight(client, state):
                return False
            if int(state.get("world_time", 0)) % 24000 >= 12000:
                client.transport.dispatch(
                    "mine", {"blocks": LOG_BLOCKS, "quantity": needed + 4}
                )
                idle_checks = 0
                stalled_checks = 0
                exploring = False
                continue

            # INTEGRATE DEFENSE
            from .combat import defend_or_flee, scan_for_threats
            if abort_on_threats and scan_for_threats(client, radius=12):
                 print("DEBUG: Hostile entered bounded wood-gathering radius")
                 client.transport.dispatch("cancel", {})
                 return False
            if not abort_on_threats and defend_or_flee(client):
                 print("DEBUG: Wood gathering interrupted by defense logic. Resuming mining...")
                 # Re-issue mine command just in case
                 client.transport.dispatch("mine", {"blocks": LOG_BLOCKS, "quantity": needed + 4})
                 time.sleep(2)
                 idle_checks = 0
                 stalled_checks = 0
                 exploring = False
                 continue

            # Fail Fast: Check if Baritone gave up (is_pathing = False)
            state = client.transport.dispatch("get_state", {})
            is_pathing = state.get("is_pathing", True)
            
            # Inventory Progress Tracking
            curr_logs = sum(count_item(client, block) for block in LOG_BLOCKS)
            curr_planks = sum(count_item(client, p) for p in PLANK_TYPES)
            total = curr_logs + (curr_planks // 4)
            print(f"DEBUG: gather_wood total={total}/{count} (Pathing: {is_pathing})")
            
            if total >= count:
                client.transport.dispatch("cancel", {})
                return True

            if not is_pathing:
                idle_checks += 1
                if idle_checks >= 3:
                    if not exploring:
                        if idle_checks == 3:
                            print("DEBUG: Baritone idle. Retrying mine command locally...")
                            client.transport.dispatch("mine", {"blocks": LOG_BLOCKS, "quantity": needed + 4})
                        elif idle_checks >= 6: # ~18s total idle
                            print("DEBUG: No trees nearby (idle)? Attempting explicit search...")
                            # Explicitly find blocks since 'mine' command might be failing
                            found_log = find_nearby_block(client, LOG_BLOCKS, radius=128)
                            if found_log:
                                lx, ly, lz = found_log
                                print(
                                    f"DEBUG: Found log at ({lx}, {ly}, {lz}). "
                                    "Approaching and breaking it directly..."
                                )
                                if _approach_and_break_log(client, found_log):
                                    exploring = False
                                else:
                                    print(
                                        "DEBUG: Could not approach explicit log; "
                                        "exploring for a reachable tree..."
                                    )
                                    client.transport.dispatch(
                                        "chat", {"message": "#explore"}
                                    )
                                    exploring = True
                                idle_checks = 0
                                stalled_checks = 0
                                time.sleep(2)
                            else:
                                if max_distance_from_origin is not None:
                                    print(
                                        "DEBUG: No logs in bounded search radius; "
                                        "returning home"
                                    )
                                    client.transport.dispatch("cancel", {})
                                    return False
                                print("DEBUG: No logs found in radius 128. Random exploration...")
                                client.transport.dispatch("chat", {"message": "#explore"})
                                exploring = True
                                idle_checks = 0
                                time.sleep(2)
                        continue
                    else:
                        # We were exploring/pathing, but now we stopped.
                        # Assume we arrived or stuck.
                        print("DEBUG: Exploration/Goto finished (stopped pathing). Resuming mining...")
                        client.transport.dispatch("mine", {"blocks": LOG_BLOCKS, "quantity": needed + 4})
                        exploring = False
                        idle_checks = 0
                        stalled_checks = 0
                        continue
                        
                        # Old logic below for reference, replaced by above:
                        # if idle_checks < 20: ... pass ... else: fail
            else:
                if exploring:
                    # If we started pathing again, we might have found a tree or just moving.
                    # Let's give it time.
                    pass
                idle_checks = 0
            
            if stalled_checks >= 10 and is_pathing: # Only check stall if we THINK we are moving
                print(f"DEBUG: Wood gathering stalled (stalled_checks={stalled_checks}). Retrying local mine command...")
                client.transport.dispatch("cancel", {})
                time.sleep(0.5)
                client.transport.dispatch("mine", {"blocks": LOG_BLOCKS, "quantity": needed + 4})
                stalled_checks = 0
                # Do NOT reset idle_checks here
                
                # Only explore if we've stalled multiple times effectively
                # actually let's implement a separate counter for "major stalls"
                # but for now, just retrying the mine command usually fixes pathing issues
                continue

            if total == last_count:
                stalled_checks += 1
            else:
                stalled_checks = 0
                last_count = total
                if exploring:
                    print("DEBUG: Found wood during exploration! Cancelling explore and mining...")
                    client.transport.dispatch("cancel", {})
                    client.transport.dispatch("mine", {"blocks": LOG_BLOCKS, "quantity": needed + 4})
                    exploring = False
            
            # Timeout/Still Stuck handling
            if stalled_checks >= 15 and not exploring:
                 print("DEBUG: No wood progress for 45s. NOW attempting exploration...")
                 client.transport.dispatch("chat", {"message": "#explore"})
                 exploring = True
                 stalled_checks = 0
            elif stalled_checks >= 30 and exploring:
                 print("DEBUG: Still no wood progress during exploration. Giving up.")
                 return False
                
            time.sleep(3)
        print("DEBUG: gather_wood timeout")
        client.transport.dispatch("cancel", {})
        return False
    except Exception as exc:
        print(f"Wood gathering error: {exc}")
        return False


def gather_stone(client, count: int = 16, timeout: int = 180) -> bool:
    """Gather cobblestone until count reached."""
    try:
        # Do not briefly start and immediately cancel Baritone's mine process
        # when the target is already satisfied.  That start/cancel race can
        # leave Baritone's block lookup cleared while its tick is still using
        # it, crashing the client with a builder-process NPE.
        current_total = count_item(client, "minecraft:cobblestone") + count_item(
            client, "minecraft:cobbled_deepslate"
        )
        if current_total >= count:
            print(f"DEBUG: gather_stone success! total={current_total}")
            return True

        # Check for pickaxe - cannot mine stone with hand
        if not _ensure_mining_pickaxe(client):
             print("DEBUG: No pickaxe for gathering stone! Ensuring wooden pickaxe...")
             print("DEBUG: Failed to acquire pickaxe. Aborting gather_stone.")
             return False

        # Initial attempt: standard mine
        client.transport.dispatch("mine", {"blocks": STONE_BLOCKS, "quantity": count + 10})
        
        start = time.time()
        last_count = current_total
        stalled_checks = 0
        idle_checks = 0
        direct_failures = 0
        
        while time.time() - start < timeout:
            # INTEGRATE DEFENSE
            from .combat import defend_or_flee
            if defend_or_flee(client):
                 # Combat happened. Resume mining.
                 client.transport.dispatch("mine", {"blocks": STONE_BLOCKS, "quantity": count + 10})
                 time.sleep(2)
                 idle_checks = 0
                 continue

            if not _ensure_mining_pickaxe(client):
                print("DEBUG: Could not replace worn pickaxe during stone gathering")
                return False
            # Fail Fast: Check if Baritone gave up (is_pathing = False)
            state = client.transport.dispatch("get_state", {})
            is_pathing = state.get("is_pathing", True)
            if not is_pathing:
                idle_checks += 1
            else:
                idle_checks = 0

            total = count_item(client, "minecraft:cobblestone") + count_item(client, "minecraft:cobbled_deepslate")
            print(f"DEBUG: gather_stone loop: total={total}/{count}")
            
            if total >= count:
                print(f"DEBUG: gather_stone success! total={total}")
                client.transport.dispatch("cancel", {})
                return True
                
            # Stall detection: If we aren't getting items
            if total == last_count:
                stalled_checks += 1
            else:
                stalled_checks = 0
                last_count = total
                
            # If the broad Baritone process stops or makes no progress, mine a
            # verified nearby natural-stone face by exact coordinate.  Reissuing
            # ``mine`` on every idle poll used to reset these counters forever.
            if idle_checks >= 2 or stalled_checks >= 5:
                print("DEBUG: Stone gathering stalled. Trying exact nearby stone...")
                client.transport.dispatch("cancel", {})
                target = _find_safe_nearby_stone(client)
                if target and _approach_and_break_stone(client, target):
                    print(f"DEBUG: Broke nearby stone at {target}")
                    direct_failures = 0
                    idle_checks = 0
                    stalled_checks = 0
                    continue

                direct_failures += 1
                print(
                    "DEBUG: Exact nearby-stone attempt failed "
                    f"({direct_failures}/3); retrying broad mine"
                )
                if direct_failures >= 3:
                    print("DEBUG: No reachable nearby stone after 3 exact attempts")
                    return False
                client.transport.dispatch(
                    "mine", {"blocks": STONE_BLOCKS, "quantity": count + 10}
                )
                idle_checks = 0
                stalled_checks = 0
                
            time.sleep(3)
            
        print("DEBUG: gather_stone timeout")
        client.transport.dispatch("cancel", {})
        return False
    except Exception as exc:
        print(f"Stone gathering error: {exc}")
        return False


def gather_ores(client, ore_type: str, count: int, timeout: int = 600) -> bool:
    """Gather specific ore type."""
    if ore_type not in ORES:
        print(f"Unknown ore type: {ore_type}")
        return False
    drop_items = {
        "coal": "minecraft:coal",
        "iron": "minecraft:raw_iron",
        "gold": "minecraft:raw_gold",
        "diamond": "minecraft:diamond",
        "copper": "minecraft:raw_copper",
        "lapis": "minecraft:lapis_lazuli",
        "redstone": "minecraft:redstone",
        "emerald": "minecraft:emerald",
    }
    drop_item = drop_items.get(ore_type, f"minecraft:raw_{ore_type}")
    try:
        current_total = count_item(client, drop_item)
        if current_total >= count:
            return True

        # Initial check for pickaxe - preventing infinite loops after tool wear.
        # Diamond ore specifically requires iron tier or better.
        usable_pickaxes = (
            [
                "minecraft:iron_pickaxe",
                "minecraft:diamond_pickaxe",
                "minecraft:netherite_pickaxe",
            ]
            if ore_type == "diamond"
            else PICKAXE_ITEMS
        )
        if remaining_pickaxe_durability(client, usable_pickaxes) <= 0:
            print("DEBUG: No usable pickaxe at start of gather_ores; preparing one...")
            replacement = (
                "minecraft:iron_pickaxe"
                if ore_type == "diamond"
                else "minecraft:stone_pickaxe"
            )
            if not ensure_supplies(client, {replacement: 1}, timeout=120).success:
                return False

        _start_mine_process(client, ORES[ore_type], count + 2)
        start = time.time()
        idle_checks = 0
        last_tool_check = time.time()
        last_total = current_total
        stalled_checks = 0
        direct_failures = 0
        failed_targets: set[tuple[int, int, int]] = set()
        
        while time.time() - start < timeout:
            # INTEGRATE DEFENSE
            from .combat import defend_or_flee
            if defend_or_flee(client):
                _start_mine_process(client, ORES[ore_type], count + 2)
                time.sleep(2)
                idle_checks = 0
                continue

            # Check pickaxe every 30 seconds
            if time.time() - last_tool_check > 30:
                if remaining_pickaxe_durability(client, usable_pickaxes) <= 0:
                    print("DEBUG: Pickaxe exhausted during ore gathering; stopping safely")
                    client.transport.dispatch("cancel", {})
                    time.sleep(0.75)
                    return False
                last_tool_check = time.time()

            # Fail Fast: Check if Baritone gave up (is_pathing = False)
            state = client.transport.dispatch("get_state", {})
            is_pathing = state.get("is_pathing", True) # Default True to be safe

            if int(state.get("food_level", state.get("food", 20))) <= 8:
                from .combat import eat_until_hunger
                client.transport.dispatch("cancel", {})
                time.sleep(0.75)
                if not eat_until_hunger(client, minimum_food=14):
                    print("DEBUG: Ore gathering stopped: hunger could not be stabilized")
                    return False
                _start_mine_process(client, ORES[ore_type], count + 2)
                idle_checks = 0
                stalled_checks = 0
                continue
            
            if not is_pathing:
                idle_checks += 1
            else:
                idle_checks = 0

            total = count_item(client, drop_item)
            if total >= count:
                client.transport.dispatch("cancel", {})
                return True

            if total == last_total:
                stalled_checks += 1
            else:
                last_total = total
                stalled_checks = 0

            if idle_checks >= 3 or stalled_checks >= 5:
                print(
                    f"DEBUG: {ore_type} gathering stalled; "
                    "trying exact nearby ore..."
                )
                _serialized_dispatch(
                    client,
                    "cancel",
                    {},
                    post_delay_seconds=_BARITONE_CANCEL_GRACE_SECONDS,
                )
                fallback_radius = _ORE_FALLBACK_RADII[
                    min(direct_failures, len(_ORE_FALLBACK_RADII) - 1)
                ]
                target = _find_safe_nearby_ore(
                    client,
                    ORES[ore_type],
                    radius=fallback_radius,
                    excluded_positions=failed_targets,
                )
                if target and _approach_and_break_stone(client, target):
                    print(f"DEBUG: Broke nearby {ore_type} ore at {target}")
                    direct_failures = 0
                    failed_targets = set()
                    idle_checks = 0
                    stalled_checks = 0
                    continue

                direct_failures += 1
                if target:
                    failed_targets.add(target)
                if direct_failures >= _ORE_FALLBACK_LIMIT:
                    print(
                        f"DEBUG: No reachable nearby {ore_type} ore after "
                        f"{_ORE_FALLBACK_LIMIT} exact attempts"
                    )
                    return False
                _serialized_dispatch(
                    client,
                    "mine",
                    {"blocks": ORES[ore_type], "quantity": count + 2},
                    post_delay_seconds=_BARITONE_MINE_SETUP_SECONDS,
                )
                idle_checks = 0
                stalled_checks = 0
            time.sleep(3) # Reduced sleep to scan more often
        client.transport.dispatch("cancel", {})
        return False
    except Exception as exc:
        print(f"Ore gathering error: {exc}")
        return False


def go_to_y_level(client, y: int, timeout: int = 300) -> bool:
    """Carve and walk a verified one-block-at-a-time staircase."""
    try:
        settings = [
            "#set allowBreak true",
            "#set allowPlace true",
            "#set allowParkour false",
            "#set allowParkourAscend false",
            "#set allowParkourPlace false",
            "#set allowDownward false",
            "#set allowWaterBucketFall false",
            "#set maxFallHeightNoWater 3",
            "#set maxFallHeightBucket 3",
        ]
        for s in settings:
            _serialized_dispatch(client, "chat", {"message": s}, post_delay_seconds=0.1)
        
        overall_start = time.time()
        preferred_direction = 0
        directions = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        air_blocks = {"", "minecraft:air", "minecraft:cave_air", "minecraft:void_air"}
        unsafe_blocks = {
            "minecraft:water", "minecraft:lava", "minecraft:bedrock",
            "minecraft:gravel", "minecraft:sand", "minecraft:red_sand",
            "minecraft:magma_block", "minecraft:pointed_dripstone",
            "minecraft:spawner", "minecraft:chest", "minecraft:trapped_chest",
            "minecraft:barrel", "minecraft:furnace", "minecraft:crafting_table",
        }

        def block_id(x: int, by: int, z: int) -> str:
            return str(
                client.transport.dispatch(
                    "get_block", {"x": x, "y": by, "z": z}
                ).get("id", "")
            )

        def break_for_step(x: int, by: int, z: int) -> bool:
            current = block_id(x, by, z)
            if current in air_blocks:
                return True
            if current in unsafe_blocks:
                return False
            if not _ensure_mining_pickaxe(client):
                return False
            response = _serialized_dispatch(
                client,
                "break_block",
                {"x": x, "y": by, "z": z},
                post_delay_seconds=_BARITONE_MINE_SETUP_SECONDS,
            )
            if response.get("error"):
                return False
            deadline = time.monotonic() + 12.0
            while time.monotonic() < deadline:
                if block_id(x, by, z) in air_blocks:
                    # The bridge implements exact block breaking through a
                    # short-lived Baritone build process.  The world block can
                    # become air before that process is removed from Baritone's
                    # tick list.  Explicit cancellation plus a cleanup grace
                    # prevents the next head/foot break from mutating that list
                    # while the render thread is still iterating it.
                    _serialized_dispatch(
                        client,
                        "cancel",
                        {},
                        post_delay_seconds=_BARITONE_BUILD_CLEANUP_SECONDS,
                    )
                    return True
                state = client.transport.dispatch("get_state", {})
                if state.get("is_dead", False) or float(state.get("health", 20) or 0) <= 0:
                    return False
                time.sleep(0.25)
            _serialized_dispatch(
                client,
                "cancel",
                {},
                post_delay_seconds=_BARITONE_BUILD_CLEANUP_SECONDS,
            )
            return False

        while time.time() - overall_start < timeout:
            state = client.transport.dispatch("get_state", {})
            pos = state.get("block_position", state.get("position", {}))
            px, current_y, pz = int(pos.get("x", 0)), int(pos.get("y", 64)), int(pos.get("z", 0))

            if state.get("is_dead", False) or float(state.get("health", 20) or 0) <= 0:
                print("Y navigation aborted: player is dead")
                return False

            food_level = int(state.get("food_level", state.get("food", 20)))
            if food_level <= 10:
                from .combat import eat_until_hunger
                client.transport.dispatch("cancel", {})
                if not eat_until_hunger(client, minimum_food=14):
                    print("Y navigation aborted: could not restore hunger")
                    return False
            
            if current_y <= y + 3:
                print(f"DEBUG: Reached target Y={y}!")
                client.transport.dispatch("cancel", {})
                return True

            step_succeeded = False
            for offset in range(len(directions)):
                direction_index = (preferred_direction + offset) % len(directions)
                dx, dz = directions[direction_index]
                target_x, target_y, target_z = px + dx, current_y - 1, pz + dz
                floor_id = block_id(target_x, target_y - 1, target_z)
                head_id = block_id(target_x, target_y + 1, target_z)
                foot_id = block_id(target_x, target_y, target_z)

                # A real staircase always has a verified solid floor.  If the
                # candidate opens into a cave, rotate instead of stepping into
                # a drop and calling that downward progress.
                if floor_id in air_blocks or floor_id in unsafe_blocks:
                    continue
                if head_id in unsafe_blocks or foot_id in unsafe_blocks:
                    continue

                print(
                    f"DEBUG: Stair step Y={current_y}->{target_y} at "
                    f"({target_x}, {target_y}, {target_z})"
                )
                # Clear head space before the foot block so falling gravel or
                # a newly exposed cavity cannot push the player down early.
                if not break_for_step(target_x, target_y + 1, target_z):
                    continue
                if not break_for_step(target_x, target_y, target_z):
                    continue
                if block_id(target_x, target_y - 1, target_z) in air_blocks:
                    continue

                _serialized_dispatch(
                    client,
                    "goto",
                    {"x": target_x, "y": target_y, "z": target_z},
                    post_delay_seconds=_BARITONE_MINE_SETUP_SECONDS,
                )
                move_deadline = time.monotonic() + 8.0
                starting_health = float(state.get("health", 20) or 0)
                while time.monotonic() < move_deadline:
                    moved = client.transport.dispatch("get_state", {})
                    moved_pos = moved.get("block_position", moved.get("position", {}))
                    moved_y = int(moved_pos.get("y", current_y))
                    moved_health = float(moved.get("health", starting_health) or 0)
                    if (
                        moved.get("is_dead", False)
                        or moved_health <= 0
                        or moved_health < starting_health - 4
                        or moved_y < target_y - 1
                    ):
                        _serialized_dispatch(
                            client,
                            "cancel",
                            {},
                            post_delay_seconds=_BARITONE_CANCEL_GRACE_SECONDS,
                        )
                        print("Y navigation safety abort during one-block step")
                        return False
                    if moved_y <= target_y:
                        _serialized_dispatch(
                            client,
                            "cancel",
                            {},
                            post_delay_seconds=_BARITONE_CANCEL_GRACE_SECONDS,
                        )
                        preferred_direction = direction_index
                        step_succeeded = True
                        break
                    time.sleep(0.25)
                if step_succeeded:
                    break

            if not step_succeeded:
                _serialized_dispatch(
                    client,
                    "cancel",
                    {},
                    post_delay_seconds=_BARITONE_CANCEL_GRACE_SECONDS,
                )
                print(f"Y navigation stalled: no safe staircase step from Y={current_y}")
                return False

            time.sleep(0.25)
        
        client.transport.dispatch("cancel", {})
        return False
    except Exception as exc:
        print(f"Y level navigation error: {exc}")
        return False



def gather_gravel(client, count: int, timeout: int = 300) -> bool:
    """Gather gravel until specific amount of flint is obtained."""
    try:
        # Request mining a lot of gravel (10% drop rate for flint)
        client.transport.dispatch("mine", {"blocks": ["minecraft:gravel"], "quantity": count * 20})
        start = time.time()
        while time.time() - start < timeout:
            total = count_item(client, "minecraft:flint")
            if total >= count:
                client.transport.dispatch("cancel", {})
                return True
            time.sleep(3)
        client.transport.dispatch("cancel", {})
        return False
    except Exception as exc:
        print(f"Gravel gathering error: {exc}")
        return False


def gather_water(client, count: int = 1, timeout: int = 60) -> bool:
    """Find water and fill bucket."""
    try:
        # Check if we have a bucket
        if count_item(client, "minecraft:bucket") < 1:
             # Just fail, let the strategy handle crafting if it strictly needs water bucket
             # But usually ensuring "water_bucket" implies crafting "bucket" first which is handled by ensure_dependencies?
             # Actually ensure_supplies handles strict items. If we need water_bucket, we call this.
             pass

        # Find water
        # Search for water block
        water_pos = find_nearby_block(client, ["minecraft:water"], radius=32)
        if not water_pos:
            print("No water found nearby")
            return False
            
        # Go to water
        goto(client, water_pos[0], water_pos[1], water_pos[2], tolerance=2)
        
        # Select bucket
        if not select_item(client, "minecraft:bucket"):
            print("No empty bucket to fill")
            return False
            
        # Interact with water
        client.transport.dispatch("look_at", {"x": water_pos[0], "y": water_pos[1], "z": water_pos[2]})
        time.sleep(0.5)
        client.transport.dispatch("use_item", {}) # Right click
        time.sleep(0.5)
        
        return count_item(client, "minecraft:water_bucket") >= count
    except Exception as exc:
        print(f"Water gathering error: {exc}")
        return False


def _default_mine(client, block_id: str, quantity: int) -> bool:
    try:
        client.transport.dispatch("mine", {"blocks": [block_id], "quantity": max(1, quantity)})
        return True
    except Exception as exc:
        print(f"Mine command failed for {block_id}: {exc}")
        return False


def _wait_for_path_completion(
    client,
    timeout: float,
    start_grace: float = 3.0,
) -> bool:
    """Wait for a chat-dispatched Baritone path without a blind fixed sleep."""
    deadline = time.monotonic() + timeout
    grace_deadline = time.monotonic() + start_grace
    observed_pathing = False
    while time.monotonic() < deadline:
        state = client.transport.dispatch("get_state", {})
        if state.get("is_pathing", False):
            observed_pathing = True
        elif observed_pathing:
            return True
        elif time.monotonic() >= grace_deadline:
            # The goal may already be within arrival tolerance, or the named
            # waypoint may not exist.  Either way the caller must verify the
            # expected block instead of sleeping for the whole timeout.
            return True
        time.sleep(0.5)
    client.transport.dispatch("cancel", {})
    return False


def _craft_with_table(client, item_id: str, qty: int) -> bool:
    """Craft with crafting table - opens table first if needed."""
    from .base import open_crafting_table, place_crafting_table
    from .automation_utils import get_player_pos
    
    # Early exit if we already have the item
    if count_item(client, item_id) >= qty:
        return True

    # Tool recipes depend on sticks, which are made in the player 2x2 grid.
    # Prepare them before opening the crafting table; otherwise the manual
    # fallback sees planks but no sticks and loops forever.
    if not ensure_tool_sticks(client, item_id, qty):
        print(f"  Failed to prepare stick dependency for {item_id}")
        return False
    
    # Try to open existing crafting table nearby
    if not open_crafting_table(client):
        # Named crafting-table waypoints are mutable and can outlive the block
        # they described.  A live run followed one outside at night and died.
        # Stay local and repair/place a verified workstation instead.
        print("  No verified nearby crafting table; preparing a local one...")

        # No nearby table and waypoint failed, try to place one.
        if count_item(client, "minecraft:crafting_table") == 0:
            # Ensure we have planks
            # Check for any planks first
            total_planks = sum(count_item(client, p) for p in [
                "minecraft:oak_planks", "minecraft:birch_planks", "minecraft:spruce_planks",
                "minecraft:dark_oak_planks", "minecraft:acacia_planks", "minecraft:jungle_planks",
                "minecraft:mangrove_planks", "minecraft:cherry_planks"
            ])

            if total_planks < 4:
                # Check for logs
                total_logs = sum(count_item(client, b) for b in LOG_BLOCKS)
                if total_logs > 0:
                    # Find which log we have
                    for log in LOG_BLOCKS:
                        if count_item(client, log) > 0:
                            print(f"  Crafting planks from {log} for crafting table...")
                            plank_type = log.replace("_log", "_planks").replace("_wood", "_planks")
                            craft(client, plank_type, 1)
                            time.sleep(0.5)
                            break

            # Craft crafting table first (2x2 recipe)
            craft(client, "minecraft:crafting_table", 1)
            time.sleep(0.5)
        
        # Get player position for placement
        pos = get_player_pos(client)
        if not pos:
            print(f"  Cannot get position to place crafting table")
            return False
        
        px, py, pz = int(pos[0]), int(pos[1]), int(pos[2])
        
        # Try a few spots around the player
        found_spot = False
        # Try spots with Y variations too (player might be underground)
        offsets = [
             # Same Y level
             (2, 0, 0), (-2, 0, 0), (0, 0, 2), (0, 0, -2),
             (1, 0, 1), (-1, 0, -1), (1, 0, -1), (-1, 0, 1),
             # One block up (more likely to be air if underground)
             (1, 1, 0), (-1, 1, 0), (0, 1, 1), (0, 1, -1),
             (1, 1, 1), (-1, 1, -1),
             # One block down
             (1, -1, 0), (-1, -1, 0),
        ]
        
        found_spot = False
        table_pos = None
        max_attempts = 8  # Increased since we have more spots to try
        attempts = 0
        
        for dx, dy, dz in offsets:
            if attempts >= max_attempts:
                print(f"  Max placement attempts ({max_attempts}) reached")
                break
                
            tx, ty, tz = px + dx, py + dy, pz + dz
            
            # Check if spot is air or replaceable first
            try:
                block_result = client.transport.dispatch("get_block", {"x": tx, "y": ty, "z": tz})
                block_id = block_result.get("id", "")
                if block_id not in ["minecraft:air", "minecraft:cave_air", "minecraft:short_grass", "minecraft:tall_grass", ""]:
                    print(f"  Skip ({tx}, {ty}, {tz}) - occupied by {block_id}")
                    continue  # Skip this spot, it's not air
            except Exception as e:
                print(f"  Block check failed: {e}")
                # Continue anyway if check fails
            
            print(f"  Attempting to place crafting table at ({tx}, {ty}, {tz})...")
            attempts += 1
            if place_crafting_table(client, tx, ty, tz):
                found_spot = True
                table_pos = (tx, ty, tz)
                print(f"  Crafting table placed at ({tx}, {ty}, {tz})!")
                break  # Stop trying other spots once placed!
        
        if not found_spot:
            # Fallback: mine out a specific nearby block using attack_block
            print(f"  All spots occupied - mining a space with attack_block...")
            fallback_pos = (px + 1, py, pz)
            try:
                # Use attack_block to directly break the target block
                for _ in range(10):  # Multiple attempts to break the block
                    client.transport.dispatch("attack_block", {
                        "x": fallback_pos[0],
                        "y": fallback_pos[1],
                        "z": fallback_pos[2]
                    })
                    time.sleep(0.3)
                
                time.sleep(2)  # Wait for block to fully break and drop
                
                # Now try to place at the mined spot
                print(f"  Attempting placement at mined spot {fallback_pos}...")
                if place_crafting_table(client, fallback_pos[0], fallback_pos[1], fallback_pos[2]):
                    found_spot = True
                    table_pos = fallback_pos
                    print(f"  Crafting table placed at fallback {fallback_pos}!")
            except Exception as e:
                print(f"  Mining fallback failed: {e}")
        
        if not found_spot:
            # Last resort: go to surface where there's more space
            print(f"  Failed underground - trying to go to surface...")
            client.transport.dispatch("chat", {"message": "#surface"})
            _wait_for_path_completion(client, timeout=120.0)
            # Try again on surface
            state = client.transport.dispatch("get_state", {})
            pos = state.get("block_position", state.get("position", {}))
            surface_x, surface_y, surface_z = int(pos.get("x", 0)), int(pos.get("y", 64)), int(pos.get("z", 0))
            print(f"  At surface Y={surface_y}, trying placement...")
            if place_crafting_table(client, surface_x + 1, surface_y, surface_z):
                found_spot = True
                table_pos = (surface_x + 1, surface_y, surface_z)
                print(f"  Crafting table placed at surface {table_pos}!")
        
        if not found_spot:
            print(f"  Failed to place crafting table for {item_id} (all spots occupied)")
            return False
        
        # Now try to open the table we just placed
        if table_pos:
            # Save as POI/waypoint for future use
            print(f"  Saving crafting table as waypoint at {table_pos}...")
            client.transport.dispatch("chat", {"message": f"#waypoint save crafting_table {table_pos[0]} {table_pos[1]} {table_pos[2]}"})
            
            # FIX: Blacklist to prevent breaking
            print("  Safeguard: Blacklisting crafting tables from mining...")
            client.transport.dispatch("chat", {"message": "#blacklist minecraft:crafting_table"})
            
            # Step back
            px, py, pz = table_pos
            print("  Stepping back from table...")
            client.transport.dispatch("goto", {"x": px+1, "y": py, "z": pz}) 
            time.sleep(1.0)
            
            if not open_crafting_table(client, table_pos[0], table_pos[1], table_pos[2]):
                print(f"  Warning: Placed table but failed to open at {table_pos}")
    
    # Now craft
    try:
        result = craft(client, item_id, qty)
        time.sleep(0.3)
        return result
    except Exception as e:
        print(f"  Crafting failed for {item_id}: {e}")
        return False


DEFAULT_REQUIREMENT_STRATEGIES: Dict[str, Callable[[Any, int], bool]] = {
    "minecraft:oak_log": lambda client, qty: gather_wood(client, count=max(qty, 16)),
    "minecraft:cobblestone": lambda client, qty: gather_stone(client, count=max(qty, 16)),
    "minecraft:iron_ingot": lambda client, qty: _smelt_requirement_shortfall(client, "minecraft:iron_ingot", qty),
    "minecraft:diamond": lambda client, qty: gather_ores(client, "diamond", count=max(qty, 4)),
    "minecraft:gold_ingot": lambda client, qty: _smelt_requirement_shortfall(client, "minecraft:gold_ingot", qty),
    "minecraft:obsidian": lambda client, qty: _default_mine(client, "minecraft:obsidian", qty),
    "minecraft:crafting_table": lambda client, qty: craft(client, "minecraft:crafting_table", qty) or True,
    "minecraft:furnace": lambda client, qty: _craft_with_table(client, "minecraft:furnace", qty),
    "minecraft:chest": lambda client, qty: _craft_with_table(client, "minecraft:chest", qty),
    # Early game wooden tools (2x2 crafting)
    "minecraft:stick": lambda client, qty: (client.transport.dispatch("close_screen", {}), craft(client, "minecraft:stick", max(qty, 4)), time.sleep(1)),
    "minecraft:oak_planks": lambda client, qty: (client.transport.dispatch("close_screen", {}), craft(client, "minecraft:oak_planks", qty), time.sleep(1)),
    # Wooden tools (3x3 crafting table required)
    "minecraft:wooden_pickaxe": lambda client, qty: _craft_with_table(client, "minecraft:wooden_pickaxe", qty),
    "minecraft:wooden_sword": lambda client, qty: _craft_with_table(client, "minecraft:wooden_sword", qty),
    "minecraft:wooden_axe": lambda client, qty: _craft_with_table(client, "minecraft:wooden_axe", qty),
    "minecraft:wooden_shovel": lambda client, qty: _craft_with_table(client, "minecraft:wooden_shovel", qty),
    # Stone tools
    "minecraft:stone_pickaxe": lambda client, qty: (client.transport.dispatch("close_screen", {}), _craft_with_table(client, "minecraft:stone_pickaxe", qty)),
    "minecraft:stone_sword": lambda client, qty: (client.transport.dispatch("close_screen", {}), _craft_with_table(client, "minecraft:stone_sword", qty)),
    "minecraft:stone_axe": lambda client, qty: (client.transport.dispatch("close_screen", {}), _craft_with_table(client, "minecraft:stone_axe", qty)),
    "minecraft:stone_shovel": lambda client, qty: (client.transport.dispatch("close_screen", {}), _craft_with_table(client, "minecraft:stone_shovel", qty)),
    "minecraft:iron_pickaxe": lambda client, qty: _craft_with_table(client, "minecraft:iron_pickaxe", qty),
    "minecraft:iron_sword": lambda client, qty: _craft_with_table(client, "minecraft:iron_sword", qty),
    "minecraft:iron_axe": lambda client, qty: _craft_with_table(client, "minecraft:iron_axe", qty),
    "minecraft:iron_shovel": lambda client, qty: _craft_with_table(client, "minecraft:iron_shovel", qty),
    "minecraft:iron_helmet": lambda client, qty: _craft_with_table(client, "minecraft:iron_helmet", qty),
    "minecraft:iron_chestplate": lambda client, qty: _craft_with_table(client, "minecraft:iron_chestplate", qty),
    "minecraft:iron_leggings": lambda client, qty: _craft_with_table(client, "minecraft:iron_leggings", qty),
    "minecraft:iron_boots": lambda client, qty: _craft_with_table(client, "minecraft:iron_boots", qty),
    "minecraft:diamond_pickaxe": lambda client, qty: _craft_with_table(client, "minecraft:diamond_pickaxe", qty),
    "minecraft:diamond_sword": lambda client, qty: _craft_with_table(client, "minecraft:diamond_sword", qty),
    "minecraft:bow": lambda client, qty: _craft_with_table(client, "minecraft:bow", qty),
    "minecraft:arrow": lambda client, qty: _craft_with_table(client, "minecraft:arrow", max(qty, 32)),
    "minecraft:string": lambda client, qty: hunt_mobs(client, ["spider", "cave_spider"], {"minecraft:string": qty}, search_radius=64, timeout=300).success,
    "minecraft:feather": lambda client, qty: hunt_mobs(client, ["chicken"], {"minecraft:feather": qty}, search_radius=50, timeout=300).success,
    "minecraft:flint": lambda client, qty: gather_gravel(client, count=qty),
    "minecraft:shield": lambda client, qty: _craft_with_table(client, "minecraft:shield", qty),
    "minecraft:bucket": lambda client, qty: _craft_with_table(client, "minecraft:bucket", qty),
    "minecraft:water_bucket": lambda client, qty: gather_water(client, count=qty),
}


def manage_inventory(client):
    """Check if inventory is full and drop junk items if needed."""
    from .inventory import is_full, drop_items
    if not is_full(client):
        return

    print("  Inventory full! Clearing junk...")
    # Items to drop (keep cobble/deepslate if < 64? simplied: just drop non-essential)
    # Always drop purely junk blocks
    junk = ["minecraft:dirt", "minecraft:gravel", "minecraft:diorite", "minecraft:andesite", "minecraft:granite", "minecraft:tuff"]
    drop_items(client, junk)
    
    # If still full, drop excess stone/deepslate but try to keep some?
    # For now, if really full, just drop them. We can always mine more.
    if is_full(client):
         print("  Still full, dropping stone/deepslate...")
         drop_items(client, ["minecraft:cobblestone", "minecraft:deepslate", "minecraft:cobbled_deepslate"])


def _select_furnace_fuel(client, smelt_count: int) -> Optional[str]:
    """Return one carried fuel stack that can finish the requested batch."""
    needed = max(1, int(smelt_count))
    preferred = ["minecraft:coal", "minecraft:charcoal", *PLANK_ITEMS]
    # Logs are a last resort because converting them to planks multiplies
    # their useful furnace time by four.
    preferred.extend(LOG_TO_PLANKS)
    for item_id in preferred:
        burn = FURNACE_FUEL_SMELTS[item_id]
        required_items = math.ceil(needed / burn)
        if count_item(client, item_id) >= required_items:
            return item_id
    return None


def _prepare_safe_furnace_fuel(client, smelt_count: int) -> Optional[str]:
    """Prepare furnace fuel without sending an unarmored bot into a cave.

    Existing coal or charcoal is used first.  Otherwise the bot gathers a
    small daylight wood reserve and converts it into the much more efficient
    plank fuel.  ``gather_wood`` enforces its own daylight boundary.
    """
    fuel_id = _select_furnace_fuel(client, smelt_count)
    if fuel_id is not None:
        return fuel_id

    # The smelter may already have opened a nearby furnace while locating it.
    # Close every container before any pathing or player-grid craft starts.
    try:
        client.transport.dispatch("close_screen", {})
    except Exception:
        pass
    manage_inventory(client)
    required_planks = math.ceil(max(1, int(smelt_count)) / 1.5)
    current_planks = sum(count_item(client, item_id) for item_id in PLANK_ITEMS)
    # gather_wood treats four existing planks as one log equivalent, so this
    # absolute target accounts for both carried logs and carried planks.
    required_log_equivalents = math.ceil(required_planks / 4)
    print(
        f"  Preparing safe wood fuel ({current_planks}/{required_planks} planks, "
        f"target {required_log_equivalents} log equivalents)..."
    )
    if not gather_wood(
        client,
        count=required_log_equivalents,
        timeout=300,
    ):
        print("  Could not gather daylight wood fuel; refusing cave coal search.")
        return None

    client.transport.dispatch("close_screen", {})
    for log_id, plank_id in LOG_TO_PLANKS.items():
        if sum(count_item(client, item_id) for item_id in PLANK_ITEMS) >= required_planks:
            break
        log_count = count_item(client, log_id)
        if log_count <= 0:
            continue
        missing_planks = required_planks - sum(
            count_item(client, item_id) for item_id in PLANK_ITEMS
        )
        # Ask for an output count.  The verified craft wrapper accepts recipe
        # batches and succeeds once the plank-family inventory reaches it.
        craft_output = min(log_count * 4, missing_planks)
        if not craft(client, plank_id, craft_output):
            print(f"  Could not convert {log_id} into furnace fuel.")
            return None

    fuel_id = _select_furnace_fuel(client, smelt_count)
    if fuel_id is None:
        print("  Wood fuel preparation finished below the required burn time.")
    return fuel_id


def resume_active_furnace(
    client,
    furnace_pos,
    input_item: str,
    output_item: str,
    timeout: float = 600.0,
) -> bool:
    """Drain a previously loaded furnace and collect every finished item.

    An interrupted controller can leave all ore and fuel in the block entity.
    Those stacks disappear from player inventory, so treating ``raw == 0`` as
    completed work sends the next run mining again.  Return ``True`` only when
    matching pending/output work was found and fully drained.
    """
    from . import harness_ops

    try:
        opened = harness_ops.available() and harness_ops.open_container(
            client, tuple(furnace_pos), timeout=4.0
        )
    except Exception as exc:
        print(f"  Could not inspect loaded furnace: {exc}")
        opened = False
    if not opened:
        return False

    def furnace_slots():
        screen = client.transport.dispatch("get_screen", {})
        data = screen.get("data", screen)
        by_slot = {
            int(slot.get("slot", -1)): slot for slot in data.get("slots", [])
        }
        return data, by_slot

    data, slots = furnace_slots()
    input_slot = slots.get(0, {})
    output_slot = slots.get(2, {})
    had_work = (
        input_slot.get("id") == input_item
        or output_slot.get("id") == output_item
    )
    if not had_work:
        client.transport.dispatch("close_screen", {})
        return False

    initial_input = int(input_slot.get("count", 0))
    deadline = time.monotonic() + min(
        timeout, max(20.0, initial_input * 10.5 + 30.0)
    )
    empty_polls = 0
    print(
        f"  Resuming loaded furnace at {tuple(furnace_pos)} "
        f"({initial_input} {input_item} pending)..."
    )

    while time.monotonic() < deadline:
        data, slots = furnace_slots()
        output_slot = slots.get(2, {})
        if (
            output_slot.get("id") == output_item
            and int(output_slot.get("count", 0)) > 0
        ):
            payload = {"slot": 2, "type": "QUICK_MOVE", "button": 0}
            sync_id = data.get("sync_id")
            if sync_id is not None:
                payload["sync_id"] = sync_id
            client.transport.dispatch("inventory_click", payload)
            empty_polls = 0
            time.sleep(0.2)
            continue

        input_slot = slots.get(0, {})
        input_empty = (
            input_slot.get("id") in (None, "minecraft:air")
            or int(input_slot.get("count", 0)) <= 0
        )
        if input_empty:
            empty_polls += 1
            if empty_polls >= 2:
                client.transport.dispatch("close_screen", {})
                print("  Loaded furnace batch is fully collected.")
                return True
        else:
            empty_polls = 0
            fuel_slot = slots.get(1, {})
            block = client.transport.dispatch(
                "get_block",
                {
                    "x": int(furnace_pos[0]),
                    "y": int(furnace_pos[1]),
                    "z": int(furnace_pos[2]),
                },
            )
            lit = str(block.get("state", {}).get("lit", "false")).lower() == "true"
            fuel_empty = (
                fuel_slot.get("id") in (None, "minecraft:air")
                or int(fuel_slot.get("count", 0)) <= 0
            )
            if not lit and fuel_empty:
                client.transport.dispatch("close_screen", {})
                print("  Loaded furnace stalled without fuel.")
                return False
        time.sleep(0.5)

    client.transport.dispatch("close_screen", {})
    print("  Timed out while resuming loaded furnace.")
    return False


def _smelt_with_furnace(
    client,
    item_id: str,
    qty: int,
    furnace_pos: Optional[tuple[int, int, int]] = None,
) -> bool:
    """Smelt an absolute output target using verified nearby infrastructure."""
    from .base import place_furnace, open_furnace
    from .automation_utils import get_player_pos

    # Map desired output to input
    input_item = "minecraft:raw_iron"
    if item_id == "minecraft:gold_ingot":
        input_item = "minecraft:raw_gold"

    # Check what we already have
    current = count_item(client, item_id)
    needed = qty - current
    if needed <= 0:
        return True

    # Gather input if missing
    if count_item(client, input_item) < needed:
        ore = "iron" if "iron" in input_item else "gold"
        if not gather_ores(client, ore, count=needed):
            return False
    if count_item(client, input_item) < needed:
        return False

    # Fuel preparation may leave the house.  Complete it before locating or
    # placing a furnace so Baritone cannot break the workstation while trying
    # to escape a cramped room.
    fuel_id = _prepare_safe_furnace_fuel(client, needed)
    if fuel_id is None:
        return False

    try:
        client.transport.dispatch("close_screen", {})
    except Exception:
        pass

    furnace_blocks = ["minecraft:furnace", "minecraft:blast_furnace"]
    if furnace_pos is not None:
        try:
            provided = (
                int(furnace_pos[0]),
                int(furnace_pos[1]),
                int(furnace_pos[2]),
            )
        except (TypeError, ValueError):
            provided = None
        else:
            verified_block = client.transport.dispatch(
                "get_block",
                {"x": provided[0], "y": provided[1], "z": provided[2]},
            ).get("id", "")
            if "furnace" in verified_block:
                fpos = provided
            else:
                fpos = None
    else:
        fpos = None

    if fpos is None:
        fpos = find_nearby_block(client, furnace_blocks, radius=10)
    if fpos is None:
        if count_item(client, "minecraft:furnace") == 0:
            if count_item(client, "minecraft:cobblestone") < 8:
                if not gather_stone(client, count=8):
                    return False
            if not craft(client, "minecraft:furnace", 1):
                return False

        print("  Placing furnace after fuel gathering is complete...")
        pos = get_player_pos(client)
        px, py, pz = int(pos[0]), int(pos[1]), int(pos[2])
        offsets = [
            (1, 0, 0),
            (-1, 0, 0),
            (0, 0, 1),
            (0, 0, -1),
            (1, 1, 0),
            (-1, 1, 0),
            (0, 1, 1),
            (0, 1, -1),
        ]
        for dx, dy, dz in offsets:
            if place_furnace(client, px + dx, py, pz + dz):
                break
        fpos = find_nearby_block(client, furnace_blocks, radius=10)
    if fpos is None:
        print("  No verified furnace block is available for smelting.")
        return False

    # Preferred: harness smelter (loads furnace, waits per item, verifies)
    from . import harness_ops
    state = client.transport.dispatch("get_state", {})
    position = state.get("block_position", state.get("position", {}))
    distance = sum(
        (float(position.get(axis, 0)) - float(coordinate)) ** 2
        for axis, coordinate in zip(("x", "y", "z"), fpos)
    ) ** 0.5
    if distance > 4.5:
        if not harness_ops.available() or not harness_ops.move_near(
            client,
            int(fpos[0]),
            int(fpos[1]),
            int(fpos[2]),
            timeout=30.0,
        ):
            print(f"  Could not move beside furnace at {fpos}.")
            return False
    # Never issue a generic goto for the furnace coordinate itself: Baritone
    # treats the solid goal block as an obstruction and mines the workstation.
    verified_block = client.transport.dispatch(
        "get_block",
        {"x": int(fpos[0]), "y": int(fpos[1]), "z": int(fpos[2])},
    ).get("id", "")
    if "furnace" not in verified_block:
        print(f"  Furnace disappeared before interaction (found {verified_block}).")
        return False

    if harness_ops.available():
        try:
            if harness_ops.smelt_in_furnace(
                client,
                tuple(fpos),
                input_item,
                fuel_id,
                item_id,
                output_count=needed,
            ):
                return count_item(client, item_id) >= qty
            print("  Harness smelt fell short; trying native furnace flow...")
        except Exception as e:
            print(f"  Harness smelt failed (falling back): {e}")

    # Native fallback uses the live container-slot list rather than raw player
    # indices, then collects partial output throughout the burn.
    if not open_furnace(client):
        return False
    screen = client.transport.dispatch("get_screen", {})
    data = screen.get("data", screen)
    slots = data.get("slots", [])
    fuel_slot = next(
        (
            int(slot["slot"])
            for slot in slots
            if int(slot.get("slot", -1)) >= 3 and slot.get("id") == fuel_id
        ),
        None,
    )
    input_slot = next(
        (
            int(slot["slot"])
            for slot in slots
            if int(slot.get("slot", -1)) >= 3 and slot.get("id") == input_item
        ),
        None,
    )
    if fuel_slot is None or input_slot is None:
        client.transport.dispatch("close_screen", {})
        return False
    client.transport.dispatch(
        "inventory_click",
        {"slot": fuel_slot, "type": "QUICK_MOVE", "button": 0},
    )
    client.transport.dispatch(
        "inventory_click",
        {"slot": input_slot, "type": "QUICK_MOVE", "button": 0},
    )

    deadline = time.monotonic() + max(15.0, needed * 10.5 + 15.0)
    while time.monotonic() < deadline:
        screen = client.transport.dispatch("get_screen", {})
        data = screen.get("data", screen)
        output = next(
            (slot for slot in data.get("slots", []) if slot.get("slot") == 2),
            None,
        )
        if output and output.get("id") == item_id and output.get("count", 0) > 0:
            client.transport.dispatch(
                "inventory_click",
                {"slot": 2, "type": "QUICK_MOVE", "button": 0},
            )
        if count_item(client, item_id) >= qty:
            client.transport.dispatch("close_screen", {})
            return True
        time.sleep(1.0)

    client.transport.dispatch("close_screen", {})
    return count_item(client, item_id) >= qty


def _smelt_requirement_shortfall(
    client,
    item_id: str,
    shortfall: int,
    furnace_pos: Optional[tuple[int, int, int]] = None,
) -> bool:
    """Translate ensure_supplies' shortfall into the smelter's absolute target."""
    target = count_item(client, item_id) + max(0, int(shortfall))
    if furnace_pos is None:
        return _smelt_with_furnace(client, item_id, target)
    return _smelt_with_furnace(client, item_id, target, furnace_pos=furnace_pos)


# Update strategies to use new smelting function
DEFAULT_REQUIREMENT_STRATEGIES["minecraft:iron_ingot"] = (
    lambda client, qty: _smelt_requirement_shortfall(
        client, "minecraft:iron_ingot", qty
    )
)
DEFAULT_REQUIREMENT_STRATEGIES["minecraft:gold_ingot"] = (
    lambda client, qty: _smelt_requirement_shortfall(
        client, "minecraft:gold_ingot", qty
    )
)


def _missing_requirements(client, requirements: Dict[str, int]) -> Dict[str, int]:
    def _count_equivalent_tools(item_id: str) -> int:
        if item_id not in _PICKAXE_TIER_RANK:
            return count_item(client, item_id)

        required_rank = _PICKAXE_TIER_RANK[item_id]
        current = 0
        for candidate_id, candidate_rank in _PICKAXE_TIER_RANK.items():
            if candidate_rank >= required_rank:
                current += count_item(client, candidate_id)
        return current

    missing: Dict[str, int] = {}
    for item_id, required in requirements.items():
        current = _count_equivalent_tools(item_id)
        if current < required:
            missing[item_id] = required - current
    return missing


def ensure_supplies(
    client,
    requirements: Dict[str, int],
    strategies: Optional[Dict[str, Callable[[Any, int], bool]]] = None,
    poll_interval: float = 5.0,
    timeout: int = 600,
) -> TaskResult:
    """
    Ensure the inventory matches the provided requirement list.

    Args:
        client: Baritone client
        requirements: Mapping of item_id -> minimum amount
        strategies: Optional overrides for how to acquire each item
        poll_interval: Seconds to wait between refreshes
        timeout: Maximum duration before giving up
    """
    strategies_map = dict(DEFAULT_REQUIREMENT_STRATEGIES)
    if strategies:
        strategies_map.update(strategies)

    start = time.time()
    attempts = 0
    operations = []

    # Generic requirement checks must not mutate containers.  In particular,
    # the starter-house supply chest is durable storage, not opportunistic
    # loot.  Chest acquisition belongs to explicit, checkpointed objectives
    # that can distinguish owned storage from discovered structures.
    missing = _missing_requirements(client, requirements)
    if not missing:
        return TaskResult.ok("All requirements already satisfied", operations=operations)

    while missing and time.time() - start < timeout:
        # Check inventory space
        manage_inventory(client)
        
        print(f"Ensuring supplies: {missing} (t={time.time()-start:.0f}s/{timeout}s)")
        for item_id, shortfall in list(missing.items()):
            handler = strategies_map.get(item_id)
            if handler is None:
                print(f"  No handler for {item_id}, skipping")
                continue
            attempts += 1
            print(f"  Gathering {item_id} x{shortfall}...")
            handler(client, shortfall)
            operations.append({"item": item_id, "shortfall": shortfall})
        time.sleep(poll_interval)
        missing = _missing_requirements(client, requirements)

    if missing:
        return TaskResult.fail(
            "Missing required supplies",
            missing=missing,
            operations=operations,
            attempts=attempts,
            timeout=timeout,
        )

    return TaskResult.ok(
        "Requirements satisfied",
        operations=operations,
        attempts=attempts,
        duration=time.time() - start,
    )
