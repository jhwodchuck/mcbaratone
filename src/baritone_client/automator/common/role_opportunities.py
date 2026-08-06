"""Recurring post-assignment work backed by verified game-state deltas."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple

from ...common.end import acquire_elytra, acquire_shulker_boxes, enter_end_portal
from ...common.inventory import count_item
from ...common.mob_farm import grind_xp_at_location
from ...common.nether import enter_nether_portal, hunt_blazes
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


def _saved_location(state: Any, key: str, *, verified: bool = False) -> Optional[Tuple[int, int, int]]:
    custom = getattr(state, "custom_data", {}) or {}
    direct = _location_entry(custom.get(key), verified=verified)
    if direct is not None:
        return direct
    locations = custom.get("locations", {})
    if isinstance(locations, Mapping):
        for candidate in locations.get(key, []) if isinstance(locations.get(key), list) else []:
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


def select_role_opportunity(
    role: FleetRole, signals: Any, state: Any, *, cooldown_ready: bool
) -> Optional[LocalOpportunity]:
    """Select only work whose dimension, safety, and durable prerequisites exist."""
    if not cooldown_ready or not _safe(signals):
        return None
    dimension = str(getattr(signals, "dimension", ""))
    inventory = getattr(signals, "inventory", {})
    if role is FleetRole.NETHER_SUPPLY:
        if "overworld" in dimension:
            portal = _saved_location(state, "nether_portal")
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
        if "overworld" in dimension:
            portal = _saved_location(state, "end_portal")
            if portal is not None and _checkpoint_location(state, "end_city"):
                return LocalOpportunity(
                    OpportunityKind.DIMENSION_ENTRY, 190,
                    "verified End portal and End-city checkpoints can restore the supply route",
                    location=portal, target_item="minecraft:the_end",
                )
        if "end" not in dimension or not _checkpoint_location(state, "end_city"):
            return None
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
        if "overworld" not in dimension or location is None:
            return None
        return LocalOpportunity(
            OpportunityKind.ENCHANTING_XP, 210,
            "verified spawner checkpoint can produce a bounded XP delta",
            location=location,
            target_item="experience_total",
        )
    return None


def run_role_opportunity(client: Any, state: Any, opportunity: LocalOpportunity) -> tuple[bool, str, int, int]:
    """Run one real primitive and accept success only when its target increases."""
    if opportunity.kind is OpportunityKind.DIMENSION_ENTRY and opportunity.location:
        if opportunity.target_item == "minecraft:the_nether":
            entered = enter_nether_portal(client, portal=opportunity.location, timeout=90)
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
        return after > before, f"{item} increased" if after > before else f"no {item} delta observed", before, after
    if opportunity.kind is OpportunityKind.ENCHANTING_XP and opportunity.location:
        result = grind_xp_at_location(
            client, *opportunity.location, target_level=30, timeout=300
        )
        gained = int((result or {}).get("xp_gained", 0) or 0)
        before = int((result or {}).get("start_level", 0) or 0)
        after = int((result or {}).get("achieved_level", before) or before)
        return gained > 0, "experience increased" if gained > 0 else "no experience delta observed", before, after
    return False, "role opportunity has no verified primitive", 0, 0
