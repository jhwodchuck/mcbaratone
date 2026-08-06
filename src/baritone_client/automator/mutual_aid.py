"""Publish short-lived fleet-visible facts about a bot's unmet needs.

Milestone 1 deliberately has no responder, claim, transfer, cancellation, or
waiting path.  A request is an observation written to the shared catalog; the
requesting bot continues its own recovery exactly as it did before.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from typing import Any, Mapping, Optional, Tuple

from ..common.storage_catalog import StorageCatalog, catalog_for
from . import aid_chat, camp_breaker
from .end_readiness import bot_name
from .local_opportunity import local_work_blockers


def _catalog(state: Any, catalog: Optional[StorageCatalog]) -> StorageCatalog:
    if catalog is not None:
        return catalog
    if state is None:
        raise ValueError("aid requests need a state or catalog context")
    return catalog_for(None, state)


def _requester(state: Any) -> str:
    requester = bot_name(state)
    if not requester:
        raise ValueError("aid requests require a stable BotNN requester identity")
    return requester


def _position(state: Any, position: Optional[Tuple[int, int, int]]) -> Tuple[int, int, int]:
    raw = position if position is not None else getattr(state, "_last_position", (0, 64, 0))
    try:
        x, y, z = raw
        return int(x), int(y), int(z)
    except (TypeError, ValueError):
        return (0, 64, 0)


def raise_request(
    state: Any,
    kind: str,
    detail: str,
    urgency: int,
    ttl: float,
    *,
    now: Optional[float] = None,
    catalog: Optional[StorageCatalog] = None,
    dimension: str = "minecraft:overworld",
    position: Optional[Tuple[int, int, int]] = None,
) -> dict[str, Any]:
    """Create or refresh this bot's open aid fact for one need kind.

    The catalog's partial unique index, rather than a timer, enforces one open
    request per ``(requester, kind)``.  Refreshing a request retains its
    ``created_at`` so the reported age remains the age of the underlying need.
    """
    normalized_kind = str(kind).strip()
    if not normalized_kind:
        raise ValueError("aid request kind is required")
    if float(ttl) <= 0:
        raise ValueError("aid request ttl must be positive")
    observed = time.time() if now is None else float(now)
    return _catalog(state, catalog).upsert_aid_request(
        uuid.uuid4().hex,
        _requester(state),
        normalized_kind,
        str(detail),
        int(urgency),
        float(ttl),
        dimension=str(dimension or "minecraft:overworld"),
        position=_position(state, position),
        now=observed,
    )


def expire_stale(
    now: Optional[float] = None,
    state: Any = None,
    *,
    catalog: Optional[StorageCatalog] = None,
) -> int:
    """Close elapsed requests once; this operation is safe on every tick."""
    return _catalog(state, catalog).expire_aid_requests(now)


def open_requests(
    state: Any = None,
    *,
    catalog: Optional[StorageCatalog] = None,
) -> list[dict[str, Any]]:
    """Read the open facts for reporting without directing any other bot."""
    return _catalog(state, catalog).list_aid_requests()


def _blocker(blockers: tuple[str, ...], prefix: str) -> str:
    return next((item for item in blockers if item.startswith(prefix)), "")


def _escalation_detail(state: Any, level: int) -> str:
    custom = getattr(state, "custom_data", {}) or {}
    ledger = custom.get("productive_work", {}) if isinstance(custom, Mapping) else {}
    if isinstance(ledger, Mapping) and ledger.get("last_detail"):
        return str(ledger["last_detail"])
    return f"work-progress escalation level {level}"


def publish_requests(
    state: Any,
    signals: Any,
    *,
    now: Optional[float] = None,
    catalog: Optional[StorageCatalog] = None,
    client: Any = None,
) -> list[dict[str, Any]]:
    """Publish M1 trigger facts and expire prior ones without blocking work.

    Only current signals are used: local-work blocker text, camp hold streak,
    and the durable work-progress escalation value.  A catalog outage is not
    allowed to become a new scheduling gate, so publication failure is ignored
    by this best-effort observability path.
    """
    try:
        _requester(state)
        aid_catalog = _catalog(state, catalog)
        observed = time.time() if now is None else float(now)
        expire_stale(observed, state, catalog=aid_catalog)
    except (OSError, sqlite3.Error, ValueError):
        return []
    if not getattr(signals, "observed", False):
        return []

    blockers = local_work_blockers(signals)
    dimension = str(getattr(signals, "dimension", "") or "minecraft:overworld")
    position = _position(state, getattr(signals, "position", None))
    requests: list[dict[str, Any]] = []
    triggers = []
    food = int(getattr(signals, "food", 0) or 0)
    health = float(getattr(signals, "health", 0.0) or 0.0)
    hostile_detail = next((item for item in blockers if "hostile(s) near" in item), "")
    if food < 6:
        triggers.append(("food", _blocker(blockers, "food "), 90))
    if health < 8:
        triggers.append(("rescue", _blocker(blockers, "health "), 95))
    if hostile_detail and camp_breaker.hold_streak(state) >= 6:
        triggers.append(("clear_hostiles", hostile_detail, 60))
    custom = getattr(state, "custom_data", {}) or {}
    progress = custom.get("productive_work", {}) if isinstance(custom, Mapping) else {}
    try:
        escalation = int(progress.get("escalation_level", 0) or 0)
    except (AttributeError, TypeError, ValueError):
        escalation = 0
    if escalation >= 3:
        triggers.append(("supply", _escalation_detail(state, escalation), 50))
    try:
        for kind, detail, urgency in triggers:
            requests.append(
                raise_request(
                    state,
                    kind,
                    detail,
                    urgency,
                    180.0,
                    now=observed,
                    catalog=aid_catalog,
                    dimension=dimension,
                    position=position,
                )
            )
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return requests
    if client is not None:
        # Only a genuinely NEW need reaches chat; aid_chat enforces the quiet
        # period and the no-repeat rule. Chat is decoration and must never
        # affect scheduling, so nothing here can raise.
        for request in requests:
            aid_chat.announce(client, state, request, signals, now=observed)
    return requests


__all__ = ["expire_stale", "open_requests", "publish_requests", "raise_request"]
