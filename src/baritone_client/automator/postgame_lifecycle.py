"""Durable lifecycle for automation after the campaign terminal objective.

The city-build verification is a milestone, not permission to discard the
controller's recovery state.  This small coordinator keeps that distinction
explicit and is deliberately independent of gameplay actions: it can only ask
the adaptive scheduler for its already bounded, evidence-producing work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..common.city import build_district, district_ring_offsets
from .state_manager import Phase
from .work_progress import productive_snapshot, record_productive_attempt


POSTGAME_STATE_KEY = "persistent_postgame"


@dataclass(frozen=True)
class PostgameTurn:
    """Result of one postgame scheduling attempt."""

    did_work: bool
    reason: str
    state_changed: bool = False


class PersistentPostgameLifecycle:
    """Persist the terminal transition and safely schedule ongoing local work."""

    def __init__(self, state: Any, scheduler: Any, client: Any = None) -> None:
        self.state = state
        self.scheduler = scheduler
        self.client = client

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
            return PostgameTurn(True, decision.summary, state_changed=True)
        return self._build_next_city_district(decision.summary)

    def _build_next_city_district(self, scheduler_reason: str) -> PostgameTurn:
        """Build exactly one verified district when ordinary local work is idle."""
        if self.client is None:
            return PostgameTurn(False, scheduler_reason)
        try:
            terraform = self.state.get_phase_payload(Phase.TERRAFORM)
        except Exception:
            terraform = {}
        if not isinstance(terraform, Mapping):
            return PostgameTurn(False, f"{scheduler_reason}; terraform handoff unavailable")
        center = terraform.get("center")
        try:
            center_x, center_z = (int(value) for value in center)
            target_y = int(terraform["target_y"])
        except (KeyError, TypeError, ValueError):
            return PostgameTurn(False, f"{scheduler_reason}; verified terraform center unavailable")

        city = self.state.custom_data.setdefault("city_progress", {})
        if not isinstance(city, dict):
            city = {}
            self.state.custom_data["city_progress"] = city
        ring = max(1, int(city.get("ring", 1) or 1))
        progress = city.setdefault("ring_progress", {})
        if not isinstance(progress, dict):
            progress = {}
            city["ring_progress"] = progress
        offsets = list(district_ring_offsets(ring))
        index = max(0, int(progress.get("next_index", 0) or 0))
        if index >= len(offsets):
            city["rings_completed"] = max(
                int(city.get("rings_completed", 0) or 0),
                ring + 1,
            )
            ring += 1
            city["ring"] = ring
            progress = {}
            city["ring_progress"] = progress
            offsets = list(district_ring_offsets(ring))
            index = 0

        dcx, dcz = offsets[index]
        before = productive_snapshot(self.state)
        result = build_district(
            self.client, center_x, center_z, dcx, dcz, target_y, flatten=True
        )
        if not result.success:
            attempt = record_productive_attempt(
                self.state, "postgame_city_district", before, before,
                detail=f"ring {ring} district {dcx},{dcz}: {result.reason}",
            )
            return PostgameTurn(
                False,
                f"CITY GROWTH DEFERRED: {result.reason} (no-progress {attempt.no_progress_streak})",
                state_changed=True,
            )

        progress["next_index"] = index + 1
        progress["total"] = len(offsets)
        city["ring"] = ring
        city["districts_completed"] = int(city.get("districts_completed", 0) or 0) + 1
        if progress["next_index"] >= len(offsets):
            city["rings_completed"] = max(
                int(city.get("rings_completed", 0) or 0),
                ring + 1,
            )
            city["ring"] = ring + 1
            city["ring_progress"] = {}
        after = productive_snapshot(self.state)
        attempt = record_productive_attempt(
            self.state, "postgame_city_district", before, after,
            detail=f"verified ring {ring} district {dcx},{dcz}",
        )
        if not attempt.progressed:
            return PostgameTurn(False, "CITY GROWTH DEFERRED: district lacked durable evidence", state_changed=True)
        return PostgameTurn(True, f"CITY GROWTH: verified ring {ring} district {dcx},{dcz}", state_changed=True)
