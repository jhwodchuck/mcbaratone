"""Recurring post-assignment work backed by verified game-state deltas."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple

from ...common.end import (
    acquire_elytra,
    acquire_shulker_boxes,
    enter_end_portal,
    find_end_city,
    traverse_end_gateway,
)
from ...common.inventory import count_item
from ...common.mob_farm import grind_xp_at_location
from ...common.nether import enter_nether_portal, hunt_blazes
from ...common.navigation import goto
from ...common.resources import ensure_supplies
from ..end_readiness import FleetRole
from ..local_opportunity import LocalOpportunity, OpportunityKind


def _coordinate(value: Any) -> Optional[Tuple[int, int, int]]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        return tuple(int(part) for part in value)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


def _checkpoint_location(state: Any, key: str) -> Optional[Tuple[int, int, int]]:
    custom = getattr(state, "custom_data", {}) or {}
    value = custom.get(key)
    if isinstance(value, Mapping):
        value = value.get("location") or value.get("position")
    return _coordinate(value)


def _location_entry(value: Any, *, verified: bool = False) -> Optional[Tuple[int, int, int]]:
    if isinstance(value, Mapping):
        if verified and not bool(value.get("verified") or "verified" in value.get("tags", [])):
            return None
        nested = value.get("location") or value.get("position")
        if nested is not None:
            return _coordinate(nested)
        try:
            return int(value["x"]), int(value["y"]), int(value["z"])
        except (KeyError, TypeError, ValueError):
            return None
    return _coordinate(value)


def _saved_location(
    state: Any,
    key: str,
    *,
    verified: bool = False,
    dimension: str = "",
) -> Optional[Tuple[int, int, int]]:
    custom = getattr(state, "custom_data", {}) or {}
    direct = _location_entry(custom.get(key), verified=verified)
    if direct is not None:
        return direct
    locations = custom.get("locations", {})
    if isinstance(locations, Mapping):
        for candidate in locations.get(key, []) if isinstance(locations.get(key), list) else []:
            if dimension and isinstance(candidate, Mapping):
                saved_dimension = str(candidate.get("dimension", ""))
                if saved_dimension and saved_dimension.removeprefix(
                    "minecraft:"
                ) != dimension.removeprefix("minecraft:"):
                    continue
            location = _location_entry(candidate, verified=verified)
            if location is not None:
                return location
    return None


def _xp_engine_location(state: Any) -> Optional[Tuple[int, int, int]]:
    """Resolve the exact verified shapes persisted by ``XpEngineHandler``."""
    custom = getattr(state, "custom_data", {}) or {}
    structures = custom.get("structures", {})
    if isinstance(structures, Mapping):
        location = _location_entry(structures.get("xp_engine"), verified=True)
        if location is not None:
            return location
    phase_payloads = getattr(state, "phase_payloads", {}) or {}
    payload = phase_payloads.get("XP_ENGINE", {}) if isinstance(phase_payloads, Mapping) else {}
    if isinstance(payload, Mapping):
        location = _location_entry(payload.get("farm"), verified=True)
        if location is not None:
            return location
    return _saved_location(state, "xp_engine", verified=True)


def _safe(signals: Any) -> bool:
    return bool(
        getattr(signals, "observed", False)
        and getattr(signals, "entities_observed", False)
        and float(getattr(signals, "health", 0.0)) >= 16.0
        and int(getattr(signals, "food", 0)) >= 14
        and int(getattr(signals, "nearby_hostiles", 0)) == 0
    )


def _last_end_supply_failed(state: Any) -> bool:
    runtime = (getattr(state, "custom_data", {}) or {}).get("adaptive_scheduler", {})
    opportunities = runtime.get("opportunities", {}) if isinstance(runtime, Mapping) else {}
    last = opportunities.get(OpportunityKind.END_SUPPLY.value, {}) if isinstance(opportunities, Mapping) else {}
    return isinstance(last, Mapping) and last.get("success") is False


def _end_worker(state: Any) -> dict[str, Any]:
    custom = getattr(state, "custom_data", None)
    if not isinstance(custom, dict):
        custom = {}
        state.custom_data = custom
    worker = custom.setdefault("end_worker", {})
    if not isinstance(worker, dict):
        worker = {}
        custom["end_worker"] = worker
    return worker


def _end_frontier_required(state: Any) -> bool:
    worker = _end_worker(state)
    if "frontier_required" in worker:
        return bool(worker["frontier_required"])
    return _last_end_supply_failed(state)


def _same_city(first: Tuple[int, int, int], second: Tuple[int, int, int]) -> bool:
    return sum((first[index] - second[index]) ** 2 for index in range(3)) <= 64 ** 2


def _near(location: Tuple[int, int, int], target: Tuple[int, int, int]) -> bool:
    return sum((location[index] - target[index]) ** 2 for index in range(3)) <= 96 ** 2


def _central_end_island(location: Tuple[int, int, int]) -> bool:
    return location[0] ** 2 + location[2] ** 2 <= 512 ** 2


def _persist_end_city(state: Any, location: Tuple[int, int, int]) -> None:
    custom = getattr(state, "custom_data", None)
    if not isinstance(custom, dict):
        custom = {}
        state.custom_data = custom
    custom["end_city"] = {"location": list(location), "verified": True}
    locations = custom.setdefault("locations", {})
    if isinstance(locations, dict):
        cities = locations.setdefault("end_city", [])
        if isinstance(cities, list):
            cities.append({"x": location[0], "y": location[1], "z": location[2], "tags": ["verified"]})


def select_role_opportunity(
    role: FleetRole, signals: Any, state: Any, *, cooldown_ready: bool
) -> Optional[LocalOpportunity]:
    """Select only work whose dimension, safety, and durable prerequisites exist."""
    if not cooldown_ready or not _safe(signals):
        return None
    dimension = str(getattr(signals, "dimension", ""))
    difficulty = str(getattr(signals, "difficulty", "")).lower()
    inventory = getattr(signals, "inventory", {})
    if role is FleetRole.NETHER_SUPPLY:
        if difficulty == "peaceful":
            if "nether" in dimension:
                portal = _saved_location(
                    state, "nether_portal", dimension=dimension
                )
                if portal is not None:
                    return LocalOpportunity(
                        OpportunityKind.DIMENSION_ENTRY,
                        260,
                        "Peaceful disables blaze production; return to the Overworld for renewable work",
                        location=portal,
                        target_item="minecraft:overworld",
                    )
                return None
            if "overworld" in dimension:
                return LocalOpportunity(
                    OpportunityKind.WOOD_FARM,
                    205,
                    "Peaceful disables blaze production; bank renewable wood until hostile work resumes",
                )
        if "overworld" in dimension:
            portal = _saved_location(
                state, "nether_portal", dimension=dimension
            )
            if portal is not None:
                return LocalOpportunity(
                    OpportunityKind.DIMENSION_ENTRY, 190,
                    "verified Nether portal checkpoint can restore the supply route",
                    location=portal, target_item="minecraft:the_nether",
                )
        if "nether" not in dimension:
            return None
        rods = int(inventory.get("minecraft:blaze_rod", 0) or 0)
        return LocalOpportunity(
            OpportunityKind.NETHER_SUPPLY, 230,
            "safe Nether supply cycle can replenish blaze rods from a real fortress hunt",
            target_item="minecraft:blaze_rod",
        )
    if role is FleetRole.END_RUNNER:
        if difficulty == "peaceful" and "overworld" in dimension:
            return LocalOpportunity(
                OpportunityKind.WOOD_FARM,
                205,
                "Peaceful disables Enderman hunting; bank renewable wood until hostile work resumes",
            )
        if "overworld" in dimension:
            portal = _saved_location(state, "end_portal")
            if portal is not None and _checkpoint_location(state, "end_city"):
                return LocalOpportunity(
                    OpportunityKind.DIMENSION_ENTRY, 190,
                    "verified End portal and End-city checkpoints can restore the supply route",
                    location=portal, target_item="minecraft:the_end",
                )
        city = _checkpoint_location(state, "end_city")
        if "end" not in dimension or not city:
            return None
        position = tuple(getattr(signals, "position", (0, 64, 0)))
        if not _near(position, city):
            route = "gateway_then_city" if _central_end_island(position) else "city"
            return LocalOpportunity(
                OpportunityKind.END_CITY_ROUTE, 250,
                "saved End city requires one bounded, verified route leg",
                location=city,
                target_item=route,
            )
        if _end_frontier_required(state):
            return LocalOpportunity(
                OpportunityKind.END_FRONTIER, 245,
                "previous End-city supply produced no item delta; search a bounded new frontier",
                location=city,
            )
        if int(inventory.get("minecraft:elytra", 0) or 0) < 1:
            return LocalOpportunity(
                OpportunityKind.END_SUPPLY, 240,
                "verified End-city checkpoint can supply an Elytra",
                target_item="minecraft:elytra",
            )
        return LocalOpportunity(
            OpportunityKind.END_SUPPLY, 220,
            "verified End-city checkpoint can supply another shulker box",
            target_item="minecraft:shulker_box",
        )
    if role is FleetRole.ENCHANTING:
        location = _xp_engine_location(state)
        if "overworld" not in dimension:
            return None
        if location is not None:
            return LocalOpportunity(OpportunityKind.ENCHANTING_XP, 210, "verified spawner checkpoint can produce a bounded XP delta", location=location, target_item="experience_total")
        page_material = int(inventory.get("minecraft:paper", 0) or 0) + int(
            inventory.get("minecraft:sugar_cane", 0) or 0
        )
        if difficulty == "peaceful" and (
            int(inventory.get("minecraft:leather", 0) or 0) < 1
            or page_material < 3
        ):
            return LocalOpportunity(
                OpportunityKind.WOOD_FARM,
                175,
                "Peaceful enchanting inputs are unavailable; bank renewable wood until materials recover",
            )
        return LocalOpportunity(
            OpportunityKind.ENCHANTING_MATERIAL,
            180,
            "one bounded supply cycle can acquire or craft enchanting books",
            target_item="minecraft:book",
        )
    return None


def run_role_opportunity(client: Any, state: Any, opportunity: LocalOpportunity) -> tuple[bool, str, int, int]:
    """Run one real primitive and accept success only when its target increases."""
    if opportunity.kind is OpportunityKind.DIMENSION_ENTRY and opportunity.location:
        if opportunity.target_item == "minecraft:the_nether":
            entered = enter_nether_portal(client, portal=opportunity.location, timeout=90)
        elif opportunity.target_item == "minecraft:overworld":
            entered = enter_nether_portal(
                client,
                portal=opportunity.location,
                target_dimension="minecraft:overworld",
                timeout=90,
            )
        elif opportunity.target_item == "minecraft:the_end":
            entered = enter_end_portal(client, portal=opportunity.location, timeout=90)
        else:
            entered = False
        detail = "dimension entry verified; awaiting productive cycle" if entered else "verified portal entry did not reach target dimension"
        # Traversal re-establishes a prerequisite, never resource progress.
        return False, detail, 0, 0
    if opportunity.kind is OpportunityKind.NETHER_SUPPLY:
        before = count_item(client, "minecraft:blaze_rod")
        hunt_blazes(client, target_count=before + 2, timeout=300, state=state)
        after = count_item(client, "minecraft:blaze_rod")
        return after > before, "blaze rods increased" if after > before else "no blaze-rod delta observed", before, after
    if opportunity.kind is OpportunityKind.END_SUPPLY:
        item = opportunity.target_item
        before = count_item(client, item)
        if item == "minecraft:elytra":
            acquire_elytra(client, timeout=300)
        else:
            acquire_shulker_boxes(client, target=before + 1, timeout=600)
        after = count_item(client, item)
        _end_worker(state)["frontier_required"] = after <= before
        return after > before, f"{item} increased" if after > before else f"no {item} delta observed", before, after
    if opportunity.kind is OpportunityKind.END_CITY_ROUTE and opportunity.location:
        route_ready = True
        if opportunity.target_item == "gateway_then_city":
            route_ready = traverse_end_gateway(client, timeout=90) is not None
        if route_ready and goto(
            client,
            *opportunity.location,
            timeout=180,
            tolerance=16.0,
        ):
            return False, "outer-island route verified; awaiting productive cycle", 0, 0
        return False, "verified gateway/city route could not be established", 0, 0
    if opportunity.kind is OpportunityKind.END_FRONTIER and opportunity.location:
        worker = _end_worker(state)
        cursor = int(worker.get("frontier_cursor", 0) or 0)
        worker["frontier_cursor"] = cursor + 1
        radius = 192
        dx, dz = (
            (radius, 0),
            (0, radius),
            (-radius, 0),
            (0, -radius),
        )[cursor % 4]
        waypoint = (
            opportunity.location[0] + dx,
            opportunity.location[1],
            opportunity.location[2] + dz,
        )
        if not goto(client, *waypoint, timeout=120, tolerance=16.0):
            return False, "bounded End frontier waypoint was unreachable", 0, 0
        discovered = find_end_city(client, timeout=300, max_distance=1024.0)
        if discovered is not None and not _same_city(opportunity.location, discovered):
            _persist_end_city(state, discovered)
            worker["frontier_required"] = False
            return False, "new End city verified; awaiting productive cycle", 0, 0
        return False, "no newly verified End city beyond the exhausted frontier", 0, 0
    if opportunity.kind is OpportunityKind.ENCHANTING_MATERIAL:
        before = count_item(client, "minecraft:book")
        ensure_supplies(
            client,
            {"minecraft:book": before + 1},
            timeout=300,
        )
        after = count_item(client, "minecraft:book")
        return after > before, "book batch increased" if after > before else "no book delta observed", before, after
    if opportunity.kind is OpportunityKind.ENCHANTING_XP and opportunity.location:
        result = grind_xp_at_location(
            client, *opportunity.location, target_level=30, timeout=300
        )
        gained = int((result or {}).get("xp_gained", 0) or 0)
        before = int((result or {}).get("start_level", 0) or 0)
        after = int((result or {}).get("achieved_level", before) or before)
        return gained > 0, "experience increased" if gained > 0 else "no experience delta observed", before, after
    return False, "role opportunity has no verified primitive", 0, 0
