"""Bounded, repeatable wheat-to-bread supply work for survival roles."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, hypot, sqrt
import math
from typing import Any, Mapping, Optional, Sequence, Tuple

from .combat import eat_until_hunger
from .farming import (
    _block_id,
    establish_wheat_farm,
    find_farm_surface_near,
    find_natural_crop_center,
    harvest_wheat_farm,
)
from .inventory import (
    craft,
    deposit_excess_to_chest,
    get_inventory,
    resolve_storage_location,
)


WHEAT = "minecraft:wheat"
SEEDS = "minecraft:wheat_seeds"
BREAD = "minecraft:bread"
#: Farms must stay within one short walk of the durable base anchor. A plot
#: further out is not an asset: the worker stops visiting it, the harvest step
#: times out travelling, and the fleet accumulates farms it never uses. At 64
#: the siting search still offers eight distinct sites -- enough that rejected
#: ground never stalls expansion -- and the furthest is a ~15 second walk.
MAX_ANCHOR_RADIUS = 64
#: Beyond this the worker plainly never arrived at the plot; harvest travel
#: uses a 4-block tolerance, so anything much larger is a failed journey.
UNREACHED_PLOT_DISTANCE = 24.0
#: A worker that has drifted hundreds of blocks needs more than the harvest
#: step's 120s travel budget to get home. Kept well short of a full journey on
#: purpose: this blocks one scheduling tick, and partial progress still counts
#: because the next cycle starts closer than the last.
RETURN_HOME_TIMEOUT = 150
#: Seed top-up runs every cycle, so it gets a short leash; partial stock is
#: still progress and the next cycle tries again.
SEED_STOCK_TIMEOUT = 45


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


def _base_anchor(state: Any) -> Optional[Tuple[int, int, int]]:
    """Return only a durable base/storage anchor, never a farm fallback."""
    custom = getattr(state, "custom_data", {}) or {}
    if isinstance(custom, Mapping):
        for key in (
            "base_location",
            "homestead_anchor",
            "base",
            "storage",
            "home",
        ):
            candidate = _position(custom.get(key))
            if candidate:
                return candidate
        structures = custom.get("structures", {})
        if isinstance(structures, Mapping):
            for key in ("storage", "home_storage"):
                candidate = _position(structures.get(key))
                if candidate:
                    return candidate
    return None


def _anchor(state: Any, plots: Sequence[Tuple[int, int, int]]) -> Optional[Tuple[int, int, int]]:
    base = _base_anchor(state)
    if base is not None:
        return base
    custom = getattr(state, "custom_data", {}) or {}
    if isinstance(custom, Mapping):
        for key in ("farm_location", "wheat_farm"):
            candidate = _position(custom.get(key))
            if candidate:
                return candidate
        structures = custom.get("structures", {})
        if isinstance(structures, Mapping):
            candidate = _position(structures.get("food_source"))
            if candidate:
                return candidate
    return plots[0] if plots else None


def _within_reach(
    candidate: Tuple[int, int, int],
    anchor: Tuple[int, int, int],
    radius: int,
) -> bool:
    """A farm is only useful if the worker can actually walk to it.

    Siting used to march outward with every rejection -- Bot18's six attempts
    landed 20, 26, 48, 51, 64 and 72 blocks from base, and the unbounded grid
    fallback keeps going. A farm the worker cannot reach in one trip produces
    nothing, and the fleet ends up owning plots it never visits again.
    """
    return hypot(candidate[0] - anchor[0], candidate[2] - anchor[2]) <= radius


def _candidate(
    anchor: Tuple[int, int, int],
    plots: Sequence[Tuple[int, int, int]],
    rejected: Sequence[Tuple[int, int, int]],
    distance: int,
    cursor: int,
    maximum_slots: int,
    max_anchor_radius: int = MAX_ANCHOR_RADIUS,
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
        if not _within_reach(candidate, anchor, max_anchor_radius):
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
    # The grid ring grows without limit by design, so it must be clipped to a
    # walkable radius or the worker eventually sites farms it can never visit.
    if (
        candidate not in rejected
        and _within_reach(candidate, anchor, max_anchor_radius)
        and all(
            hypot(candidate[0] - plot[0], candidate[2] - plot[2]) >= distance
            for plot in plots
        )
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


def _retire_distant_plots(
    state: Any,
    worker: dict,
    anchor: Optional[Tuple[int, int, int]],
) -> list:
    """Drop plots the siting radius would never choose today.

    The unbounded search left plots scattered far from base. They are not
    assets: the worker cannot reach them, and keeping them in the rotation
    means every cycle burns its travel budget on a trip that cannot finish.
    Retiring one frees the next cycle to site a fresh plot near base.
    """
    if anchor is None:
        return []
    kept, retired = [], []
    for record in worker.get("farm_plots", []):
        plot = _position(record)
        if plot is None:
            continue
        if _within_reach(plot, anchor, MAX_ANCHOR_RADIUS):
            kept.append({"origin": list(plot)})
        else:
            retired.append(list(plot))
    if retired:
        worker["farm_plots"] = kept
        history = worker.setdefault("retired_plots", [])
        history.extend(retired)
        # `_plots` imports this compatibility field on every cycle. Leaving a
        # retired origin here resurrects it immediately, so the worker spends
        # every turn retiring the same unreachable farm and never sites the
        # replacement near its durable base.
        custom = getattr(state, "custom_data", None)
        if isinstance(custom, dict):
            legacy = _position(custom.get("wheat_farm"))
            if legacy in {tuple(plot) for plot in retired}:
                custom.pop("wheat_farm", None)
    return retired


def _return_to_anchor(client: Any, anchor: Optional[Tuple[int, int, int]]) -> bool:
    """Walk back toward base so the next cycle starts within reach.

    A 468-block journey cannot finish inside the harvest step's 120s travel
    budget, which is why the worker never got home on its own. This trip is
    given room to complete, and partial progress still helps: the next cycle
    starts closer than the last.
    """
    if anchor is None:
        return False
    try:
        from .navigation import goto as _goto

        return bool(_goto(client, anchor[0], anchor[1], anchor[2],
                          timeout=RETURN_HOME_TIMEOUT, tolerance=8.0))
    except Exception:
        return False


def _stock_seeds(client: Any, target: int) -> int:
    """Break grass until a seed reserve exists. Returns seeds gained.

    Bounded and best effort: failing to find grass is not a reason to abort a
    cycle, and partial stock still makes the next planting attempt cheaper.
    """
    wanted = max(0, int(target))
    if wanted <= 0:
        return 0
    try:
        from .farming import _gather_seeds

        before = _count(get_inventory(client), SEEDS)
        if before >= wanted:
            return 0
        # Bounded well under the primitive's 180s default: this now runs every
        # cycle, and a worker standing where no grass grows must not spend
        # three minutes discovering that each time.
        _gather_seeds(client, wanted, timeout=SEED_STOCK_TIMEOUT)
        return max(0, _count(get_inventory(client), SEEDS) - before)
    except Exception:
        return 0


def _next_plot_candidate(
    anchor: Tuple[int, int, int],
    known: Sequence[Tuple[int, int, int]],
    worker: dict,
    distance: int,
    maximum_slots: int,
) -> Optional[Tuple[int, int, int]]:
    """Choose a site, reopening one exhausted frontier per durable anchor."""
    rejected = [_position(site) for site in worker["failed_plot_sites"]]
    rejected = [site for site in rejected if site]
    cursor = int(worker.get("expansion_cursor", 0) or 0)
    candidate = _candidate(
        anchor, known, rejected, distance, cursor, maximum_slots
    )
    worker["expansion_cursor"] = cursor + 1
    anchor_key = list(anchor)
    if (
        candidate is None
        and not known
        and rejected
        and worker.get("failed_site_reset_anchor") != anchor_key
    ):
        # Setup failures (notably a missing bucket) used to blacklist every
        # nearby coordinate. With no farm left, that made the bounded search
        # permanently return None even after the prerequisite was repaired.
        worker["failed_plot_sites"] = []
        worker["failed_site_reset_anchor"] = anchor_key
        worker["expansion_cursor"] = 1
        candidate = _candidate(anchor, known, [], distance, 0, maximum_slots)
    return candidate


def _establish_candidate(client: Any, state: Any, candidate, size: int):
    """Resolve a planned X/Z coordinate to terrain and establish one plot."""
    if candidate is None:
        return None
    surface = None
    if getattr(state, "checkpoint_dir", None) and not _plots(state):
        natural, used_water = find_natural_crop_center(
            client, lambda x, y, z: _block_id(client, x, y, z)
        )
        if natural is not None and not used_water:
            surface = natural
    if surface is None:
        surface = find_farm_surface_near(client, *candidate)
    if surface is None:
        return None
    return establish_wheat_farm(client, *surface, size=size, state=state)


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


def _plot_distance(client: Any, plot: Sequence[int]) -> float:
    """Straight-line distance to a plot, for reporting why a trip failed."""
    try:
        snapshot = client.transport.dispatch("get_state", {})
        position = snapshot.get("block_position", snapshot.get("position", {})) or {}
        return math.dist(
            (float(position["x"]), float(position["y"]), float(position["z"])),
            (float(plot[0]), float(plot[1]), float(plot[2])),
        )
    except Exception:
        return float("nan")


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
    anchor = _base_anchor(state)
    retired = _retire_distant_plots(state, worker, anchor)
    if retired:
        went_home = _return_to_anchor(client, anchor)
        _flush(state, client)
        return _result(
            worker,
            False,
            f"retired {len(retired)} farm plot(s) beyond the "
            f"{MAX_ANCHOR_RADIUS}-block siting radius; "
            + (
                "returned to base for resiting"
                if went_home
                else "started bounded return to base for resiting"
            ),
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
    unreachable = []
    for plot in inspected:
        plot_before = _count(get_inventory(client), WHEAT)
        harvest_wheat_farm(client, *plot, range_=max(1, int(farm_range)))
        harvested += max(0, _count(get_inventory(client), WHEAT) - plot_before)
        # harvest_wheat_farm returns False both when it never arrived and when
        # it arrived to find nothing ripe, so its flag cannot distinguish them.
        # Measure instead: still far from the plot means the trip failed, and
        # that is a different problem from an empty farm. Bot18 sat 461 blocks
        # from its own plot reporting "harvested 0 wheat" six times, then tried
        # to fix it by building another farm it also could not reach.
        separation = _plot_distance(client, plot)
        if separation == separation and separation > UNREACHED_PLOT_DISTANCE:
            unreachable.append((plot, separation))
    if known:
        worker["plot_cursor"] = (cursor + len(inspected)) % len(known)
    if unreachable and len(unreachable) == len(inspected) and harvested == 0:
        # Every plot this pass was out of reach. Establishing another near the
        # same distant anchor would repeat the trip that just failed.
        worker["unreachable_plots"] = [list(plot) for plot, _ in unreachable]
        nearest = min(distance for _, distance in unreachable)
        anchor = _anchor(state, known)
        # Two different failures wear the same symptom. A plot beyond the
        # siting radius is a bad plot, left over from the unbounded search, and
        # is retired so a fresh one can be sited near base. A good plot the
        # worker simply walked away from needs the worker brought home instead
        # -- Bot18's plot sat 21 blocks from its anchor while the bot was 468.
        retired = _retire_distant_plots(state, worker, anchor)
        if retired:
            _flush(state, client)
            return _result(
                worker,
                False,
                f"retired {len(retired)} farm plot(s) beyond the "
                f"{MAX_ANCHOR_RADIUS}-block siting radius; will resite near base",
            )
        went_home = _return_to_anchor(client, anchor)
        _flush(state, client)
        return _result(
            worker,
            False,
            f"could not reach {len(unreachable)} known farm plot(s); nearest is "
            f"{nearest:.0f} blocks away; "
            + ("walked back toward base" if went_home else "return to base failed"),
        )

    new_plots = 0
    crop_tiles = 0
    replanted = 0
    seeds_gathered = 0
    if harvested == 0:
        anchor = _anchor(state, known)
        # Seeds are the bootstrap for the whole food economy and every bot in
        # the fleet carried zero. Grass breaking already existed, but only
        # inside establish_wheat_farm -- after travel and after irrigation --
        # so any site that failed earlier meant no seeds were ever collected
        # and the next attempt started empty again. The 29 seeds in storage
        # are no help: they sit as ones and twos across thirteen chests.
        # Stock up first; seeds keep, and a reserve makes every later attempt
        # cheaper.
        seeds_gathered = _stock_seeds(client, seed_reserve)
        if anchor is not None:
            candidate = _next_plot_candidate(
                anchor,
                known,
                worker,
                max(8, int(max_plot_distance)),
                max_candidate_slots,
            )
            established = _establish_candidate(
                client, state, candidate, max(3, int(plot_size) | 1)
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

    # Gathering seeds is real progress: it is the bootstrap the whole food
    # economy waits on, and a cycle that stocks them has not done nothing.
    success = bool(harvested or new_plots or bread_crafted or banked or seeds_gathered)
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
        + (f", gathered {seeds_gathered} seeds" if seeds_gathered else "")
    )
    return _result(worker, success, detail, **values)


__all__ = ["FoodCycleResult", "run_food_cycle"]
