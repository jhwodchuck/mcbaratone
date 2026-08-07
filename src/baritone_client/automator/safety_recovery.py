"""Bounded safety actions that must run before ordinary fleet work."""

from __future__ import annotations

from typing import Any, Callable

from ..common.combat import defend_or_flee
from .local_opportunity import LocalOpportunity, OpportunityKind


def select_self_defense(signals: object) -> LocalOpportunity | None:
    """Offer defense before a nearby hostile can become a repeated role hold."""
    if not getattr(signals, "observed", False) or not getattr(signals, "entities_observed", False):
        return None
    if "overworld" not in str(getattr(signals, "dimension", "") or ""):
        return None
    hostiles = int(getattr(signals, "nearby_hostiles", 0) or 0)
    if hostiles <= 0:
        return None
    return LocalOpportunity(
        OpportunityKind.SELF_DEFENSE, 320,
        f"{hostiles} nearby hostile(s) require a supervised defensive recovery",
    )


def run_self_defense(
    client: Any, observe: Callable[[], Any]
) -> tuple[bool, str, int, int]:
    """Run one defensive state-machine tick and measure its local effect."""
    before = int(getattr(observe(), "nearby_hostiles", 0) or 0)
    intervened = defend_or_flee(client, allow_safe_recovery_movement=True)
    after = int(getattr(observe(), "nearby_hostiles", 0) or 0)
    success = bool(intervened and after < before)
    detail = (
        f"nearby hostiles reduced {before}->{after}"
        if after < before
        else f"defensive recovery attempted; nearby hostiles {before}->{after}"
    )
    return success, detail, before, after


__all__ = ["run_self_defense", "select_self_defense"]
