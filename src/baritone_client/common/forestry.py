"""Bounded production forestry for a dedicated fleet wood supplier.

The long-running Suite 1100 prototype proved the useful shape of the loop:
harvest a small batch, replant carried saplings on verified natural ground,
then put the output in durable storage.  This module keeps that behavior in
the production controller without importing or bypassing the functional-test
world safety harness.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from typing import Any, Iterable, Mapping, Optional

from .base import robust_place
from .inventory import (
    deposit_excess_to_chest,
    get_inventory,
    resolve_storage_location,
)
from .navigation import goto
from .resources import LOG_BLOCKS, PLANK_ITEMS, ensure_supplies, gather_wood


SAPLING_ITEMS = (
    "minecraft:oak_sapling",
    "minecraft:spruce_sapling",
    "minecraft:birch_sapling",
    "minecraft:jungle_sapling",
    "minecraft:acacia_sapling",
    "minecraft:cherry_sapling",
    "minecraft:mangrove_propagule",
)

_GROUND_BLOCKS = {
    "minecraft:grass_block",
    "minecraft:dirt",
    "minecraft:coarse_dirt",
    "minecraft:podzol",
    "minecraft:rooted_dirt",
    "minecraft:mycelium",
}
_REPLACEABLE_TOKENS = (
    "air",
    "grass",
    "flower",
    "fern",
    "snow",
    "vine",
    "leaf_litter",
)


@dataclass(frozen=True)
class WoodCycleResult:
    """Verified outcomes from one harvest, replant, and storage pass."""

    success: bool
    detail: str
    logs_harvested: int = 0
    saplings_planted: int = 0
    logs_banked: int = 0
    total_logs_banked: int = 0


def _unwrap(response: Any) -> Mapping[str, Any]:
    if not isinstance(response, Mapping):
        return {}
    data = response.get("data", response)
    return data if isinstance(data, Mapping) else {}


def _count_logs(inventory: Mapping[str, Any]) -> int:
    return sum(max(0, int(inventory.get(item_id, 0) or 0)) for item_id in LOG_BLOCKS)


def _log_equivalents(inventory: Mapping[str, Any]) -> int:
    logs = _count_logs(inventory)
    planks = sum(
        max(0, int(inventory.get(item_id, 0) or 0)) for item_id in PLANK_ITEMS
    )
    return logs + planks // 4


def _position(snapshot: Mapping[str, Any]) -> tuple[int, int, int]:
    raw = snapshot.get("block_position", snapshot.get("position", {}))
    raw = raw if isinstance(raw, Mapping) else {}
    return (
        int(float(raw.get("x", 0) or 0)),
        int(float(raw.get("y", 64) or 64)),
        int(float(raw.get("z", 0) or 0)),
    )


def _base_origin(state: Any) -> Optional[tuple[int, int, int]]:
    custom = getattr(state, "custom_data", {}) or {}
    for key in ("base_build_origin", "starter_house_origin"):
        value = custom.get(key)
        if isinstance(value, (list, tuple)) and len(value) == 3:
            try:
                return tuple(int(float(axis)) for axis in value)  # type: ignore[return-value]
            except (TypeError, ValueError):
                continue
    return None


def find_sapling_spots(
    voxels: Iterable[Mapping[str, Any]],
    center: tuple[int, int, int],
    *,
    maximum: int = 8,
    base_origin: Optional[tuple[int, int, int]] = None,
    base_clearance: float = 10.0,
    tree_spacing: float = 3.0,
    vertical_clearance: int = 8,
) -> list[tuple[int, int, int]]:
    """Select loaded, naturally supported planting cells from one view.

    The selector is intentionally pure so the same terrain rules can be
    regression-tested without a live Minecraft client. Missing cells in a
    bridge view are treated as air; only a known obstruction rejects the
    vertical growth column.
    """

    if maximum <= 0:
        return []
    blocks: dict[tuple[int, int, int], str] = {}
    ground: dict[tuple[int, int], int] = {}
    occupied_trees: list[tuple[int, int]] = []
    for voxel in voxels:
        if not all(axis in voxel for axis in ("x", "y", "z")):
            continue
        x, y, z = (int(voxel[axis]) for axis in ("x", "y", "z"))
        block_id = str(voxel.get("id", ""))
        blocks[(x, y, z)] = block_id
        if block_id in _GROUND_BLOCKS:
            ground[(x, z)] = max(y, ground.get((x, z), -64))
        if (
            block_id.endswith("_sapling")
            or block_id == "minecraft:mangrove_propagule"
            or block_id.endswith("_log")
            or block_id.endswith("_stem")
        ):
            occupied_trees.append((x, z))

    cx, _cy, cz = center
    candidates: list[tuple[float, int, int, int]] = []
    for (x, z), ground_y in ground.items():
        distance = hypot(x - cx, z - cz)
        if distance < 5.0 or distance > 30.0:
            continue
        if (
            base_origin is not None
            and hypot(x - base_origin[0], z - base_origin[2]) < base_clearance
        ):
            continue
        if any(hypot(x - tx, z - tz) < tree_spacing for tx, tz in occupied_trees):
            continue
        plant_y = ground_y + 1
        blocked = False
        for dy in range(vertical_clearance):
            block_id = blocks.get((x, plant_y + dy, z), "")
            if block_id and not any(token in block_id for token in _REPLACEABLE_TOKENS):
                blocked = True
                break
        if blocked:
            continue
        candidates.append((distance, x, plant_y, z))

    selected: list[tuple[int, int, int]] = []
    for _distance, x, y, z in sorted(candidates):
        if any(hypot(x - sx, z - sz) < tree_spacing for sx, _sy, sz in selected):
            continue
        selected.append((x, y, z))
        if len(selected) >= maximum:
            break
    return selected


def plant_carried_saplings(client: Any, state: Any, *, maximum: int = 8) -> int:
    """Plant carried single-tree saplings in the currently loaded work area."""

    inventory = get_inventory(client)
    available = [
        item_id
        for item_id in SAPLING_ITEMS
        for _ in range(max(0, int(inventory.get(item_id, 0) or 0)))
    ]
    if not available or maximum <= 0:
        return 0

    snapshot = _unwrap(client.transport.dispatch("get_state", {}))
    if (
        snapshot.get("is_dead")
        or float(snapshot.get("health", 0) or 0) < 16.0
        or int(snapshot.get("food_level", snapshot.get("food", 0)) or 0) < 14
        or int(snapshot.get("world_time", 0) or 0) % 24000 >= 11000
    ):
        return 0
    view = _unwrap(client.transport.dispatch("get_view", {"radius": 32}))
    spots = find_sapling_spots(
        view.get("voxels", []),
        _position(snapshot),
        maximum=min(maximum, len(available)),
        base_origin=_base_origin(state),
    )
    planted = 0
    for item_id, (x, y, z) in zip(available, spots):
        live = _unwrap(client.transport.dispatch("get_state", {}))
        if (
            live.get("is_dead")
            or float(live.get("health", 0) or 0) < 16.0
            or int(live.get("world_time", 0) or 0) % 24000 >= 11000
        ):
            break
        before_count = int(get_inventory(client).get(item_id, 0) or 0)
        if robust_place(client, x, y, z, item_id) and int(
            get_inventory(client).get(item_id, 0) or 0
        ) < before_count:
            planted += 1
    return planted


def _worker(state: Any) -> dict[str, Any]:
    custom = getattr(state, "custom_data", None)
    if not isinstance(custom, dict):
        custom = {}
        state.custom_data = custom
    worker = custom.setdefault("wood_worker", {})
    if not isinstance(worker, dict):
        worker = {}
        custom["wood_worker"] = worker
    return worker


def _tree_frontier(snapshot: Mapping[str, Any], cursor: int) -> tuple[int, int, int]:
    """Return one bounded, changing surface-search waypoint per cursor."""
    x, y, z = _position(snapshot)
    # Every leg is deliberately local. Further exploration is earned only by
    # a later cycle starting from a verified arrival, never by a growing goal.
    radius = 32
    dx, dz = ((radius, 0), (0, radius), (-radius, 0), (0, -radius))[cursor % 4]
    return x + dx, y, z + dz


def _recover_no_wood_progress(
    client: Any, state: Any, snapshot: Mapping[str, Any]
) -> tuple[str, int]:
    """Advance one bounded recovery instead of replaying the same harvest."""
    worker = _worker(state)
    cursor = int(worker.get("escalation_cursor", 0) or 0)
    worker["escalation_cursor"] = cursor + 1
    action = cursor % 3
    if action == 0:
        ensure_supplies(client, {"minecraft:stone_axe": 1}, timeout=120)
        # Tool repair is useful only when inventory proves it; this is not wood output.
        has_axe = any(
            "_axe" in item and int(count or 0) > 0
            for item, count in get_inventory(client).items()
        )
        return (
            "confirmed a usable axe after bounded repair"
            if has_axe
            else "attempted bounded axe repair",
            0,
        )
    if action == 1:
        before = sum(int(get_inventory(client).get(item, 0) or 0) for item in SAPLING_ITEMS)
        ensure_supplies(client, {"minecraft:oak_sapling": 4}, timeout=120)
        after = sum(int(get_inventory(client).get(item, 0) or 0) for item in SAPLING_ITEMS)
        planted = plant_carried_saplings(client, state, maximum=4)
        if planted:
            worker["saplings_planted"] = int(worker.get("saplings_planted", 0) or 0) + planted
            return f"expanded verified plantation by {planted} saplings", planted
        return (
            "attempted bounded sapling acquisition"
            if after <= before
            else "acquired saplings but no verified planting site",
            0,
        )
    waypoint = _tree_frontier(snapshot, cursor)
    reached = goto(
        client, *waypoint, timeout=45, tolerance=3.0,
        check_interval=1.0,
    )
    detail = (
        f"reached bounded tree-search frontier at {waypoint}"
        if reached
        else f"attempted bounded tree-search frontier at {waypoint}"
    )
    return detail, 0


def run_wood_cycle(
    client: Any,
    state: Any,
    *,
    harvest_target: int = 24,
    maximum_saplings: int = 8,
) -> WoodCycleResult:
    """Harvest, replant, and bank one bounded batch of renewable wood."""

    worker = _worker(state)
    worker["attempts"] = int(worker.get("attempts", 0) or 0) + 1
    snapshot = _unwrap(client.transport.dispatch("get_state", {}))
    if (
        snapshot.get("is_dead")
        or "overworld" not in str(snapshot.get("dimension", ""))
        or float(snapshot.get("health", 0) or 0) < 16.0
        or int(snapshot.get("food_level", snapshot.get("food", 0)) or 0) < 14
        or int(snapshot.get("world_time", 0) or 0) % 24000 >= 11000
    ):
        return WoodCycleResult(False, "survival or daylight margin is not ready")

    before = get_inventory(client)
    before_logs = _count_logs(before)
    target = _log_equivalents(before) + max(1, int(harvest_target))
    gathered = gather_wood(
        client,
        count=target,
        timeout=300,
        latest_world_time=11000,
        max_distance_from_origin=64.0,
        abort_on_threats=True,
        minimum_health=16.0,
    )
    after_harvest = get_inventory(client)
    harvested = max(0, _count_logs(after_harvest) - before_logs)
    if not gathered and harvested <= 0:
        worker["no_progress"] = int(worker.get("no_progress", 0) or 0) + 1
        recovery, planted = _recover_no_wood_progress(client, state, snapshot)
        worker["last_no_progress_reason"] = "no logs harvested before bounded gather stopped"
        return WoodCycleResult(
            bool(planted),
            f"no logs were harvested before the bounded gather stopped; {recovery}",
            saplings_planted=planted,
        )

    planted = plant_carried_saplings(client, state, maximum=maximum_saplings)
    chest = resolve_storage_location(client, state=state, verify=False)
    if chest is None:
        return WoodCycleResult(
            False,
            "harvested wood but no checkpointed storage was available",
            logs_harvested=harvested,
            saplings_planted=planted,
        )
    deposited = deposit_excess_to_chest(
        client,
        chest,
        deposit_items=set(LOG_BLOCKS),
        state=state,
    )
    if deposited < 0:
        return WoodCycleResult(
            False,
            "harvested wood but the storage deposit was not verified",
            logs_harvested=harvested,
            saplings_planted=planted,
        )

    after_storage = get_inventory(client)
    banked = max(0, _count_logs(after_harvest) - _count_logs(after_storage))
    stats = worker
    stats["cycles"] = int(stats.get("cycles", 0) or 0) + 1
    stats["logs_harvested"] = int(stats.get("logs_harvested", 0) or 0) + harvested
    stats["saplings_planted"] = int(stats.get("saplings_planted", 0) or 0) + planted
    stats["logs_banked"] = int(stats.get("logs_banked", 0) or 0) + banked
    detail = (
        f"harvested {harvested} logs, planted {planted} saplings, "
        f"banked {banked} logs"
    )
    success = bool(harvested > 0 and banked > 0)
    if success:
        stats["no_progress"] = 0
    return WoodCycleResult(
        success,
        detail,
        logs_harvested=harvested,
        saplings_planted=planted,
        logs_banked=banked,
        total_logs_banked=int(stats["logs_banked"]),
    )


__all__ = [
    "SAPLING_ITEMS",
    "WoodCycleResult",
    "find_sapling_spots",
    "plant_carried_saplings",
    "run_wood_cycle",
]
