"""
Resource gathering utilities - Wood, stone, ores, and materials.
"""

import math
import time
from typing import Any, Callable, Dict, Optional, Set
from .inventory import (
    count_item,
    craft,
    ensure_tool_sticks,
    free_inventory_slots,
    select_item,
)
from . import inventory
_DEFAULT_INVENTORY_ENSURE_RAW_PLANKS = inventory._ensure_raw_planks
from .tasks import PlayerDeathDetected, SurvivalRecoveryRequired, TaskResult
from .furnace_recovery import collect_finished_furnace_output, resume_active_furnace
from .combat import hunt_mobs
from .navigation import find_nearby_block, goto, goto_xz
from .movement_recovery import (
    ExplorationWaypoints,
    MovementWatchdog,
    recover_stalled_gathering,
)
from .mining_safety import run_mining_defense
from .ore_gather_recovery import OreStallRecovery
from .surface_egress import try_lower_surface_egress
from .storage_safety import (
    MAX_STORAGE_TRAVEL_DISTANCE,
    storage_distance,
    store_surplus_in_chest as _store_surplus_in_chest,
)
from .wood_gathering import (
    maintain_survival_window,
    recover_after_aquatic_stop,
    required_log_count,
    stop_reason as wood_gathering_stop_reason,
    stop_reason_after_defense as wood_gathering_stop_reason_after_defense,
)
from .automation_utils import get_player_pos
from ..core.exceptions import CommandError, TransportError
from .mining_acceleration import with_mining_acceleration

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
# Hunger management for long resource loops / deep stair descent.  These values
# keep progression moving in fragile server conditions where a strict 14-barrier
# was stalling bots with safe-but-low food.
_SAFE_MINING_FOOD = 6
_MINING_HUNGER_TARGET = 12
# Minecraft only regenerates health at food >= 18. Below that line a bot that
# takes any damage cannot heal at all, so committing to a deep descent under
# it -- with nothing edible carried to lift back over it -- is a one-way trip
# into a hostile area it can never recover in. See the descent gate below.
_MINING_REGEN_FOOD = 18


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


def _read_state_with_retry(
    client,
    *, retries: int = 3, label: str = "get_state"
) -> tuple[Optional[Dict[str, Any]], int]:
    """Read state with bounded retries, returning ``None`` when all retries fail."""
    attempts = 0
    while attempts < retries:
        attempts += 1
        try:
            return client.transport.dispatch("get_state", {}), attempts
        except TransportError:
            print(
                f"{label}: transport timeout while reading state "
                f"(attempt {attempts}/{retries}); retrying"
            )
            time.sleep(0.5)
        except CommandError as exc:
            error_text = str(exc).lower()
            if "player not available" in error_text or "not connected" in error_text:
                print(
                    f"{label}: state endpoint not ready (attempt {attempts}/{retries}); "
                    "retrying"
                )
                time.sleep(0.5)
            else:
                raise
    return None, attempts


def _read_state_optional(
    client,
    *,
    retries: int = 3,
    label: str = "get_state",
) -> Optional[Dict[str, Any]]:
    """Read state but keep loops moving on transient bridge miss."""
    state, _ = _read_state_with_retry(client, retries=retries, label=label)
    return state


def _find_blocks_optional(
    client,
    payload: Dict[str, Any],
    *,
    retries: int = 3,
    label: str = "find_blocks",
) -> Optional[Dict[str, Any]]:
    """Read-block search with transient retry and non-fatal fallback."""
    attempts = 0
    while attempts < retries:
        attempts += 1
        try:
            return client.transport.dispatch("find_blocks", payload)
        except (TransportError, CommandError) as exc:
            message = str(exc).lower()
            if (
                attempts < retries
                and (
                    isinstance(exc, TransportError)
                    or "player not available" in message
                    or "not connected" in message
                    or "connection lost" in message
                    or "connection reset" in message
                )
            ):
                print(
                    f"{label}: find_blocks failed (attempt {attempts}/{retries}); "
                    f"retrying"
                )
                time.sleep(0.5)
                continue
            print(f"{label}: find_blocks hard-failed: {exc}")
            return None
    return None


def _ensure_mining_pickaxe(client) -> bool:
    """Ensure stone gathering has a live pickaxe, including mid-task wear."""
    if sum(count_item(client, item_id) for item_id in PICKAXE_ITEMS) > 0:
        return True

    # Replacing a broken tool can open a crafting table, place one nearby, or
    # move toward its saved waypoint.  Doing that after dark exposed a live
    # run to a zombie while the crafting screen was open.  Stay at the mining
    # position until daylight before beginning any replacement workflow.
    state, _ = _read_state_with_retry(
        client,
        retries=3,
        label="Pickaxe replacement state check",
    )
    if state is None:
        print("DEBUG: Could not read state while checking replacement-time daylight.")
        return False
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


def equip_best_pickaxe(client, pickaxe_ids: Optional[list[str]] = None) -> bool:
    """Put the pickaxe with the most usable durability into the main hand.

    Ported from the harness's get_best_tool_durability, which measures each
    tool's remaining uses and moves the best one to the hotbar. Production had
    equip_best_weapon and equip_best_armor but no pickaxe equivalent, and
    equip_best_weapon walks a fixed tier list and returns on first match --
    so a nearly-spent diamond pickaxe wins over a fresh iron one, and a
    pickaxe sitting outside the hotbar is never reachable at all.

    Live 2026-08-02: Bot17 failed "Prepare deep-mining tools + bucket"
    repeatedly while carrying two worn iron pickaxes; the kit check measures
    total durability but nothing ever equipped the better tool.
    """
    from .inventory import select_item

    allowed = list(pickaxe_ids or PICKAXE_ITEMS)
    try:
        response = client.transport.dispatch("get_inventory", {})
    except Exception:
        return False
    data = response.get("data", response)

    best = None  # (remaining, tier_rank, item_id)
    for section in ("inventory", "offhand"):
        for item in data.get(section, []) or []:
            item_id = str(item.get("id", ""))
            if item_id not in allowed or int(item.get("count", 0) or 0) <= 0:
                continue
            maximum = int(item.get("max_damage", PICKAXE_MAX_DAMAGE.get(item_id, 0)) or 0)
            remaining = max(0, maximum - max(0, int(item.get("damage", 0) or 0)))
            if remaining <= 0:
                continue
            tier = _PICKAXE_TIERS.index(item_id) if item_id in _PICKAXE_TIERS else -1
            candidate = (remaining, tier, item_id)
            if best is None or candidate > best:
                best = candidate

    if best is None:
        return False
    # select_item routes through the harness hotbar swap, so a pickaxe in the
    # main inventory is moved into a reachable slot rather than silently left.
    return bool(select_item(client, best[2], allow_swap=True))


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
    # A discovered tree is often behind leaves/uneven terrain.  The default
    # Baritone settings may refuse to clear a short approach corridor, making
    # every reachable-looking log appear impossible to harvest.
    client.transport.dispatch("chat", {"message": "#set allowBreak true"})
    if not goto(
        client,
        lx,
        ly,
        lz,
        timeout=90,
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
    state = _read_state_optional(
        client,
        retries=3,
        label="Safe nearby stone state",
    )
    if state is None:
        return None
    position = state.get("block_position", state.get("position", {}))
    px = int(position.get("x", state.get("x", 0)))
    py = int(position.get("y", state.get("y", 0)))
    pz = int(position.get("z", state.get("z", 0)))

    response = _find_blocks_optional(
        client,
        {
            "blocks": ["minecraft:stone", "minecraft:deepslate"],
            "radius": max(1, min(int(radius), 16)),
            "limit": 4096,
        },
        label="Safe nearby stone search",
    )
    if response is None:
        return None
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
    state = _read_state_optional(
        client, retries=3, label="Safe nearby ore state"
    )
    if state is None:
        return None
    position = state.get("block_position", state.get("position", {}))
    px = int(position.get("x", state.get("x", 0)))
    py = int(position.get("y", state.get("y", 0)))
    pz = int(position.get("z", state.get("z", 0)))
    response = _find_blocks_optional(
        client,
        {
            "blocks": block_types,
            "radius": max(1, min(int(radius), 16)),
            "limit": 4096,
        },
        label="Safe nearby ore search",
    )
    if response is None:
        return None
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
    """Gather logs, counting four carried planks as one log equivalent."""
    needed = required_log_count(client, count)
    if needed is None:
        return True
    
    try:
        if not _reserve_gathering_inventory(client):
            return False
        # Check the safety boundary before creating a path.  Starting the mine
        # process first gave Baritone enough time to walk out of the starter
        # house at night before the first loop iteration could cancel it.
        initial_state = _read_state_optional(
            client, retries=3, label="Wood gather initial state"
        )
        if initial_state is None:
            print("DEBUG: Wood gathering could not read initial state; skipping this attempt")
            return False
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
        last_count = count - needed
        stalled_checks = 0
        exploring = False
        failed_log_positions: Set[tuple[int, int, int]] = set()
        movement = MovementWatchdog()
        movement.reset(initial_state)
        exploration_waypoints = ExplorationWaypoints(
            origin_x,
            origin_z,
            maximum_radius=float(max_distance_from_origin or 96.0),
        )
        major_stalls = 0
        # Seconds spent evading/relocating rather than gathering. Defence is
        # not work, and charging it against the gathering budget is what let a
        # single camped mob reduce output to exactly zero -- see the credit
        # site below. Capped at one extra `timeout` so a permanently contested
        # area still terminates instead of looping forever.
        defense_seconds = 0.0
        # One bounded escape attempt per gather; see the marooned branch below.
        # A dedicated watchdog so maroon detection cannot perturb the stall
        # window `recover_stalled_gathering` relies on.
        marooned_egress_attempted = False
        maroon_watch = MovementWatchdog()
        maroon_watch.reset(initial_state)

        while time.time() - start - defense_seconds < timeout:
            if free_inventory_slots(client) < 2:
                client.transport.dispatch("cancel", {})
                if not _reserve_gathering_inventory(client):
                    return False
                _start_mine_process(client, LOG_BLOCKS, needed + 4)
            state = _read_state_optional(client, retries=3, label="Wood gather state")
            if state is None:
                time.sleep(0.5)
                continue
            stop_reason = wood_gathering_stop_reason(
                client,
                state,
                origin_x=origin_x,
                origin_z=origin_z,
                latest_world_time=latest_world_time,
                max_distance_from_origin=max_distance_from_origin,
                minimum_health=minimum_health,
            )
            if stop_reason:
                if not recover_after_aquatic_stop(
                    client,
                    stop_reason,
                    quantity=needed + 4,
                    movement_watchdogs=(movement, maroon_watch),
                ):
                    return False
                idle_checks = stalled_checks = 0
                exploring = False
                continue
            safety_resume = maintain_survival_window(
                client, state, quantity=needed + 4
            )
            if safety_resume is not None:
                if not safety_resume:
                    return False
                idle_checks = 0
                stalled_checks = 0
                exploring = False
                continue

            # Marooned check runs BEFORE defence on purpose. A stranded bot
            # cannot flee, relocate, explore or reach a log -- every recovery
            # silently no-ops -- and when a mob is parked nearby the defence
            # branch below fires on every single iteration and `continue`s,
            # so the idle-path recovery further down is never reached (and
            # defence resets idle_checks to 0 anyway). Live: Bot07 and Bot08
            # sat on the SAME one-block island at (-9, 85, -7) with air on
            # every side, burning ~4000 cancelled actions at wood=0/64 while
            # a creeper camped beside them. Getting off the island is what
            # resolves both problems, so it has to be checked first.
            # Deliberately NOT gated on is_pathing. Baritone reports
            # is_pathing=True while it works through flee goals, so a bot
            # pinned on a pillar looks "busy" the whole time it is going
            # nowhere -- an is_pathing guard here never fired live. Sustained
            # zero displacement is the honest signal. The threshold is high
            # enough (~24s+ of no movement) that normal work, including
            # standing still to break a log, never trips it.
            maroon_watch.observe(state)
            if maroon_watch.stalled(12) and not marooned_egress_attempted:
                marooned_egress_attempted = True
                print(
                    "DEBUG: Idle and stationary; treating as marooned and "
                    "attempting lower-surface egress"
                )
                reached = try_lower_surface_egress(
                    client,
                    state,
                    minimum_altitude=0,
                    allow_upward_excavation=True,
                )
                maroon_watch.reset(state)
                movement.reset(state)
                idle_checks = 0
                stalled_checks = 0
                if reached is not None:
                    _start_mine_process(client, LOG_BLOCKS, needed + 4)
                    exploring = False
                    continue
                print("DEBUG: Lower-surface egress found no way off; continuing")

            # INTEGRATE DEFENSE
            from .combat import defend_or_flee, scan_for_threats
            if abort_on_threats and scan_for_threats(client, radius=12):
                 print("DEBUG: Hostile entered bounded wood-gathering radius")
                 client.transport.dispatch("cancel", {})
                 return False
            if not abort_on_threats:
                defense_started = time.time()
                if defend_or_flee(client):
                    stop_reason = wood_gathering_stop_reason_after_defense(
                        client,
                        origin_x=origin_x,
                        origin_z=origin_z,
                        latest_world_time=latest_world_time,
                        max_distance_from_origin=max_distance_from_origin,
                        minimum_health=minimum_health,
                    )
                    if stop_reason:
                        if not recover_after_aquatic_stop(
                            client,
                            stop_reason,
                            quantity=needed + 4,
                            movement_watchdogs=(movement, maroon_watch),
                        ):
                            return False
                        idle_checks = stalled_checks = 0
                        exploring = False
                        continue
                    # Credit back the time defence consumed. A creeper camped
                    # near the work area triggers evade -> "relocate 28 blocks
                    # away" -> walk back, which costs far more of the budget
                    # than the old short flee did. Live: Bot07/Bot08 burned
                    # every 180s window on 4-5 such cycles and hit
                    # "gather_wood timeout" with 0 logs, every attempt, for a
                    # day -- wood stuck at 0/64 while health stayed 20/20.
                    defense_seconds = min(
                        defense_seconds + (time.time() - defense_started),
                        float(timeout),
                    )
                    print("DEBUG: Wood gathering interrupted by defense logic. Resuming mining...")
                    # Re-issue mine command just in case
                    client.transport.dispatch("mine", {"blocks": LOG_BLOCKS, "quantity": needed + 4})
                    time.sleep(2)
                    idle_checks = 0
                    stalled_checks = 0
                    exploring = False
                    continue

            # Fail Fast: Check if Baritone gave up (is_pathing = False)
            state = _read_state_optional(client, retries=3, label="Wood gather pathing state")
            if state is None:
                time.sleep(0.5)
                continue
            is_pathing = state.get("is_pathing", True)
            movement.observe(state)
            
            # Inventory Progress Tracking
            curr_logs = sum(count_item(client, block) for block in LOG_BLOCKS)
            curr_planks = sum(count_item(client, p) for p in PLANK_ITEMS)
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
                            from .wood_search import start_explicit_wood_search

                            search_result = start_explicit_wood_search(
                                client,
                                log_blocks=LOG_BLOCKS,
                                failed_positions=failed_log_positions,
                                exploration_waypoints=exploration_waypoints,
                                bounded=max_distance_from_origin is not None,
                                find_blocks=_find_blocks_optional,
                                find_nearby=find_nearby_block,
                                approach_and_break=_approach_and_break_log,
                            )
                            if search_result is None:
                                return False
                            exploring = search_result
                            movement.reset(state)
                            idle_checks = 0
                            stalled_checks = 0
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

            if total == last_count:
                stalled_checks += 1
            else:
                stalled_checks = 0
                last_count = total
                major_stalls = 0
                if exploring:
                    print("DEBUG: Found wood during exploration! Cancelling explore and mining...")
                    client.transport.dispatch("cancel", {})
                    client.transport.dispatch("mine", {"blocks": LOG_BLOCKS, "quantity": needed + 4})
                    exploring = False

            # A Baritone process flag is not proof of motion. The old code
            # reset stalled_checks at 10, making its exploration-at-15 branch
            # unreachable forever. Escalate only when both inventory and real
            # displacement are stalled.
            if stalled_checks >= 10 and is_pathing and movement.stalled(4):
                major_stalls, exploring = recover_stalled_gathering(
                    client,
                    exploration_waypoints,
                    state,
                    blocks=LOG_BLOCKS,
                    quantity=needed + 4,
                    major_stalls=major_stalls,
                    exploring=exploring,
                )
                stalled_checks = 0
                movement.reset(state)
                continue
                
            time.sleep(3)
        print("DEBUG: gather_wood timeout")
        client.transport.dispatch("cancel", {})
        return False
    except PlayerDeathDetected:
        raise
    except Exception as exc:
        print(f"Wood gathering error: {exc}")
        return False


def _relocate_to_checkpointed_stone_source(
    client,
    *,
    minimum_y: int = 96,
) -> bool:
    from .stone_descent import relocate_to_checkpointed_stone_source

    return relocate_to_checkpointed_stone_source(client, minimum_y=minimum_y)


def _relocate_to_dry_stone_terrain(client) -> bool:
    from .stone_descent import relocate_to_dry_stone_terrain

    return relocate_to_dry_stone_terrain(client)


def _relocate_to_checkpointed_mine(client) -> bool:
    """Reach this bot's verified mining staircase before scanning again."""
    automation_state = getattr(client, "_automation_state", None)
    if automation_state is None:
        return False
    staircase = automation_state.custom_data.get("shared_mining_staircase", {})
    entrance = staircase.get("entrance")
    if not isinstance(entrance, (list, tuple)) or len(entrance) != 3:
        return False
    target = tuple(int(value) for value in entrance)
    live = client.transport.dispatch("get_state", {})
    if live.get("dimension", "minecraft:overworld") != "minecraft:overworld":
        return False
    print(f"DEBUG: Returning ore gatherer to verified mine entrance at {target}")
    client.transport.dispatch("cancel", {})
    if not goto_xz(client, target[0], target[2], timeout=240, tolerance=6.0):
        return False
    return goto(
        client,
        *target,
        timeout=90,
        check_interval=1.0,
        tolerance=3.0,
    )


def _read_block_optional(client, x: int, y: int, z: int) -> Optional[str]:
    from .stone_descent import read_block_optional

    return read_block_optional(client, x, y, z)


def _choose_descent_offset(
    client, px: int, py: int, pz: int, *, probe_distance: int = 3
) -> Optional[tuple[int, int]]:
    from .stone_descent import choose_descent_offset

    return choose_descent_offset(client, px, py, pz, probe_distance=probe_distance)


def _manual_column_descend(
    client,
    *,
    target_y: int,
    max_steps: int = 40,
) -> bool:
    from .stone_descent import manual_column_descend

    return manual_column_descend(client, target_y=target_y, max_steps=max_steps)


def ensure_tunnel_lighting(client, state, last_torch_pos) -> tuple:
    from .stone_descent import ensure_tunnel_lighting as implementation

    return implementation(client, state, last_torch_pos)

def _descend_to_stone_layer(
    client,
    *,
    target_depth: int = 25,
    floor_y: int = 50,
    timeout: int = 180,
    lateral_reach: int = 8,
) -> bool:
    from .stone_descent import descend_to_stone_layer

    return descend_to_stone_layer(
        client, target_depth=target_depth, floor_y=floor_y,
        timeout=timeout, lateral_reach=lateral_reach,
    )


def _restart_stone_mining(client, count: int) -> float:
    client.transport.dispatch(
        "mine", {"blocks": STONE_BLOCKS, "quantity": count + 10}
    )
    return time.time()


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
        if not _reserve_gathering_inventory(client):
            return False

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
        relocation_attempted = False
        dry_relocation_attempted = False
        descent_attempted = False
        last_torch_pos = None

        while time.time() - start < timeout:
            if free_inventory_slots(client) < 2:
                client.transport.dispatch("cancel", {})
                if not _reserve_gathering_inventory(client):
                    return False
                client.transport.dispatch(
                    "mine", {"blocks": STONE_BLOCKS, "quantity": count + 10}
                )
            # INTEGRATE DEFENSE
            from .combat import defend_or_flee
            defense = run_mining_defense(client, defend_or_flee)
            if defense.intervened:
                 if defense.survival_abort:
                     client.transport.dispatch("cancel", {})
                     print(
                         "SURVIVAL: abandoning submerged stone target "
                         "after surfacing"
                     )
                     # The normal dry-terrain recovery ladder lives below the
                     # stall detector. Returning here made that ladder
                     # unreachable: every phase retry restarted the same mine
                     # from the same flooded cell. Relocate once before any
                     # mining process is allowed to resume.
                     dry_relocation_attempted = True
                     if _relocate_to_dry_stone_terrain(client):
                         direct_failures = idle_checks = stalled_checks = 0
                         descent_attempted = False
                         start = _restart_stone_mining(client, count)
                         continue
                     client.transport.dispatch("cancel", {})
                     return False
                 # Combat happened. Resume mining.
                 client.transport.dispatch("mine", {"blocks": STONE_BLOCKS, "quantity": count + 10})
                 time.sleep(2)
                 idle_checks = 0
                 continue

            if not _ensure_mining_pickaxe(client):
                print("DEBUG: Could not replace worn pickaxe during stone gathering")
                return False
            # Fail Fast: Check if Baritone gave up (is_pathing = False)
            state = _read_state_optional(client, retries=3, label="Stone gather state")
            if state is None:
                time.sleep(0.5)
                continue
                
            placed, last_torch_pos = ensure_tunnel_lighting(client, state, last_torch_pos)
            if placed:
                client.transport.dispatch("mine", {"blocks": STONE_BLOCKS, "quantity": count + 10})
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
                    # Recover through descent, known terrain, then a new dry site.
                    if (
                        not descent_attempted
                        and _descend_to_stone_layer(client)
                    ):
                        descent_attempted = True
                        direct_failures = idle_checks = stalled_checks = 0
                        start = _restart_stone_mining(client, count)
                        continue
                    if (
                        not relocation_attempted
                        and _relocate_to_checkpointed_stone_source(client)
                    ):
                        relocation_attempted = True
                        direct_failures = idle_checks = stalled_checks = 0
                        start = _restart_stone_mining(client, count)
                        continue
                    if (
                        not dry_relocation_attempted
                        and _relocate_to_dry_stone_terrain(client)
                    ):
                        dry_relocation_attempted = True
                        descent_attempted = False
                        direct_failures = idle_checks = stalled_checks = 0
                        start = _restart_stone_mining(client, count)
                        continue
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
    except PlayerDeathDetected:
        raise
    except Exception as exc:
        print(f"Stone gathering error: {exc}")
        return False


@with_mining_acceleration
def gather_ores(client, ore_type: str, count: int, timeout: int = 600) -> bool:
    """Gather ore with best-effort mining acceleration on supported bridges."""
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
        # Keep three slots so incidental cave blocks cannot displace the ore;
        # the loop exits before another cleanup once the target arrives.
        required_slots = 3
        if not _reserve_gathering_inventory(client, required_slots):
            return False

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
        recovery = OreStallRecovery()
        
        while time.time() - start < timeout:
            state, _state_attempts = _read_state_with_retry(
                client, retries=3, label="Ore gathering get_state"
            )
            if state is None:
                if time.time() - start >= timeout:
                    print("Ore gathering state check timeout budget exhausted")
                    return False
                continue

            # A target pickup is success even when it consumes the final slot.
            total = count_item(client, drop_item)
            if total >= count:
                client.transport.dispatch("cancel", {})
                return True
            if free_inventory_slots(client) < required_slots:
                _serialized_dispatch(
                    client,
                    "cancel",
                    {},
                    post_delay_seconds=_BARITONE_CANCEL_GRACE_SECONDS,
                )
                if not _reserve_gathering_inventory(
                    client, minimum_free_slots=required_slots
                ):
                    return False
                _start_mine_process(client, ORES[ore_type], count + 2)
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
            is_pathing = state.get("is_pathing", True) # Default True to be safe

            if int(state.get("food_level", state.get("food", 20))) <= 8:
                from .combat import eat_until_hunger
                client.transport.dispatch("cancel", {})
                time.sleep(0.75)
                if not eat_until_hunger(client, minimum_food=_MINING_HUNGER_TARGET):
                    after_eat, _ = _read_state_with_retry(
                        client,
                        retries=2,
                        label="Ore gathering hunger re-check",
                    )
                    if after_eat is None:
                        print(
                            "Ore gathering: hunger check timed out; "
                            "continuing with current food level."
                        )
                    else:
                        fallback_food = int(
                            after_eat.get("food_level", after_eat.get("food", 20))
                        )
                        if fallback_food < _SAFE_MINING_FOOD:
                            print(
                                "DEBUG: Ore gathering stopped: hunger could not be "
                                f"restored safely (food={fallback_food})"
                            )
                            return False
                        print(
                            "DEBUG: Ore gathering could not reach recovery target; "
                            f"continuing at fallback food={fallback_food}"
                        )
                    _start_mine_process(client, ORES[ore_type], count + 2)
                    idle_checks = 0
                    stalled_checks = 0
                    continue
                _start_mine_process(client, ORES[ore_type], count + 2)
                idle_checks = 0
                stalled_checks = 0
                continue

            if not is_pathing:
                idle_checks += 1
            else:
                idle_checks = 0

            if total == last_total:
                stalled_checks += 1
            else:
                last_total = total
                stalled_checks = 0

            if idle_checks >= 3 or stalled_checks >= 5:
                if not recovery.recover(
                    client, ore_type, ORES[ore_type], count
                ):
                    return False
                idle_checks = 0
                stalled_checks = 0
            time.sleep(3) # Reduced sleep to scan more often
        client.transport.dispatch("cancel", {})
        return False
    except PlayerDeathDetected:
        raise
    except Exception as exc:
        print(f"Ore gathering error: {exc}")
        return False


def _attempt_water_pocket_sidestep(
    client,
    block_id,
    break_for_step,
    walk_to_cleared_step,
    state,
    *,
    px: int,
    current_y: int,
    pz: int,
    directions: list[tuple[int, int]],
    air_blocks: set[str],
    unsafe_blocks: set[str],
) -> str:
    for dx, dz in directions:
        target_x = px + dx
        target_y = current_y
        target_z = pz + dz
        floor_id = block_id(target_x, current_y - 1, target_z)
        foot_id = block_id(target_x, current_y, target_z)
        head_id = block_id(target_x, current_y + 1, target_z)
        if (
            floor_id in air_blocks
            or floor_id in unsafe_blocks
            or foot_id in unsafe_blocks
            or head_id in unsafe_blocks
        ):
            continue
        if foot_id not in air_blocks and not break_for_step(
            target_x, target_y, target_z
        ):
            continue
        if head_id not in air_blocks and not break_for_step(
            target_x, current_y + 1, target_z
        ):
            continue
        if (
            block_id(target_x, current_y, target_z) in unsafe_blocks
            or block_id(target_x, current_y + 1, target_z) in unsafe_blocks
        ):
            continue
        move_result = walk_to_cleared_step(
            target_x,
            target_y,
            target_z,
            current_y,
            state,
            label="water pocket sidestep",
        )
        if move_result == "unsafe":
            return "unsafe"
        if move_result == "moved":
            return "moved"

    print(
        "Y navigation: could not sidestep out of a source-water "
        "pocket before descent failed"
    )
    return "blocked"


def _recover_descent_stalled_column(
    client,
    *,
    current_y: int,
    target_y: int,
    max_altitude: int | None = None,
    reason: str = "repeated descent stall",
) -> bool:
    """Attempt one manual column descent after a detected navigation stall.

    ``max_altitude`` lets a caller restrict this to low-altitude stalls only
    (the two callers that used to be separate ``..._low_altitude_...`` and
    ``..._stalled_column`` functions differed only in this gate and their
    print wording -- same fallback-target math and same recovery call).
    """
    if target_y >= current_y:
        return False
    if max_altitude is not None and current_y > max_altitude:
        return False
    fallback_target = max(target_y, current_y - 12)
    if fallback_target >= current_y:
        return False
    print(
        f"Y navigation: {reason} at Y={current_y}, "
        f"trying manual column recovery to Y={fallback_target}"
    )
    return _manual_column_descend(
        client,
        target_y=fallback_target,
        max_steps=current_y - fallback_target,
    )


def go_to_y_level(client, y: int, timeout: int = 300) -> bool:
    """Carve and walk a verified one-block-at-a-time staircase."""
    downward_enabled = False
    try:
        settings = [
            "#set allowBreak true",
            "#set allowPlace true",
            "#set allowParkour false",
            "#set allowParkourAscend false",
            "#set allowParkourPlace false",
            # The controller verifies and excavates a supported one-block step
            # before issuing each goal. Baritone still refuses to enter that
            # lower block when allowDownward is disabled, even for a completed
            # staircase. Enable it only for this guarded descent and restore it
            # in the function's finally block.
            "#set allowDownward true",
            "#set allowWaterBucketFall false",
            "#set maxFallHeightNoWater 3",
            "#set maxFallHeightBucket 3",
        ]
        for s in settings:
            _serialized_dispatch(client, "chat", {"message": s}, post_delay_seconds=0.1)
            if s == "#set allowDownward true":
                downward_enabled = True

        overall_start = time.time()
        preferred_direction = 0
        unreadable_retries = 0
        descent_stall_retries = 0
        descent_stall_position: tuple[int, int, int] | None = None
        low_altitude_stall_retries = 0
        low_altitude_stall_position: tuple[int, int, int] | None = None
        directions = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        air_blocks = {"", "minecraft:air", "minecraft:cave_air", "minecraft:void_air"}
        # A block we could not read (bridge stalled under fleet load) is treated
        # as unsafe so we never dig or step toward an unknown, but a single
        # unreadable block no longer raises out and abandons the whole descent.
        UNREADABLE = "__unreadable__"
        unsafe_blocks = {
            "minecraft:water", "minecraft:lava", "minecraft:bedrock",
            "minecraft:gravel", "minecraft:sand", "minecraft:red_sand",
            "minecraft:magma_block", "minecraft:pointed_dripstone",
            "minecraft:spawner", "minecraft:chest", "minecraft:trapped_chest",
            "minecraft:barrel", "minecraft:furnace", "minecraft:crafting_table",
            UNREADABLE,
        }
        # Dense deepslate is used to prioritize the verified vertical route.
        # The tick-driven ``dig_block`` primitive now performs exact mining;
        # normal Baritone excavation remains a compatibility fallback.
        baritone_excavation_blocks = {"minecraft:deepslate"}

        read_flags = {"unreadable": False}
        attempted_descent_anchors = set()
        anchor_relocations = 0
        lowest_y_seen = None

        def block_id(x: int, by: int, z: int) -> str:
            try:
                return str(
                    client.transport.dispatch(
                        "get_block", {"x": x, "y": by, "z": z}
                    ).get("id", "")
                )
            except TransportError:
                # Transient bridge timeout: report unreadable (unsafe) so the
                # step is skipped this pass rather than crashing the descent,
                # and flag it so a stall caused by a timeout is retried rather
                # than mistaken for genuinely impassable terrain.
                read_flags["unreadable"] = True
                return UNREADABLE

        from .stone_descent import mine_step_block

        def break_for_step(x: int, by: int, z: int) -> bool:
            return mine_step_block(
                client,
                x,
                by,
                z,
                read_block=block_id,
                air_blocks=air_blocks,
                unsafe_blocks=unsafe_blocks,
                baritone_fallback_blocks=baritone_excavation_blocks,
            )

        def safe_anchor_candidates(px: int, current_y: int, pz: int):
            """Yield nearby same-level columns with two stable support blocks."""
            rays = (
                (1, 0),
                (-1, 0),
                (0, 1),
                (0, -1),
                (1, 1),
                (1, -1),
                (-1, 1),
                (-1, -1),
            )
            for radius in range(2, 9):
                for dx, dz in rays:
                    candidate = (
                        px + dx * radius,
                        current_y,
                        pz + dz * radius,
                    )
                    if candidate in attempted_descent_anchors:
                        continue
                    cx, cy, cz = candidate
                    floor_id = block_id(cx, cy - 1, cz)
                    support_id = block_id(cx, cy - 2, cz)
                    foot_id = block_id(cx, cy, cz)
                    head_id = block_id(cx, cy + 1, cz)
                    if (
                        floor_id in air_blocks
                        or floor_id in unsafe_blocks
                        or support_id in air_blocks
                        or support_id in unsafe_blocks
                        or foot_id in unsafe_blocks
                        or head_id in unsafe_blocks
                    ):
                        continue
                    yield candidate

        def walk_to_cleared_step(
            target_x: int,
            target_y: int,
            target_z: int,
            current_y: int,
            state: dict,
            *,
            label: str,
        ) -> str:
            """Ask Baritone to enter one verified step and classify the result."""
            response = _serialized_dispatch(
                client,
                "goto",
                {"x": target_x, "y": target_y, "z": target_z},
                post_delay_seconds=_BARITONE_MINE_SETUP_SECONDS,
            )
            if response.get("error"):
                print(f"Y navigation: {label} goto rejected: {response['error']}")
                return "stalled"

            # Fleet-loaded clients can remain actively pathing for longer than
            # eight seconds while calculating and mining deepslate. Cancelling
            # that healthy work recreated Bot07's stall at every lower step.
            move_deadline = time.monotonic() + 30.0
            starting_health = float(state.get("health", 20) or 0)
            last_position = (target_x, current_y, target_z)
            while time.monotonic() < move_deadline:
                moved, _ = _read_state_with_retry(
                    client,
                    retries=2,
                    label="Y navigation cleared-step wait",
                )
                if moved is None:
                    read_flags["unreadable"] = True
                    time.sleep(0.25)
                    continue
                moved_pos = moved.get(
                    "block_position", moved.get("position", {})
                )
                moved_x = int(moved_pos.get("x", target_x))
                moved_y = int(moved_pos.get("y", current_y))
                moved_z = int(moved_pos.get("z", target_z))
                last_position = (moved_x, moved_y, moved_z)
                moved_health = float(moved.get("health", starting_health) or 0)
                if (
                    moved.get("is_dead", False)
                    or moved_health <= 0
                    or moved_health < starting_health - 4
                    or moved_y < target_y - 2
                ):
                    _serialized_dispatch(
                        client,
                        "cancel",
                        {},
                        post_delay_seconds=_BARITONE_CANCEL_GRACE_SECONDS,
                    )
                    print(f"Y navigation safety abort during {label}")
                    return "unsafe"
                if moved_y <= target_y:
                    _serialized_dispatch(
                        client,
                        "cancel",
                        {},
                        post_delay_seconds=_BARITONE_CANCEL_GRACE_SECONDS,
                    )
                    return "moved"
                time.sleep(0.25)

            _serialized_dispatch(
                client,
                "cancel",
                {},
                post_delay_seconds=_BARITONE_CANCEL_GRACE_SECONDS,
            )
            print(
                f"Y navigation: {label} goto made no downward progress; "
                f"target=({target_x}, {target_y}, {target_z}) "
                f"last_position={last_position}"
            )
            return "stalled"

        while time.time() - overall_start < timeout:
            state, _ = _read_state_with_retry(
                client,
                retries=2,
                label="Y navigation progress check",
            )
            if state is None:
                # Transient bridge stall under fleet load: pause and retry the
                # iteration instead of throwing away a partly-dug staircase.
                print("Y navigation: transient get_state timeout; resuming descent")
                time.sleep(1.0)
                continue
            pos = state.get("block_position", state.get("position", {}))
            px, current_y, pz = int(pos.get("x", 0)), int(pos.get("y", 64)), int(pos.get("z", 0))
            descent_position = (px, current_y, pz)
            if descent_position == descent_stall_position:
                descent_stall_retries += 1
            else:
                descent_stall_position = descent_position
                descent_stall_retries = 0
            if descent_stall_retries >= 8:
                if _recover_descent_stalled_column(
                    client,
                    current_y=current_y,
                    target_y=y,
                ):
                    unreadable_retries = 0
                    descent_stall_retries = 0
                    low_altitude_stall_retries = 0
                    continue
                print(
                    "Y navigation stalled: no descent delta at same position "
                    f"{descent_position}; aborting"
                )
                return False

            # Relocation is a bounded recovery at one elevation, not a budget
            # for the whole surface-to-deepslate journey. Bot07 exhausted the
            # old global allowance near Y=3 even after making real progress.
            if lowest_y_seen is None or current_y < lowest_y_seen:
                lowest_y_seen = current_y
                anchor_relocations = 0
                attempted_descent_anchors.clear()

            if state.get("is_dead", False) or float(state.get("health", 20) or 0) <= 0:
                print("Y navigation aborted: player is dead")
                return False

            # Stop immediately once the target band is reached so recovery
            # branches don't classify a deep but successful descent as a stall.
            if current_y <= y + 3:
                print(f"DEBUG: Reached target Y={y}!")
                client.transport.dispatch("cancel", {})
                return True

            if current_y <= 8:
                current_position = (px, current_y, pz)
                if current_position == low_altitude_stall_position:
                    low_altitude_stall_retries += 1
                else:
                    low_altitude_stall_retries = 0
                    low_altitude_stall_position = current_position
                if low_altitude_stall_retries >= 6:
                    if _recover_descent_stalled_column(
                        client,
                        current_y=current_y,
                        target_y=y,
                        max_altitude=8,
                        reason="low-altitude stall",
                    ):
                        unreadable_retries = 0
                        low_altitude_stall_retries = 0
                        continue
                    print(
                        "Y navigation stalled: no descent delta at "
                        f"low altitude (Y={current_y}) after repeated goals at "
                        f"{current_position}; aborting"
                    )
                    return False
            else:
                low_altitude_stall_retries = 0
                low_altitude_stall_position = (px, current_y, pz)

            health = float(state.get("health", 20) or 0)
            if health < 12.0:
                from .combat import recover_health

                client.transport.dispatch("cancel", {})
                if not recover_health(
                    client, minimum_health=12.0, timeout=10.0
                ):
                    raise SurvivalRecoveryRequired(
                        f"health remains {health:.1f}/20 during Y navigation"
                    )
                continue

            food_level = int(state.get("food_level", state.get("food", 20)))
            if food_level <= 10:
                from .combat import eat_until_hunger
                client.transport.dispatch("cancel", {})
                if not eat_until_hunger(client, minimum_food=_MINING_HUNGER_TARGET):
                    after_eat, _ = _read_state_with_retry(
                        client,
                        retries=2,
                        label="Y navigation hunger re-check",
                    )
                    if after_eat is None:
                        print("Y navigation: hunger check timed out; pausing briefly and retrying")
                        time.sleep(1.5)
                        continue
                    fallback_food = int(
                        after_eat.get("food_level", after_eat.get("food", 20))
                    )
                    if fallback_food < _SAFE_MINING_FOOD:
                        print("Y navigation aborted: could not restore hunger")
                        return False
                    # eat_until_hunger has already failed by this point, so if
                    # nothing edible is carried the bot cannot lift itself over
                    # the health-regen line at any stage of the descent. It
                    # would commit to a deep, hostile, no-regen trip it cannot
                    # heal out of. Confirmed live: Bot08 descended at food=9
                    # with an empty larder, arrived around Y=40 already wounded,
                    # lost every flee attempt (a mineshaft has no terrain-safe
                    # escape endpoints) and died 9 times in one hour -- each
                    # time respawning and walking straight back down to the same
                    # spot. Abort here so the caller runs a surface food hunt
                    # first instead.
                    from .combat import _emergency_food_count

                    if (
                        fallback_food < _MINING_REGEN_FOOD
                        and _emergency_food_count(client) == 0
                    ):
                        print(
                            "Y navigation aborted: food="
                            f"{fallback_food} below the health-regen threshold "
                            f"({_MINING_REGEN_FOOD}) with no carried food; "
                            "refusing a descent that cannot be healed out of"
                        )
                        return False
                    print(
                        "Y navigation: could not reach target food before descent; "
                        f"continuing with fallback food={fallback_food}."
                    )
                    # Food exists, so this is not the one-way starvation case,
                    # but an unsuccessful eat action is still not permission to
                    # excavate. Yield this bounded descent attempt for a clean
                    # retry instead of entering an unbounded supply loop.
                    return False

            # A staircase can intersect a source-water cell while the bot's
            # head remains in air. Baritone cannot excavate out of that cell,
            # and newly broken blocks immediately refill. Seal cardinal inflow
            # and bucket the occupied source before selecting the next step.
            from .stone_descent import drain_breathable_water_pocket

            water_recovery = drain_breathable_water_pocket(client, state)
            if water_recovery is True:
                unreadable_retries = 0
                continue
            if water_recovery is False:
                sidestep_result = _attempt_water_pocket_sidestep(
                    client,
                    block_id,
                    break_for_step,
                    walk_to_cleared_step,
                    state,
                    px=px,
                    current_y=current_y,
                    pz=pz,
                    directions=directions,
                    air_blocks=air_blocks,
                    unsafe_blocks=unsafe_blocks,
                )
                if sidestep_result == "unsafe":
                    return False
                if sidestep_result == "moved":
                    unreadable_retries = 0
                    continue
                if _recover_descent_stalled_column(
                    client,
                    current_y=current_y,
                    target_y=y,
                    max_altitude=8,
                    reason="low-altitude stall",
                ):
                    continue
                print(
                    "Y navigation: could not sidestep out of a source-water "
                    "pocket before descent failed"
                )
                continue
            
            read_flags["unreadable"] = False
            step_succeeded = False
            ordered_directions = [
                (
                    directions[(preferred_direction + offset) % len(directions)][0],
                    directions[(preferred_direction + offset) % len(directions)][1],
                    (preferred_direction + offset) % len(directions),
                )
                for offset in range(len(directions))
            ]
            # In dense deepslate, the exact builder is known not to work and a
            # guarded vertical Baritone step is the recovery that actually
            # moved Bot07. Try it before burning four 30-second diagonal goals.
            if block_id(px, current_y - 1, pz) in baritone_excavation_blocks:
                ordered_directions.insert(0, (0, 0, None))

            for dx, dz, direction_index in ordered_directions:
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
                # The exact-block builder can report completion without mining
                # deepslate. If that happens, retain the verified solid floor
                # and let the guarded one-block Baritone goal excavate the two
                # already-vetted, ordinary mineable blocks.
                head_cleared = head_id in air_blocks or break_for_step(
                    target_x, target_y + 1, target_z
                )
                foot_cleared = foot_id in air_blocks or break_for_step(
                    target_x, target_y, target_z
                )
                head_after = block_id(target_x, target_y + 1, target_z)
                foot_after = block_id(target_x, target_y, target_z)
                floor_after = block_id(target_x, target_y - 1, target_z)
                if head_after in unsafe_blocks or foot_after in unsafe_blocks:
                    continue
                if floor_after in air_blocks or floor_after in unsafe_blocks:
                    continue
                if (
                    (not head_cleared and head_after not in air_blocks)
                    or (not foot_cleared and foot_after not in air_blocks)
                ):
                    if not _ensure_mining_pickaxe(client):
                        continue
                    print(
                        "Y navigation: exact break left mineable blocks; "
                        "delegating this verified step to Baritone"
                    )

                move_result = walk_to_cleared_step(
                    target_x,
                    target_y,
                    target_z,
                    current_y,
                    state,
                    label="one-block step",
                )
                if move_result == "unsafe":
                    return False
                if move_result == "moved":
                    if direction_index is not None:
                        preferred_direction = direction_index
                    step_succeeded = True
                if step_succeeded:
                    break

            if not step_succeeded:
                # Straight-down fallback. The diagonal staircase cannot proceed
                # when all four neighbours lack a solid floor (cliff edge, cave
                # mouth, sand/gravel field), which stranded bots mid-descent and
                # blocked every FOOD_AND_IRON diamond run. A vertical step is
                # safe ONLY when we land on verified solid, non-hazard ground one
                # block below the floor we break: no free-fall, and we refuse to
                # break a liquid, gravel/sand, or a base block (all in
                # unsafe_blocks). Each success lowers current_y by one, so the
                # outer loop still converges on the target.
                break_y = current_y - 1  # solid floor under our feet
                land_y = current_y - 2   # where we land after dropping one block
                break_id = block_id(px, break_y, pz)
                land_id = block_id(px, land_y, pz)
                if (
                    break_id not in air_blocks
                    and break_id not in unsafe_blocks
                    and land_id not in air_blocks
                    and land_id not in unsafe_blocks
                ):
                    print(
                        f"DEBUG: Vertical step Y={current_y}->{break_y} at "
                        f"({px}, {break_y}, {pz})"
                    )
                    block_cleared = break_id in air_blocks or break_for_step(
                        px, break_y, pz
                    )
                    break_after = block_id(px, break_y, pz)
                    land_after = block_id(px, land_y, pz)
                    if (
                        break_after not in unsafe_blocks
                        and land_after not in air_blocks
                        and land_after not in unsafe_blocks
                    ):
                        if not block_cleared and break_after not in air_blocks:
                            if not _ensure_mining_pickaxe(client):
                                continue
                            print(
                                "Y navigation: exact vertical break left a "
                                "mineable block; delegating the verified step "
                                "to Baritone"
                            )
                        move_result = walk_to_cleared_step(
                            px,
                            break_y,
                            pz,
                            current_y,
                            state,
                            label="vertical step",
                        )
                        if move_result == "unsafe":
                            return False
                        step_succeeded = move_result == "moved"

            if not step_succeeded and anchor_relocations < 3:
                # A gravel/sand collar can make every immediate step unsafe
                # even though stable stone is only a few blocks away.  Never
                # dig downward through falling material.  Instead, tunnel at
                # the current Y to a verified column with two stable supports,
                # then let the next loop resume the ordinary staircase.
                relocation_attempts = 0
                for anchor in safe_anchor_candidates(px, current_y, pz):
                    relocation_attempts += 1
                    if relocation_attempts > 4:
                        break
                    attempted_descent_anchors.add(anchor)
                    ax, ay, az = anchor
                    anchor_aborted = False
                    print(
                        f"Y navigation: relocating to stable descent anchor "
                        f"({ax}, {ay}, {az})"
                    )
                    _serialized_dispatch(
                        client,
                        "goto",
                        {"x": ax, "y": ay, "z": az},
                        post_delay_seconds=_BARITONE_MINE_SETUP_SECONDS,
                    )
                    move_deadline = time.monotonic() + 15.0
                    starting_health = float(state.get("health", 20) or 0)
                    while time.monotonic() < move_deadline:
                        moved, _ = _read_state_with_retry(
                            client,
                            retries=2,
                            label="Y navigation anchor relocation",
                        )
                        if moved is None:
                            print(
                                "Y navigation: anchor relocation state timeout; "
                                "waiting for bridge response"
                            )
                            time.sleep(0.25)
                            continue
                        moved_pos = moved.get(
                            "block_position", moved.get("position", {})
                        )
                        moved_x = int(moved_pos.get("x", px))
                        moved_y = int(moved_pos.get("y", current_y))
                        moved_z = int(moved_pos.get("z", pz))
                        moved_health = float(
                            moved.get("health", starting_health) or 0
                        )
                        if (
                            moved.get("is_dead", False)
                            or moved_health <= 0
                            or moved_health < starting_health - 4
                            or moved_y < current_y - 2
                        ):
                            _serialized_dispatch(
                                client,
                                "cancel",
                                {},
                                post_delay_seconds=_BARITONE_CANCEL_GRACE_SECONDS,
                            )
                            print("Y navigation safety abort during anchor relocation")
                            anchor_aborted = True
                            break
                        if (
                            abs(moved_x - ax) <= 1
                            and abs(moved_y - ay) <= 1
                            and abs(moved_z - az) <= 1
                        ):
                            _serialized_dispatch(
                                client,
                                "cancel",
                                {},
                                post_delay_seconds=_BARITONE_CANCEL_GRACE_SECONDS,
                            )
                            anchor_relocations += 1
                            preferred_direction = 0
                            step_succeeded = True
                            break
                        time.sleep(0.25)
                    if anchor_aborted:
                        continue
                    if step_succeeded:
                        break
                    _serialized_dispatch(
                        client,
                        "cancel",
                        {},
                        post_delay_seconds=_BARITONE_CANCEL_GRACE_SECONDS,
                    )

                if step_succeeded:
                    time.sleep(0.25)
                    continue

            if not step_succeeded:
                _serialized_dispatch(
                    client,
                    "cancel",
                    {},
                    post_delay_seconds=_BARITONE_CANCEL_GRACE_SECONDS,
                )
                # If any block this pass was unreadable (bridge timeout), the
                # stall may be spurious - retry a bounded number of times before
                # concluding the terrain is genuinely impassable.
                if read_flags["unreadable"] and unreadable_retries < 5:
                    unreadable_retries += 1
                    print(
                        f"Y navigation: unreadable blocks under load "
                        f"({unreadable_retries}/5); re-scanning descent"
                    )
                    time.sleep(1.0)
                    continue
                if _recover_descent_stalled_column(
                    client,
                    current_y=current_y,
                    target_y=y,
                    max_altitude=8,
                    reason="low-altitude stall",
                ):
                    unreadable_retries = 0
                    continue
                print(f"Y navigation stalled: no safe staircase step from Y={current_y}")
                return False

            unreadable_retries = 0
            time.sleep(0.25)
        
        client.transport.dispatch("cancel", {})
        return False
    except (PlayerDeathDetected, SurvivalRecoveryRequired):
        raise
    except Exception as exc:
        print(f"Y level navigation error: {exc}")
        return False
    finally:
        if downward_enabled:
            try:
                _serialized_dispatch(
                    client,
                    "chat",
                    {"message": "#set allowDownward false"},
                    post_delay_seconds=0.1,
                )
            except Exception:
                # A game restart also restores the configured default. Never
                # mask the descent result merely because cleanup hit a bridge
                # timeout.
                pass



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
        state = _read_state_optional(
            client, retries=3, label="Wait for path completion state"
        )
        if state is None:
            time.sleep(0.5)
            continue
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


_STONE_RECIPE_COBBLESTONE_NEEDS = {
    "minecraft:stone_pickaxe": 3,
    "minecraft:stone_axe": 3,
    "minecraft:stone_hoe": 2,
    "minecraft:stone_sword": 2,
    "minecraft:stone_shovel": 1,
    "minecraft:furnace": 8,
}


def ensure_stone_material(client, item_id: str, qty: int = 1) -> bool:
    """Gather the cobblestone a stone-tier recipe needs before crafting.

    The bridge does not recursively acquire recipe ingredients, so asking it
    for a stone tool while carrying zero cobblestone fails on every retry
    until the ensure_supplies timeout kills the phase (live FOOD_AND_IRON
    blocker: "Missing ingredients for minecraft:stone_pickaxe ...
    minecraft:cobblestone:0").
    """
    needed_each = _STONE_RECIPE_COBBLESTONE_NEEDS.get(item_id)
    if needed_each is None:
        return True

    def _carried_stone() -> int:
        return count_item(client, "minecraft:cobblestone") + count_item(
            client, "minecraft:cobbled_deepslate"
        )

    needed = needed_each * max(1, qty)
    current = _carried_stone()
    if current >= needed:
        return True
    print(f"  Need {needed - current} more cobblestone for {item_id}; gathering stone first...")
    gather_stone(client, count=needed)
    return _carried_stone() >= needed


def _craft_with_table(client, item_id: str, qty: int) -> bool:
    """Craft until the absolute carried target ``qty`` is satisfied."""
    from .base import open_crafting_table, place_crafting_table
    from .automation_utils import get_player_pos

    # Early exit if we already have the item
    current = count_item(client, item_id)
    if current >= qty:
        return True
    missing = qty - current

    # Tool recipes depend on sticks, which are made in the player 2x2 grid.
    # Prepare them before opening the crafting table; otherwise the manual
    # fallback sees planks but no sticks and loops forever.
    if not ensure_tool_sticks(client, item_id, qty):
        print(f"  Failed to prepare stick dependency for {item_id}")
        return False

    # Stone-tier recipes likewise need their cobblestone acquired up front.
    if not ensure_stone_material(client, item_id, qty):
        print(f"  Failed to prepare stone material for {item_id}")
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
                "minecraft:mangrove_planks", "minecraft:cherry_planks", "minecraft:pale_oak_planks"
            ])

            if total_planks < 4:
                from .inventory import _ensure_raw_planks
                if not _ensure_raw_planks(client, 4):
                    print(
                        "  Could not ensure 4 planks for crafting table via logs/planks."
                    )
                    return False

            # Craft crafting table first (2x2 recipe)
            craft(client, "minecraft:crafting_table", 1)
            time.sleep(0.5)
        
        # Get player position for placement
        pos = get_player_pos(client)
        if not pos:
            print(f"  Cannot get position to place crafting table")
            return False
        
        import math
        px, py, pz = math.floor(float(pos[0])), math.floor(float(pos[1])), math.floor(float(pos[2]))
        
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
        
        _REPLACEABLE = {
            "minecraft:air", "minecraft:cave_air", "minecraft:void_air",
            "minecraft:short_grass", "minecraft:tall_grass", "minecraft:grass",
            "minecraft:leaf_litter", "minecraft:vine", "minecraft:snow",
            "minecraft:fern", "minecraft:large_fern", "minecraft:dead_bush",
            "",
        }
        found_spot = False
        table_pos = None
        max_attempts = 16  # Try all offsets before mining a fallback space
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
                if block_id not in _REPLACEABLE:
                    print(f"  Skip ({tx}, {ty}, {tz}) - occupied by {block_id}")
                    continue  # Skip this spot, it's not replaceable
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
            state = _read_state_optional(
                client, retries=3, label="Craft table surface state"
            )
            if state is None:
                return False
            pos = state.get("block_position", state.get("position", {}))
            surface_x, surface_y, surface_z = (
                int(pos.get("x", 0)),
                int(pos.get("y", 64)),
                int(pos.get("z", 0)),
            )
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
        result = craft(client, item_id, missing)
        time.sleep(0.3)
        return result and count_item(client, item_id) >= qty
    except Exception as e:
        print(f"  Crafting failed for {item_id}: {e}")
        return False


def _safe_close_screen(client, label: str = "") -> None:
    """Best-effort close_screen for stale/laggy bridge responses."""
    try:
        client.transport.dispatch("close_screen", {})
    except Exception as exc:
        detail = f" ({label})" if label else ""
        print(f"  close_screen no-op{detail}: {exc}")


def _ensure_flamethrower_ready(client, qty: int = 1) -> bool:
    """Prepare ingredients for flint-and-steel before crafting.

    This avoids brittle empty-item failures in the nether-builder path and keeps
    ``ensure_supplies`` from repeatedly retrying only the recipe step.
    """
    needed = max(1, int(qty))
    if count_item(client, "minecraft:iron_ingot") < needed:
        if not _smelt_requirement_shortfall(
            client,
            "minecraft:iron_ingot",
            needed - count_item(client, "minecraft:iron_ingot"),
        ):
            if not gather_ores(client, "iron", count=needed):
                return False

    if count_item(client, "minecraft:flint") < needed:
        if not gather_gravel(client, count=needed):
            return False

    return True


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
    "minecraft:stick": lambda client, qty: (_safe_close_screen(client, "stick"), craft(client, "minecraft:stick", max(qty, 4)), time.sleep(1)),
    "minecraft:oak_planks": lambda client, qty: (_safe_close_screen(client, "plank"), craft(client, "minecraft:oak_planks", qty), time.sleep(1)),
    # Wooden tools (3x3 crafting table required)
    "minecraft:wooden_pickaxe": lambda client, qty: _craft_with_table(client, "minecraft:wooden_pickaxe", qty),
    "minecraft:wooden_sword": lambda client, qty: _craft_with_table(client, "minecraft:wooden_sword", qty),
    "minecraft:wooden_axe": lambda client, qty: _craft_with_table(client, "minecraft:wooden_axe", qty),
    "minecraft:wooden_shovel": lambda client, qty: _craft_with_table(client, "minecraft:wooden_shovel", qty),
    # Stone tools
    "minecraft:stone_pickaxe": lambda client, qty: (_safe_close_screen(client, "stone_pickaxe"), _craft_with_table(client, "minecraft:stone_pickaxe", qty)),
    "minecraft:stone_sword": lambda client, qty: (_safe_close_screen(client, "stone_sword"), _craft_with_table(client, "minecraft:stone_sword", qty)),
    "minecraft:stone_axe": lambda client, qty: (_safe_close_screen(client, "stone_axe"), _craft_with_table(client, "minecraft:stone_axe", qty)),
    "minecraft:stone_shovel": lambda client, qty: (_safe_close_screen(client, "stone_shovel"), _craft_with_table(client, "minecraft:stone_shovel", qty)),
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
    # Craft-from-carried-ingredients handlers for the progression recipes the
    # manual grid can drive (see _MANUAL_GRID_RECIPES). Without an entry,
    # ensure_supplies logs "No handler ... skipping" and burns its whole
    # timeout: end_game's ensure_supplies({"minecraft:ender_eye": 12}) would
    # have dead-ended exactly that way. Ingredient ACQUISITION (rods, pearls,
    # sugar cane, leather, obsidian) belongs to the owning phases.
    "minecraft:flint_and_steel": lambda client, qty: _ensure_flamethrower_ready(client, qty) and _craft_with_table(client, "minecraft:flint_and_steel", qty),
    "minecraft:paper": lambda client, qty: _craft_with_table(client, "minecraft:paper", qty),
    "minecraft:book": lambda client, qty: _craft_with_table(client, "minecraft:book", qty),
    "minecraft:bookshelf": lambda client, qty: _craft_with_table(client, "minecraft:bookshelf", qty),
    "minecraft:enchanting_table": lambda client, qty: _craft_with_table(client, "minecraft:enchanting_table", qty),
    "minecraft:blaze_powder": lambda client, qty: _craft_with_table(client, "minecraft:blaze_powder", qty),
    "minecraft:ender_eye": lambda client, qty: _craft_with_table(client, "minecraft:ender_eye", qty),
    "minecraft:shulker_box": lambda client, qty: _craft_with_table(client, "minecraft:shulker_box", qty),
    "minecraft:ladder": lambda client, qty: _craft_with_table(client, "minecraft:ladder", qty),
}

# A manual-grid recipe is, by definition, a supported ensure_supplies target.
# Keep hand-tuned acquisition strategies above, then fill the mechanical craft
# route for every remaining recipe. This prevents newly added progression
# recipes from silently entering the "No handler ... skipping" timeout loop.
for _manual_item_id in inventory._MANUAL_GRID_RECIPES:
    DEFAULT_REQUIREMENT_STRATEGIES.setdefault(
        _manual_item_id,
        lambda client, qty, item_id=_manual_item_id: _craft_with_table(
            client, item_id, qty
        ),
    )


def _reserve_gathering_inventory(client, minimum_free_slots: int = 3) -> bool:
    """Maintain headroom before and during any resource collection loop."""
    required = max(1, int(minimum_free_slots))
    if free_inventory_slots(client) >= required:
        return True
    print(f"  Gathering paused to reserve {required} inventory slots...")

    # Ground drops are temporary and can be collected again as soon as the bot
    # walks away. Prefer a durable trip to checkpointed home storage whenever
    # the phase executor supplied state, even if the chest chunk is unloaded.
    try:
        from .inventory import deposit_excess_to_chest, resolve_storage_location

        automation_state = getattr(client, "_automation_state", None)
        chest_pos = None
        if automation_state is not None:
            chest_pos = resolve_storage_location(
                client,
                state=automation_state,
                verify=False,
            )
        if chest_pos is not None:
            snapshot = client.transport.dispatch("get_state", {})
            distance = storage_distance(snapshot, chest_pos)
            if distance <= MAX_STORAGE_TRAVEL_DISTANCE:
                deposited = deposit_excess_to_chest(
                    client,
                    chest_pos,
                    state=automation_state,
                )
                if deposited >= 0 and free_inventory_slots(client) >= required:
                    print(
                        f"  Reserved {required} gathering slots in persistent home storage."
                    )
                    return True
            else:
                print(
                    "  Gathering cleanup skipped distant home storage "
                    f"({distance:.1f} blocks away)."
                )
    except Exception as exc:
        print(f"  Gathering storage cleanup unavailable ({exc}); using bounded disposal")

    return manage_inventory(client, minimum_free_slots=required)


def manage_inventory(client, minimum_free_slots: int = 1) -> bool:
    """Reserve carried slots for progression outputs by dropping bounded junk.

    ``drop_items`` now verifies the PlayerInventory-to-screen slot mapping, so
    this returns success only when the requested space really exists.  The
    bounded order avoids throwing every building stack merely because one
    bucket needs a destination.
    """
    from .inventory import drop_items, free_inventory_slots

    required = max(0, int(minimum_free_slots))
    if free_inventory_slots(client) >= required:
        return True

    # Prefer banking the surplus over destroying it. Suite 1100 has grown
    # storage this way for 100+ live runs: use a chest that still has room,
    # and when they are all full build another double chest rather than
    # dropping. Production had no notion of a chest being full and no way to
    # add capacity, so the only recourse was the discard tiers below -- which
    # is why bots shed hard-won ore on the ground every time they filled up.
    if _store_surplus_in_chest(client, required):
        return True

    print(
        f"  Inventory needs {required} free slot(s); clearing verified "
        "low-value stacks..."
    )
    discard_tiers = (
        [
            "minecraft:rotten_flesh",
            "minecraft:poisonous_potato",
            "minecraft:spider_eye",
            "minecraft:wildflowers",
            "minecraft:leaf_litter",
            "minecraft:wheat_seeds",
            "minecraft:beetroot_seeds",
            "minecraft:melon_seeds",
            "minecraft:pumpkin_seeds",
            *inventory.DISPOSABLE_CLUTTER_ITEMS,
            "minecraft:oak_leaves",
            "minecraft:birch_leaves",
            "minecraft:spruce_leaves",
            "minecraft:feather",
            "minecraft:moss_carpet",
            "minecraft:mangrove_roots",
            "minecraft:pointed_dripstone",
            "minecraft:dripstone_block",
            "minecraft:smooth_basalt",
            "minecraft:calcite",
            "minecraft:jungle_pressure_plate",
            "minecraft:shears",
            "minecraft:dirt",
            "minecraft:gravel",
            "minecraft:diorite",
            "minecraft:andesite",
            "minecraft:granite",
            "minecraft:tuff",
        ],
        [
            # Copper is mined incidentally on the way to iron and diamond and
            # has no consumer in any phase, but it was absent from every tier,
            # so manage_inventory could never reclaim those slots. Live
            # 2026-08-02: Bot07 carried 640 raw copper (10 permanently locked
            # stacks) and Bot16 256; all four filled up, aborted the descent,
            # walked back to a chest and started over -- a roughly hourly cycle
            # that no amount of clearing by hand survived. Dropped after the
            # stone tier so a bot mining for iron sheds ore it cannot use
            # before it sheds building material it can.
            "minecraft:raw_copper",
            "minecraft:copper_ore",
            "minecraft:deepslate_copper_ore",
            "minecraft:cobblestone",
            "minecraft:deepslate",
            "minecraft:cobbled_deepslate",
        ],
    )
    retain_counts = {
        "minecraft:shears": 1,
        "minecraft:dirt": 32,
        "minecraft:cobblestone": 128,
        "minecraft:cobbled_deepslate": 64,
        "minecraft:deepslate": 64,
    }
    for candidates in discard_tiers:
        while free_inventory_slots(client) < required:
            needed = required - free_inventory_slots(client)
            if drop_items(
                client,
                candidates,
                max_stacks=needed,
                retain_counts=retain_counts,
            ) <= 0:
                break
        if free_inventory_slots(client) >= required:
            return True

    print(
        f"  Inventory cleanup failed: only {free_inventory_slots(client)}/"
        f"{required} required slots are free"
    )
    return False


def _resolve_raw_plank_helper() -> Callable[[Any, int], bool]:
    """Return the active inventory helper for raw-plank conversion.

    Tests (and future runtime patches) can monkeypatch
    ``inventory._ensure_raw_planks``. When unpatched, we fall back to a local
    implementation that uses this module's ``craft``/``gather_wood`` functions.
    """
    helper = getattr(inventory, "_ensure_raw_planks", _DEFAULT_INVENTORY_ENSURE_RAW_PLANKS)
    return helper


def _ensure_raw_planks_for_fuel(client, required_planks: int) -> bool:
    """Ensure enough carried planks by converting any carried logs directly.

    This path is used when ``inventory._ensure_raw_planks`` is not monkeypatched,
    because that implementation depends on local inventory module symbols that
    are harder to replace in unit tests.
    """
    required_planks = max(0, required_planks)
    current_planks = sum(count_item(client, item_id) for item_id in PLANK_ITEMS)
    if current_planks >= required_planks:
        return True

    missing = required_planks - current_planks
    while missing > 0:
        converted = False
        for log_id, plank_id in LOG_TO_PLANKS.items():
            available_logs = count_item(client, log_id)
            if available_logs <= 0:
                continue
            # Convert exactly what is needed, but do not exceed available stock.
            craft_count = min(missing, max(1, available_logs) * 4)
            if not craft(client, plank_id, craft_count):
                return False
            new_planks = sum(count_item(client, item_id) for item_id in PLANK_ITEMS)
            if new_planks >= required_planks:
                return True
            if new_planks <= current_planks:
                # Prevent live/integration loops when craft is misreported.
                return False
            current_planks = new_planks
            missing = required_planks - current_planks
            converted = True
        if not converted:
            return False
    return True


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


def _select_furnace_fuel_from_pool(client, smelt_count: int) -> Optional[str]:
    """Select fuel by counting same-family logs as potential conversion.

    This supports cases where mixed stack fuel is sufficient but no single
    carried stack meets the full batch requirement yet can be topped up from
    local logs (for example 8 jungle planks plus 2 jungle logs for 15 smelts).
    """
    needed = max(1, int(smelt_count))
    preferred = ["minecraft:coal", "minecraft:charcoal"]
    for plank_id in PLANK_ITEMS:
        required_items = math.ceil(needed / FURNACE_FUEL_SMELTS[plank_id])
        log_id = None
        for log, plank in LOG_TO_PLANKS.items():
            if plank == plank_id:
                log_id = log
                break
        if log_id is None:
            continue
        if count_item(client, plank_id) >= required_items:
            return plank_id
        # same-family logs can be converted deterministically into this plank type
        if count_item(client, plank_id) + count_item(client, log_id) * 4 >= required_items:
            return plank_id
    return None


def _ensure_fuel_family_planks(client, fuel_id: str, smelt_count: int) -> bool:
    """Ensure ``fuel_id`` is available in enough quantity for the requested smelt."""
    if fuel_id not in PLANK_ITEMS:
        return False
    required = max(1, math.ceil(max(1, int(smelt_count)) / FURNACE_FUEL_SMELTS[fuel_id]))
    log_id = None
    for log, plank in LOG_TO_PLANKS.items():
        if plank == fuel_id:
            log_id = log
            break
    if log_id is None:
        return False

    current = count_item(client, fuel_id)
    while current < required:
        stock = count_item(client, log_id)
        if stock <= 0:
            return False
        to_craft = min(required - current, stock * 4)
        if not craft(client, fuel_id, to_craft):
            return False
        next_count = count_item(client, fuel_id)
        if next_count <= current:
            return False
        current = next_count
    return True


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
    # Gather a minimal daylight stock only when needed; conversion is now
    # delegated to the cross-family plank helper.
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
    ensure_raw_planks = _resolve_raw_plank_helper()
    if ensure_raw_planks is _DEFAULT_INVENTORY_ENSURE_RAW_PLANKS:
        if not _ensure_raw_planks_for_fuel(client, required_planks):
            print("  Wood fuel preparation failed after consolidation.")
            return None
    elif not ensure_raw_planks(client, required_planks):
        print("  Wood fuel preparation failed after consolidation.")
        return None

    # At this point we may have enough total planks, but still need one
    # usable stack type. Prefer the first family that can be completed with
    # carried same-family logs.
    fuel_id = _select_furnace_fuel_from_pool(client, smelt_count)
    if fuel_id is None:
        print("  Wood fuel preparation failed after consolidation.")
        return None
    if count_item(client, fuel_id) < math.ceil(
        max(1, int(smelt_count)) / FURNACE_FUEL_SMELTS[fuel_id]
    ):
        if not _ensure_fuel_family_planks(client, fuel_id, smelt_count):
            print("  Wood fuel preparation failed after consolidation.")
            return None

    return fuel_id


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

    # A previous attempt may have completed server-side after its screen read
    # timed out. Recover that durable output before gathering more ore or
    # leaving the workstation to prepare another fuel stack.
    if collect_finished_furnace_output(client, item_id, qty, furnace_pos):
        return True
    needed = qty - count_item(client, item_id)
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
            # A furnace is a 3x3 recipe. The raw craft route can fall back to
            # making a table but does not acquire/convert its four planks;
            # live Bot16 carried 42 logs and still repeated "any_planks:0"
            # for the full supply timeout. The table-aware route converts a
            # carried log first and then verifies the workstation craft.
            if not _craft_with_table(client, "minecraft:furnace", 1):
                return False

        print("  Placing furnace after fuel gathering is complete...")
        pos = get_player_pos(client)
        import math
        px, py, pz = math.floor(float(pos[0])), math.floor(float(pos[1])), math.floor(float(pos[2]))
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
    state = _read_state_optional(client, retries=3, label="Furnace distance state")
    if state is None:
        return False
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

    if collect_finished_furnace_output(client, item_id, qty, fpos):
        return True

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
        # Live blocker: a bot with 0 tools/materials after a death bootstraps
        # via this exact loop (ensure_supplies({"minecraft:wooden_pickaxe": 1})
        # from _ensure_mining_pickaxe). Without checking is_dead/health/food
        # here, a dead or critical player looped this for its full 600s
        # timeout -- gather_wood's own internal health gate aborted every
        # attempt ("Wood gathering stopped below safe health") with nothing in
        # THIS loop ever trying to recover health or even notice the corpse.
        # Confirmed live: Bot10 stuck retrying a pickaxe craft while
        # get_state().is_dead was already True.
        transport = getattr(client, "transport", None)
        if transport is not None:
            state, _ = _read_state_with_retry(
                client, retries=2, label="Ensure supplies survival check"
            )
            if state is not None:
                health = float(state.get("health", 20) or 0)
                food = int(state.get("food_level", state.get("food", 20)) or 0)
                if state.get("is_dead", False) or health <= 0:
                    transport.dispatch("cancel", {})
                    raise PlayerDeathDetected("player died while ensuring supplies")
                if health < 12.0:
                    from .combat import recover_health

                    transport.dispatch("cancel", {})
                    if not recover_health(client, minimum_health=12.0, timeout=10.0):
                        raise SurvivalRecoveryRequired(
                            f"health remains {health:.1f}/20 while ensuring supplies"
                        )
                    continue
                if food <= 10:
                    from .combat import eat_until_hunger

                    transport.dispatch("cancel", {})
                    if not eat_until_hunger(client, minimum_food=12):
                        raise SurvivalRecoveryRequired(
                            f"food remains {food}/20 while ensuring supplies"
                        )
                    continue

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
