"""Durable lifecycle for automation after the campaign terminal objective.

The city-build verification is a milestone, not permission to discard the
controller's recovery state.  This small coordinator keeps that distinction
explicit and is deliberately independent of gameplay actions: it can only ask
the adaptive scheduler for its already bounded, evidence-producing work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


POSTGAME_STATE_KEY = "persistent_postgame"


@dataclass(frozen=True)
class PostgameTurn:
    """Result of one postgame scheduling attempt."""

    did_work: bool
    reason: str


class PersistentPostgameLifecycle:
    """Persist the terminal transition and safely schedule ongoing local work."""

    def __init__(self, state: Any, scheduler: Any) -> None:
        self.state = state
        self.scheduler = scheduler

    def enter(self) -> bool:
        """Record the durable terminal milestone without invoking callbacks."""
        data = self.state.custom_data.setdefault(POSTGAME_STATE_KEY, {})
        if not isinstance(data, dict):
            data = {}
            self.state.custom_data[POSTGAME_STATE_KEY] = data
        first_entry = not bool(data.get("entered"))
        data["entered"] = True
        data["terminal_objective"] = "CITY_BUILD"
        return first_entry

    def claim_completion_notification(self) -> bool:
        """Claim the one-shot callback before its caller persists the claim."""
        data = self.state.custom_data.setdefault(POSTGAME_STATE_KEY, {})
        if not isinstance(data, dict):
            data = {}
            self.state.custom_data[POSTGAME_STATE_KEY] = data
        if data.get("completion_notified"):
            return False
        data["completion_notified"] = True
        return True

    def run_turn(self, planner: Any) -> PostgameTurn:
        """Ask the adaptive scheduler for one bounded productive action.

        A scheduler decision without local work is intentionally a cooldown
        hold, never fabricated progress.  The caller provides the safety-aware
        wait before retrying.
        """
        decision = self.scheduler.next_step(planner)
        if decision.local_work:
            return PostgameTurn(True, decision.summary)
        return PostgameTurn(False, decision.summary)
