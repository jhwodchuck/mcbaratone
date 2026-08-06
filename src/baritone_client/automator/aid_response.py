"""Bounded, lease-backed answers to fleet ``clear_hostiles`` requests."""

from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from ..common.combat import EntityQueryError, defend_or_flee, safe_combat, scan_for_threats
from ..common.inventory import equip_best_weapon
from ..common.navigation import goto
from ..common.storage_catalog import StorageCatalog, catalog_for
from .end_readiness import FleetRole, bot_name
from .local_opportunity import LocalOpportunity, OpportunityKind
from .phases.leather_supply import expedition_is_too_dangerous


MAX_RESPONSE_DISTANCE = 128.0
RESPONSE_LEASE_SECONDS = 180.0
RESPONSE_TRAVEL_TIMEOUT = 90
RESPONSE_HOSTILE_RADIUS = 16
MAX_COMBAT_ATTEMPTS = 6
_HELD_ROLE_BONUS = 120.0
_ACTIVE_ROLE_COST = 30.0


@dataclass(frozen=True)
class AidResponseResult:
    """Measured outcome of one responder attempt."""

    fulfilled: bool
    detail: str
    before_hostiles: int = 0
    after_hostiles: int = 0


def _catalog(state: Any, catalog: Optional[StorageCatalog]) -> StorageCatalog:
    return catalog if catalog is not None else catalog_for(None, state)


def _owner(state: Any) -> str:
    owner = bot_name(state)
    if not owner:
        raise ValueError("aid response needs a stable BotNN responder identity")
    return owner


def _request_by_id(catalog: StorageCatalog, request_id: str) -> Optional[dict[str, Any]]:
    return next(
        (item for item in catalog.list_aid_requests() if item["request_id"] == request_id),
        None,
    )


def _distance(position: Any, request: Mapping[str, Any]) -> float:
    try:
        x, y, z = (float(value) for value in position)
        return math.dist((x, y, z), (float(request["x"]), float(request["y"]), float(request["z"])))
    except (KeyError, TypeError, ValueError):
        return float("inf")


def _eligible_request(
    request: Mapping[str, Any], owner: str, signals: Any, now: float
) -> bool:
    return bool(
        request.get("kind") == "clear_hostiles"
        and request.get("resolution") is None
        and str(request.get("requester")) != owner
        and float(request.get("expires_at", 0) or 0) > now
        and str(request.get("dimension", "")) == str(getattr(signals, "dimension", ""))
        and _distance(getattr(signals, "position", ()), request) <= MAX_RESPONSE_DISTANCE
    )


def _combat_ready(client: Any) -> bool:
    """Use the established expedition margin and its canonical weapon equip."""
    try:
        return not expedition_is_too_dangerous(client) and bool(equip_best_weapon(client))
    except Exception:
        return False


def select_clear_hostiles_opportunity(
    client: Any,
    state: Any,
    signals: Any,
    role: FleetRole,
    *,
    role_held: bool = False,
    now: Optional[float] = None,
    catalog: Optional[StorageCatalog] = None,
) -> Optional[LocalOpportunity]:
    """Offer the best nearby fight without claiming it or blocking the requester."""
    observed = time.time() if now is None else float(now)
    if not bool(getattr(signals, "safe_for_local_work", False)) or not _combat_ready(client):
        return None
    try:
        owner = _owner(state)
        requests = _catalog(state, catalog).list_aid_requests()
    except (OSError, sqlite3.Error, ValueError):
        return None
    candidates = [item for item in requests if _eligible_request(item, owner, signals, observed)]
    if not candidates:
        return None
    request = max(
        candidates,
        key=lambda item: (
            int(item["urgency"])
            + 80.0 * (1.0 - _distance(signals.position, item) / MAX_RESPONSE_DISTANCE),
            str(item["request_id"]),
        ),
    )
    distance = _distance(signals.position, request)
    role_cost = 0.0 if role_held or role is FleetRole.BALANCED else _ACTIVE_ROLE_COST
    score = int(request["urgency"]) + 80.0 * (1.0 - distance / MAX_RESPONSE_DISTANCE)
    score += _HELD_ROLE_BONUS if role_held else 0.0
    score -= role_cost
    return LocalOpportunity(
        OpportunityKind.CLEAR_HOSTILES_AID, score,
        f"{request['requester']} needs hostiles cleared {distance:.0f} blocks away",
        location=(int(request["x"]), int(request["y"]), int(request["z"])),
        aid_request_id=str(request["request_id"]),
    )


def try_claim_clear_hostiles(
    client: Any,
    state: Any,
    signals: Any,
    opportunity: LocalOpportunity,
    *,
    now: Optional[float] = None,
    catalog: Optional[StorageCatalog] = None,
) -> Optional[dict[str, Any]]:
    """Atomically claim a still-eligible response using the shared lease."""
    observed = time.time() if now is None else float(now)
    if opportunity.kind is not OpportunityKind.CLEAR_HOSTILES_AID:
        return None
    if not bool(getattr(signals, "safe_for_local_work", False)) or not _combat_ready(client):
        return None
    try:
        owner = _owner(state)
        aid_catalog = _catalog(state, catalog)
        request = _request_by_id(aid_catalog, opportunity.aid_request_id)
    except (OSError, sqlite3.Error, ValueError):
        return None
    if request is None or not _eligible_request(request, owner, signals, observed):
        return None
    lease_key = f"aid:{request['request_id']}"
    try:
        if not aid_catalog.acquire_lease(
            lease_key, owner, ttl_seconds=RESPONSE_LEASE_SECONDS, now=observed
        ):
            return None
        request = _request_by_id(aid_catalog, str(request["request_id"]))
        if request is None or not _eligible_request(request, owner, signals, observed):
            aid_catalog.release_lease(lease_key, owner)
            return None
        aid_catalog.note_aid_response(request["request_id"], owner, "aid_request_claimed", now=observed)
        return request
    except (OSError, sqlite3.Error, ValueError):
        try:
            aid_catalog.release_lease(lease_key, owner)
        except (OSError, sqlite3.Error):
            pass
        return None


def _hostiles(client: Any) -> list[dict[str, Any]]:
    return list(scan_for_threats(client, radius=RESPONSE_HOSTILE_RADIUS, raise_on_error=True))


def _record_cleared(state: Any, count: int) -> None:
    custom = getattr(state, "custom_data", None)
    if not isinstance(custom, dict):
        custom = {}
        state.custom_data = custom
    counters = custom.setdefault("mutual_aid", {})
    if not isinstance(counters, dict):
        counters = {}
        custom["mutual_aid"] = counters
    counters["hostiles_cleared"] = int(counters.get("hostiles_cleared", 0) or 0) + count


def run_clear_hostiles_response(
    client: Any,
    state: Any,
    signals: Any,
    opportunity: LocalOpportunity,
    *,
    now: Optional[float] = None,
    catalog: Optional[StorageCatalog] = None,
) -> AidResponseResult:
    """Travel once, fight a bounded set, and fulfil only an observed drop."""
    observed = time.time() if now is None else float(now)
    request = try_claim_clear_hostiles(
        client, state, signals, opportunity, now=observed, catalog=catalog
    )
    if request is None:
        return AidResponseResult(False, "aid request was unavailable or unsafe to claim")
    owner = _owner(state)
    aid_catalog = _catalog(state, catalog)
    lease_key = f"aid:{request['request_id']}"
    before = after = 0
    fulfilled = False
    detail = "responder gave up before a measured hostile reduction"
    try:
        if not goto(
            client, int(request["x"]), int(request["y"]), int(request["z"]),
            timeout=RESPONSE_TRAVEL_TIMEOUT, tolerance=8.0,
            on_defense=lambda: defend_or_flee(client),
        ):
            detail = "could not reach requester within the bounded trip"
            return AidResponseResult(False, detail)
        current = _hostiles(client)
        before = len(current)
        after = before
        if not current:
            detail = "arrived with no measurable hostiles to clear"
            return AidResponseResult(False, detail, before, after)
        for _ in range(MAX_COMBAT_ATTEMPTS):
            defend_or_flee(client)
            current = _hostiles(client)
            after = len(current)
            if after < before:
                break
            if not current:
                break
            target_id = current[0].get("id")
            if target_id is None:
                detail = "hostile observation lacked a combat target"
                break
            safe_combat(
                client, int(target_id), retreat_health=12.0, max_duration=12,
                abort_on_other_hostiles=False,
            )
            current = _hostiles(client)
            after = len(current)
            if after < before:
                break
        if after < before and aid_catalog.resolve_aid_request(
            request["request_id"], owner, before_hostiles=before,
            after_hostiles=after, now=observed,
        ):
            fulfilled = True
            cleared = before - after
            _record_cleared(state, cleared)
            detail = f"measured requester hostiles {before}->{after}"
            return AidResponseResult(True, detail, before, after)
        detail = f"hostiles did not fall (measured {before}->{after})"
        return AidResponseResult(False, detail, before, after)
    except EntityQueryError as exc:
        detail = f"hostile observation unavailable: {exc}"
        return AidResponseResult(False, detail, before, after)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        return AidResponseResult(False, detail, before, after)
    finally:
        try:
            if not fulfilled:
                try:
                    aid_catalog.note_aid_response(
                        request["request_id"], owner, "aid_request_abandoned", now=observed,
                        evidence={"after_hostiles": after, "before_hostiles": before, "detail": detail},
                    )
                except (OSError, sqlite3.Error):
                    pass
        finally:
            try:
                aid_catalog.release_lease(lease_key, owner)
            except (OSError, sqlite3.Error):
                pass


def run_scheduled_clear_hostiles(
    client: Any, state: Any, signals: Any, opportunity: LocalOpportunity
) -> tuple[bool, str, int, int]:
    """Adapt one measured response to the scheduler's generic result shape."""
    response = run_clear_hostiles_response(client, state, signals, opportunity)
    return (
        response.fulfilled,
        response.detail,
        0,
        max(0, response.before_hostiles - response.after_hostiles),
    )


__all__ = [
    "AidResponseResult",
    "MAX_RESPONSE_DISTANCE",
    "run_clear_hostiles_response",
    "run_scheduled_clear_hostiles",
    "select_clear_hostiles_opportunity",
    "try_claim_clear_hostiles",
]
