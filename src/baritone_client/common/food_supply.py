"""Bounded, repeatable wheat-to-bread supply work for survival roles."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, hypot, sqrt
from typing import Any, Mapping, Optional, Sequence, Tuple

from .combat import eat_until_hunger
from .farming import establish_wheat_farm, harvest_wheat_farm
from .inventory import (
    craft,
    deposit_excess_to_chest,
    get_inventory,
    resolve_storage_location,
)


WHEAT = "minecraft:wheat"
SEEDS = "minecraft:wheat_seeds"
BREAD = "minecraft:bread"


@dataclass(frozen=True)
class FoodCycleResult:
    """Only inventory- or world-witnessed work credited by one food cycle."""

    success: bool
    detail: str
    cycles: int = 0
    plots: int = 0
    crop_tiles: int = 0
    wheat_harvested: int = 0
    crops_replanted: int = 0
    bread_crafted: int = 0
    prepared_food_banked: int = 0
    total_plots: int = 0
    total_crop_tiles: int = 0
    total_wheat_harvested: int = 0
    total_crops_replanted: int = 0
    total_bread_crafted: int = 0
    total_prepared_food_banked: int = 0

    @property
    def total_food_banked(self) -> int:
        """Compatibility name for scheduler/team-supply consumers."""
        return self.total_prepared_food_banked


def _count(inventory: Mapping[str, Any], item: str) -> int:
    try:
        return max(0, int(inventory.get(item, 0) or 0))
    except (TypeError, ValueError):
        return 0


def _position(value: Any) -> Optional[Tuple[int, int, int]]:
    if isinstance(value, Mapping):
        value = value.get("origin", value.get("position"))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) >= 3:
        try:
            return tuple(int(value[i]) for i in range(3))
        except (TypeError, ValueError):
            return None
    return None


def _plots(state: Any) -> list[Tuple[int, int, int]]:
    custom = getattr(state, "custom_data", {}) or {}
    worker = custom.get("food_worker", {}) if isinstance(custom, Mapping) else {}
    records = worker.get("farm_plots", []) if isinstance(worker, Mapping) else []
    found = [_position(record) for record in records if _position(record)]
    # Earlier checkpoints only carried one wheat_farm origin.
    legacy = _position(custom.get("wheat_farm")) if isinstance(custom, Mapping) else None
    if legacy and legacy not in found:
        found.append(legacy)
    return found


def _anchor(state: Any, plots: Sequence[Tuple[int, int, int]]) -> Optional[Tuple[int, int, int]]:
    custom = getattr(state, "custom_data", {}) or {}
    if isinstance(custom, Mapping):
        for key in (
            "base_location",
            "homestead_anchor",
            "farm_location",
            "base",
            "storage",
            "home",
            "wheat_farm",
        ):
            candidate = _position(custom.get(key))
            if candidate:
                return candidate
        structures = custom.get("structures", {})
        if isinstance(structures, Mapping):
            for key in ("storage", "home_storage", "food_source"):
                candidate = _position(structures.get(key))
                if candidate:
                    return candidate
    return plots[0] if plots else None


def _candidate(
    anchor: Tuple[int, int, int],
    plots: Sequence[Tuple[int, int, int]],
    rejected: Sequence[Tuple[int, int, int]],
    distance: int,
    cursor: int,
    maximum_slots: int,
) -> Optional[Tuple[int, int, int]]:
    """Inspect a bounded rotating frontier; each verified plot adds four legs."""
    offsets = ((distance, 0), (0, distance), (-distance, 0), (0, -distance))
    frontier = list(plots) or [anchor]
    total = len(frontier) * len(offsets)
    for step in range(min(max(1, maximum_slots), total)):
        slot = (cursor + step) % total
        origin = frontier[slot // len(offsets)]
        dx, dz = offsets[slot % len(offsets)]
        candidate = (origin[0] + dx, origin[1], origin[2] + dz)
        if candidate in rejected:
            continue
        if all(
            hypot(candidate[0] - plot[0], candidate[2] - plot[2]) >= distance
            for plot in plots
        ):
            return candidate
    # A farm can be surrounded by water, rock, or protected construction. If
    # all four immediate legs fail, a frontier-only search would stop forever.
    # The persisted cursor therefore also advances through an unbounded square
    # grid around the durable anchor. Each invocation still tries only the
    # bounded number of slots above plus this single fallback coordinate.
    grid_x, grid_z = _grid_offset(cursor + len(offsets))
    candidate = (
        anchor[0] + grid_x * distance,
        anchor[1],
        anchor[2] + grid_z * distance,
    )
    if candidate not in rejected and all(
        hypot(candidate[0] - plot[0], candidate[2] - plot[2]) >= distance
        for plot in plots
    ):
        return candidate
    return None


def _grid_offset(slot: int) -> Tuple[int, int]:
    """Map a durable cursor to one unique non-origin square-grid coordinate."""
    index = max(0, int(slot))
    ring = max(1, ceil((sqrt(index + 2) - 1) / 2))
    previous = (2 * ring - 1) ** 2 - 1
    position = index - previous
    perimeter = [
        *((ring, z) for z in range(-ring, ring)),
        *((x, ring) for x in range(ring, -ring, -1)),
        *((-ring, z) for z in range(ring, -ring, -1)),
        *((x, -ring) for x in range(-ring, ring)),
    ]
    east = perimeter.index((ring, 0))
    perimeter = perimeter[east:] + perimeter[:east]
    return perimeter[position % len(perimeter)]


def _survival_ready(client: Any) -> bool:
    """Refuse farm travel from dead, wrong-dimension, or critically hurt state."""
    try:
        state = client.transport.dispatch("get_state", {})
        data = state.get("data", state) if isinstance(state, Mapping) else {}
        return (
            not bool(data.get("is_dead", data.get("dead", False)))
            and "overworld" in str(data.get("dimension", ""))
            and float(data.get("health", 0) or 0) >= 12.0
        )
    except Exception:
        return False


def _worker(state: Any) -> dict:
    custom = getattr(state, "custom_data", None)
    if not isinstance(custom, dict):
        custom = {}
        state.custom_data = custom
    worker = custom.setdefault("food_worker", {})
    if not isinstance(worker, dict):
        worker = {}
        custom["food_worker"] = worker
    worker.setdefault("farm_plots", [])
    worker.setdefault("failed_plot_sites", [])
    return worker


def _flush(state: Any, client: Any) -> None:
    """Persist durable coordination state without treating a flush as output."""
    save = getattr(state, "save_checkpoint", None)
    if callable(save):
        try:
            save(get_inventory(client))
        except Exception:
            pass


def _result(
    worker: Mapping[str, Any],
    success: bool,
    detail: str,
    **values: int,
) -> FoodCycleResult:
    return FoodCycleResult(
        success,
        detail,
        **values,
        total_plots=int(worker.get("plots", 0) or 0),
        total_crop_tiles=int(worker.get("crop_tiles", 0) or 0),
        total_wheat_harvested=int(worker.get("wheat_harvested", 0) or 0),
        total_crops_replanted=int(worker.get("crops_replanted", 0) or 0),
        total_bread_crafted=int(worker.get("bread_crafted", 0) or 0),
        total_prepared_food_banked=int(worker.get("prepared_food_banked", 0) or 0),
    )


def run_food_cycle(
    client: Any,
    state: Any,
    *,
    plot_size: int = 5,
    farm_range: int = 8,
    max_plot_distance: int = 32,
    max_plots_per_cycle: int = 3,
    max_candidate_slots: int = 3,
    personal_food_reserve: int = 8,
    seed_reserve: int = 8,
) -> FoodCycleResult:
    """Harvest known plots, craft/bank proven surplus, or add exactly one plot.

    This intentionally has no lifetime plot limit.  Each invocation has one
    short farm pass for a round-robin subset of known plots and may establish
    *one* irrigated plot near the durable base/farm anchor when none yield.
    """
    worker = _worker(state)
    worker["attempts"] = int(worker.get("attempts", 0) or 0) + 1
    if not _survival_ready(client):
        _flush(state, client)
        return _result(worker, False, "survival state is unsafe for farm travel")
    try:
        safe_to_work = eat_until_hunger(client, minimum_food=14)
    except Exception:
        safe_to_work = False
    if not safe_to_work:
        _flush(state, client)
        return _result(
            worker,
            False,
            "survival recovery could not restore food before farm travel",
        )
    known = _plots(state)
    worker["farm_plots"] = [{"origin": list(plot)} for plot in known]
    harvested = 0
    limit = max(1, int(max_plots_per_cycle))
    cursor = int(worker.get("plot_cursor", 0) or 0)
    inspected = (
        [
            known[(cursor + index) % len(known)]
            for index in range(min(limit, len(known)))
        ]
        if known
        else []
    )
    for plot in inspected:
        plot_before = _count(get_inventory(client), WHEAT)
        harvest_wheat_farm(client, *plot, range_=max(1, int(farm_range)))
        harvested += max(0, _count(get_inventory(client), WHEAT) - plot_before)
    if known:
        worker["plot_cursor"] = (cursor + len(inspected)) % len(known)

    new_plots = 0
    crop_tiles = 0
    replanted = 0
    if harvested == 0:
        anchor = _anchor(state, known)
        if anchor is not None:
            rejected = [_position(site) for site in worker["failed_plot_sites"]]
            rejected = [site for site in rejected if site]
            expansion_cursor = int(worker.get("expansion_cursor", 0) or 0)
            candidate = _candidate(
                anchor,
                known,
                rejected,
                max(8, int(max_plot_distance)),
                expansion_cursor,
                max_candidate_slots,
            )
            worker["expansion_cursor"] = expansion_cursor + 1
            established = (
                establish_wheat_farm(
                    client, *candidate, size=max(3, int(plot_size) | 1)
                )
                if candidate is not None
                else None
            )
            if established:
                # The primitive verifies irrigation and at least one planted tile.
                known.append(tuple(established))
                worker["farm_plots"] = [{"origin": list(plot)} for plot in known]
                custom = getattr(state, "custom_data", {})
                if isinstance(custom, dict) and not _position(custom.get("wheat_farm")):
                    custom["wheat_farm"] = {"origin": list(established)}
                new_plots = 1
                crop_tiles = 1
                replanted = 1
            elif candidate is not None:
                worker["failed_plot_sites"].append(list(candidate))

    after_harvest = get_inventory(client)
    bread_before = _count(after_harvest, BREAD)
    wheat_available = _count(after_harvest, WHEAT)
    breads_requested = max(0, (wheat_available - 3) // 3)
    if breads_requested:
        craft(client, BREAD, breads_requested)
    after_craft = get_inventory(client)
    bread_crafted = max(0, _count(after_craft, BREAD) - bread_before)

    banked = 0
    chest = resolve_storage_location(client, state=state, verify=False)
    if chest is not None and _count(after_craft, BREAD) > max(0, int(personal_food_reserve)):
        deposited = deposit_excess_to_chest(
            client,
            chest,
            deposit_items={BREAD},
            retain_counts={
                BREAD: max(0, int(personal_food_reserve)),
                SEEDS: max(0, int(seed_reserve)),
            },
            state=state,
        )
        if deposited >= 0:
            after_bank = get_inventory(client)
            banked = max(0, _count(after_craft, BREAD) - _count(after_bank, BREAD))

    success = bool(harvested or new_plots or bread_crafted or banked)
    values = {
        "cycles": int(success),
        "plots": new_plots,
        "crop_tiles": crop_tiles,
        "wheat_harvested": harvested,
        "crops_replanted": replanted,
        "bread_crafted": bread_crafted,
        "prepared_food_banked": banked,
    }
    for key, value in values.items():
        worker[key] = int(worker.get(key, 0) or 0) + value
    _flush(state, client)
    detail = (
        f"harvested {harvested} wheat, established {new_plots} plot, "
        f"crafted {bread_crafted} bread, banked {banked} prepared food"
    )
    return _result(worker, success, detail, **values)


__all__ = ["FoodCycleResult", "run_food_cycle"]
