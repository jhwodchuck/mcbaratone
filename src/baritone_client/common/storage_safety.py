"""Survival and locality policy for automated storage travel."""

from functools import partial
from math import dist
import time
from typing import Any, Dict, Iterable, Tuple


# Starting a bounded (<=96m) trip to bank what the bot is already carrying.
#
# These were 18.0/18 -- effectively "must be at full health and full regen
# capability". Any bot that had recently been hit by anything failed the check,
# so on 2026-08-06 Bot19 stood 14 blocks from home holding 547 logs it had cut
# and could not bank them; it had completed 5 deposits all day against Bot07's
# 4,249. Depositing is the risk-*reducing* move for cargo: a wounded bot that
# dies mid-trip loses what it was already going to lose, while a successful
# trip banks it permanently. The trip is distance-capped and aborts on tick, so
# the floor only needs to exclude bots that must eat or flee first.
MIN_STORAGE_TRAVEL_HEALTH = 10.0
MIN_STORAGE_TRAVEL_FOOD = 6
# Abandoning a trip already in progress. Deliberately below the start floor:
# with one shared threshold a bot that set out at exactly the limit aborted on
# the first point of damage, healed back, set out again, and flapped. Hysteresis
# means an in-flight deposit is only abandoned when the bot is genuinely in
# danger rather than merely scuffed.
ABORT_STORAGE_TRAVEL_HEALTH = 6.0
ABORT_STORAGE_TRAVEL_FOOD = 3
# Eat back up to this when restoring a margin; comfort, not a precondition.
COMFORTABLE_STORAGE_TRAVEL_FOOD = 18
MAX_STORAGE_TRAVEL_DISTANCE = 96.0
MAX_STORAGE_TOUR_STOPS = 4
STORAGE_CHUNK_LOAD_RADIUS = 4.5
UNREACHABLE_STORAGE_COOLDOWN = 300.0
OVERFLOW_BULK_ITEMS = {
    "minecraft:basalt",
    "minecraft:blackstone",
    "minecraft:cobblestone",
    "minecraft:glowstone_dust",
    "minecraft:lapis_lazuli",
    "minecraft:magma_block",
    "minecraft:mangrove_roots",
    "minecraft:mossy_cobblestone",
    "minecraft:moss_carpet",
    "minecraft:muddy_mangrove_roots",
    "minecraft:netherrack",
    "minecraft:quartz",
    "minecraft:raw_copper",
    "minecraft:soul_sand",
}
OVERFLOW_RETAIN_COUNTS = {
    "minecraft:blackstone": 32,
    "minecraft:cobblestone": 64,
    "minecraft:netherrack": 32,
}


def storage_travel_safe(snapshot: Dict[str, Any]) -> bool:
    """Return whether a player can safely defer survival work for storage."""
    health = float(snapshot.get("health", 20) or 0)
    food = int(snapshot.get("food_level", snapshot.get("food", 20)) or 0)
    return (
        not bool(snapshot.get("is_dead", False))
        and health >= MIN_STORAGE_TRAVEL_HEALTH
        and food >= MIN_STORAGE_TRAVEL_FOOD
    )


def storage_travel_must_abort(snapshot: Dict[str, Any]) -> bool:
    """Return whether an in-flight storage trip has to be given up now."""
    health = float(snapshot.get("health", 20) or 0)
    food = int(snapshot.get("food_level", snapshot.get("food", 20)) or 0)
    return (
        bool(snapshot.get("is_dead", False))
        or health < ABORT_STORAGE_TRAVEL_HEALTH
        or food < ABORT_STORAGE_TRAVEL_FOOD
    )


def storage_distance(snapshot: Dict[str, Any], target: Tuple[int, int, int]) -> float:
    """Measure three-dimensional distance from a bridge snapshot to a target."""
    position = snapshot.get("block_position", snapshot.get("position", {}))
    current = (
        float(position.get("x", 0)),
        float(position.get("y", 64)),
        float(position.get("z", 0)),
    )
    return dist(current, target)


def cancel_unsafe_storage_travel(client) -> None:
    """Cancel an active storage path once its survival margin is exhausted."""
    snapshot = client.transport.dispatch("get_state", {})
    if not storage_travel_must_abort(snapshot):
        return
    client._storage_survival_abort = True
    client.transport.dispatch("cancel", {})
    print("STORAGE: cancelling travel outside health/hunger safety margin")


def restore_storage_travel_margin(client) -> bool:
    """Eat and regenerate before retrying storage work at a reduced margin."""
    from .combat import eat_until_hunger
    from .health_recovery import recover_health

    try:
        snapshot = client.transport.dispatch("get_state", {})
    except Exception:
        return False
    if storage_travel_safe(snapshot):
        return True
    # Eat to comfort, but only require the travel floor: insisting on a full
    # regen margin here is what burned 45s and then refused the trip anyway.
    eat_until_hunger(client, minimum_food=COMFORTABLE_STORAGE_TRAVEL_FOOD)
    recover_health(
        client,
        minimum_health=MIN_STORAGE_TRAVEL_HEALTH,
        timeout=45.0,
    )
    try:
        return storage_travel_safe(client.transport.dispatch("get_state", {}))
    except Exception:
        return False


def remember_unreachable_storage(client, position: Tuple[int, int, int]) -> None:
    """Suppress a failed catalog destination for a bounded retry interval."""
    cooldowns = getattr(client, "_unreachable_storage_until", {})
    cooldowns[tuple(position)] = time.monotonic() + UNREACHABLE_STORAGE_COOLDOWN
    client._unreachable_storage_until = cooldowns


def storage_retry_ready(client, position: Tuple[int, int, int]) -> bool:
    """Return whether a failed storage destination may be attempted again."""
    cooldowns = getattr(client, "_unreachable_storage_until", {})
    return float(cooldowns.get(tuple(position), 0)) <= time.monotonic()


def nearby_storage_positions(
    client,
    snapshot: Dict[str, Any],
    *,
    maximum_distance: float = MAX_STORAGE_TRAVEL_DISTANCE,
    limit: int = MAX_STORAGE_TOUR_STOPS,
) -> Iterable[Tuple[int, int, int]]:
    """Return the nearest bounded set of catalog containers in this dimension."""
    from .storage_catalog import catalog_for

    dimension = str(snapshot.get("dimension", "minecraft:overworld"))
    now = time.monotonic()
    cooldowns = getattr(client, "_unreachable_storage_until", {})
    candidates = []
    for row in catalog_for(client).list_containers():
        try:
            position = (int(row["x"]), int(row["y"]), int(row["z"]))
        except (KeyError, TypeError, ValueError):
            continue
        if str(row.get("dimension", dimension)) != dimension:
            continue
        if float(cooldowns.get(position, 0)) > now:
            continue
        distance = storage_distance(snapshot, position)
        if distance <= maximum_distance:
            candidates.append((distance, position))
    return [position for _distance, position in sorted(candidates)[:limit]]


def load_storage_chunk(client, target: Tuple[int, int, int], goto) -> bool:
    """Reach a persisted container using bounded, survival-guarded path legs."""
    cx, cy, cz = target
    remaining = storage_distance(client.transport.dispatch("get_state", {}), target)
    reached = remaining <= STORAGE_CHUNK_LOAD_RADIUS
    for leg in range(1, 9):
        if reached:
            break
        leg_timeout = max(30, min(120, int(remaining / 2.0) + 20))
        arrived = goto(
            client,
            cx,
            cy,
            cz,
            timeout=leg_timeout,
            check_interval=0.5,
            tolerance=STORAGE_CHUNK_LOAD_RADIUS,
            on_tick=partial(cancel_unsafe_storage_travel, client),
        )
        if getattr(client, "_storage_survival_abort", False):
            print("STORAGE: aborted chest return for survival recovery")
            return False
        if arrived:
            reached = True
            break

        next_remaining = storage_distance(
            client.transport.dispatch("get_state", {}), target
        )
        progress = remaining - next_remaining
        print(
            f"STORAGE: chest return leg {leg} moved {progress:.1f} blocks; "
            f"{next_remaining:.1f} remain"
        )
        if next_remaining <= STORAGE_CHUNK_LOAD_RADIUS:
            reached = True
            break
        if progress < 4.0:
            break
        remaining = next_remaining
    if not reached:
        remember_unreachable_storage(client, target)
    return reached


def create_overflow_storage(client, harness_ops):
    """Build double storage when possible, or one emergency carried chest."""
    from .inventory import count_item

    chest_count = count_item(client, "minecraft:chest")
    if chest_count < 1:
        return None
    first = harness_ops.find_single_chest_spot(client)
    try:
        live = client.transport.dispatch("get_state", {})
        adjacent = first is not None and storage_distance(live, first) <= 4.5
    except Exception:
        adjacent = False
    if adjacent and harness_ops.place_block_exact(
        client, first[0], first[1], first[2], "minecraft:chest"
    ):
        print(f"  STORAGE: built adjacent overflow chest at {tuple(first)}")
        return (tuple(first),)

    if chest_count >= 2:
        created = harness_ops.create_double_chest(client)
        if created:
            return tuple(created)

    if not first:
        print("  STORAGE: no room for a single overflow chest nearby")
        return None
    first = tuple(first)
    if not harness_ops.move_near(client, *first, timeout=20.0):
        return None
    if not harness_ops.place_block_exact(
        client, first[0], first[1], first[2], "minecraft:chest"
    ):
        print(f"  STORAGE: failed to place single chest at {first}")
        return None
    print(f"  STORAGE: built single overflow chest at {first}")
    return (first,)


def store_surplus_in_chest(
    client,
    required: int,
    *,
    deposit_items=None,
    retain_counts=None,
    max_stops: int = MAX_STORAGE_TOUR_STOPS,
) -> bool:
    """Bank surplus in nearby storage, growing capacity when necessary.

    Role workers may extend the normal cleanup allow-list with their own bulk
    product. Forestry needs this because logs are valuable output rather than
    generic clutter, but a full inventory of logs still has to be banked
    before another harvest can begin.
    """
    from . import harness_ops
    from .inventory import (
        EARLY_GAME_EXCESS_ITEMS,
        deposit_excess_to_chest,
        free_inventory_slots,
    )

    from .space_reclaim import (
        SPACE_RECLAIM_RETAIN_COUNTS,
        carried_items,
        space_reclaim_deposit_items,
    )

    selected_items = EARLY_GAME_EXCESS_ITEMS | OVERFLOW_BULK_ITEMS
    if deposit_items:
        selected_items |= set(deposit_items)
    selected_retains = dict(OVERFLOW_RETAIN_COUNTS)
    selected_retains.update(retain_counts or {})

    # An allow-list can only bank what it named in advance, and a bot picks up
    # whatever it happens to mine. A1Bot ended up with 33 carried item types of
    # which exactly one was listed, so every deposit freed at most a single
    # slot and the inventory never fell below full.
    #
    # The test is whether the named set can free ENOUGH slots, not whether it
    # can move anything at all: A1Bot carried 3 leaf_litter, which is on the
    # list, so a "can it move something" check would have passed while still
    # leaving 1 of the 3 required slots free and the deadlock intact.
    carried = carried_items(client)
    allow_list_slots = sum(
        1
        for item_id, count in carried.items()
        if item_id in selected_items
        and int(count or 0) > selected_retains.get(item_id, 0)
    )
    if carried and allow_list_slots < required:
        reclaim = space_reclaim_deposit_items(carried)
        if reclaim:
            print(
                f"  STORAGE: allow-list frees only {allow_list_slots}/{required} "
                f"slot(s); banking {len(reclaim)} unprotected item type(s) too"
            )
            selected_items |= reclaim
            for item_id, floor in SPACE_RECLAIM_RETAIN_COUNTS.items():
                selected_retains.setdefault(item_id, floor)

    def deposit(position) -> int:
        return deposit_excess_to_chest(
            client,
            tuple(position),
            deposit_items=selected_items,
            retain_counts=selected_retains,
        )

    if not harness_ops.available():
        return False
    try:
        snapshot = client.transport.dispatch("get_state", {})
    except Exception:
        print("  STORAGE: cleanup state unavailable; deferring travel")
        return False
    if not storage_travel_safe(snapshot) and not restore_storage_travel_margin(client):
        print("  STORAGE: cleanup travel deferred for survival recovery")
        return False

    try:
        # list_containers already excludes status='missing'.
        if max_stops == MAX_STORAGE_TOUR_STOPS:
            containers = nearby_storage_positions(client, snapshot)
        else:
            containers = nearby_storage_positions(
                client, snapshot, limit=max(1, int(max_stops))
            )
    except Exception as exc:
        print(f"  STORAGE: container list unavailable ({exc})")
        containers = []

    for position in containers:
        try:
            live = client.transport.dispatch("get_state", {})
        except Exception:
            live = snapshot
        if not storage_travel_safe(live):
            print("  STORAGE: stopping cleanup tour for survival recovery")
            return False
        try:
            if harness_ops.chest_is_full(client, position):
                continue
            if deposit(position) > 0:
                if free_inventory_slots(client) >= required:
                    return True
        except Exception as exc:
            print(f"  STORAGE: deposit to {tuple(position)} failed ({exc})")

    try:
        created = create_overflow_storage(client, harness_ops)
    except Exception as exc:
        print(f"  STORAGE: could not build overflow storage ({exc})")
        return False
    if not created:
        return False
    if not restore_storage_travel_margin(client):
        print("  STORAGE: new chest deposit deferred for survival recovery")
        return False
    try:
        deposit(created[0])
    except Exception as exc:
        print(f"  STORAGE: deposit to new double chest failed ({exc})")
        return False
    return free_inventory_slots(client) >= required
