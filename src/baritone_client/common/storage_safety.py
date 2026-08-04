"""Survival and locality policy for automated storage travel."""

from functools import partial
from math import dist
from typing import Any, Dict, Iterable, Tuple


MIN_STORAGE_TRAVEL_HEALTH = 18.0
MIN_STORAGE_TRAVEL_FOOD = 18
MAX_STORAGE_TRAVEL_DISTANCE = 96.0
MAX_STORAGE_TOUR_STOPS = 4


def storage_travel_safe(snapshot: Dict[str, Any]) -> bool:
    """Return whether a player can safely defer survival work for storage."""
    health = float(snapshot.get("health", 20) or 0)
    food = int(snapshot.get("food_level", snapshot.get("food", 20)) or 0)
    return (
        not bool(snapshot.get("is_dead", False))
        and health >= MIN_STORAGE_TRAVEL_HEALTH
        and food >= MIN_STORAGE_TRAVEL_FOOD
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
    if storage_travel_safe(snapshot):
        return
    client._storage_survival_abort = True
    client.transport.dispatch("cancel", {})
    print("STORAGE: cancelling travel outside health/hunger safety margin")


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
    candidates = []
    for row in catalog_for(client).list_containers():
        try:
            position = (int(row["x"]), int(row["y"]), int(row["z"]))
        except (KeyError, TypeError, ValueError):
            continue
        if str(row.get("dimension", dimension)) != dimension:
            continue
        distance = storage_distance(snapshot, position)
        if distance <= maximum_distance:
            candidates.append((distance, position))
    return [position for _distance, position in sorted(candidates)[:limit]]


def load_storage_chunk(client, target: Tuple[int, int, int], goto) -> bool:
    """Reach a persisted container using bounded, survival-guarded path legs."""
    cx, cy, cz = target
    remaining = storage_distance(client.transport.dispatch("get_state", {}), target)
    reached = remaining <= 3.0
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
            tolerance=3.0,
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
        if next_remaining <= 3.0:
            reached = True
            break
        if progress < 4.0:
            break
        remaining = next_remaining
    return reached


def store_surplus_in_chest(client, required: int) -> bool:
    """Bank surplus in nearby storage, growing capacity when necessary."""
    from . import harness_ops
    from .inventory import deposit_excess_to_chest, free_inventory_slots

    if not harness_ops.available():
        return False
    try:
        snapshot = client.transport.dispatch("get_state", {})
    except Exception:
        snapshot = {}
    if not storage_travel_safe(snapshot):
        print("  STORAGE: cleanup travel deferred for survival recovery")
        return False

    try:
        # list_containers already excludes status='missing'.
        containers = nearby_storage_positions(client, snapshot)
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
            if deposit_excess_to_chest(client, tuple(position)) > 0:
                if free_inventory_slots(client) >= required:
                    return True
        except Exception as exc:
            print(f"  STORAGE: deposit to {tuple(position)} failed ({exc})")

    try:
        created = harness_ops.create_double_chest(client)
    except Exception as exc:
        print(f"  STORAGE: could not build overflow storage ({exc})")
        return False
    if not created:
        return False
    try:
        deposit_excess_to_chest(client, tuple(created[0]))
    except Exception as exc:
        print(f"  STORAGE: deposit to new double chest failed ({exc})")
        return False
    return free_inventory_slots(client) >= required
