"""Persistent campaign strategy above the mechanical objective graph.

The objective planner answers which handler may run next.  This module records
why that work matters, what the campaign has learned, and which irreversible
choices (notably a permanent home) it has made.  The data lives in checkpoint
``custom_data`` so restarts do not turn a mature campaign back into a fresh
survival run.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Sequence

from ..observability import emit_event
from .state_manager import Phase


class StrategicPhase(Enum):
    SURVIVE = 0
    ESTABLISH_HOME = 1
    BUILD_INFRASTRUCTURE = 2
    PROGRESS_TECHNOLOGY = 3
    PREPARE_FOR_END = 4
    DEFEAT_DRAGON = 5
    BUILD_MEGACITY = 6


PHASE_COMPLETION_CRITERIA = {
    StrategicPhase.SURVIVE: [
        "spawn recorded",
        "renewable food or a durable recovery path",
        "basic tools and shelter verified",
    ],
    StrategicPhase.ESTABLISH_HOME: [
        "home candidate meets the minimum score",
        "permanent-home decision and reason persisted",
        "shelter, storage, workstations, and safe return verified",
    ],
    StrategicPhase.BUILD_INFRASTRUCTURE: [
        "renewable food exceeds routine consumption",
        "storage deposit and withdrawal cycle verified",
        "resource excursion returns to the same home",
    ],
    StrategicPhase.PROGRESS_TECHNOLOGY: [
        "iron gear and replacement tools sustainable",
        "enchanting, XP, and critical production systems verified",
    ],
    StrategicPhase.PREPARE_FOR_END: [
        "Nether portal and travel verified",
        "blaze and pearl supply acquired organically",
        "stronghold, staging area, and dragon kit verified",
    ],
    StrategicPhase.DEFEAT_DRAGON: [
        "End entered with the verified kit",
        "dragon defeat and surviving return observed",
    ],
    StrategicPhase.BUILD_MEGACITY: [
        "city plan and protected civic core persisted",
        "districts, transport, industry, and logistics expand across restarts",
    ],
}

OBJECTIVE_TEXT = {
    Phase.BRIDGE_CHECK: "Verify the controller/bridge contract",
    Phase.SPAWN_BOOTSTRAP: "Record spawn and establish immediate survival",
    Phase.INITIAL_GATHERING: "Gather the minimum renewable starter supplies",
    Phase.BOOT_SEQUENCE: "Select and establish a permanent home",
    Phase.BASE_CONSTRUCTION: "Verify shelter, storage, and workstations",
    Phase.FOOD_AND_IRON: "Stabilize renewable food and iron equipment",
    Phase.ENCHANTING_PIPELINE: "Build a level-30 enchanting pipeline",
    Phase.NETHER_AND_BLAZE: "Establish Nether travel and acquire six blaze rods",
    Phase.VILLAGER_INFRA: "Build sustainable villager infrastructure",
    Phase.XP_ENGINE: "Establish a renewable XP engine",
    Phase.IRON_FARM: "Establish renewable iron production",
    Phase.TOOL_PERFECTION: "Build a durable enchanted tool economy",
    Phase.WORLD_UNLOCK: "Prepare for, enter, and complete the End expedition",
    Phase.MEGABASE_INIT: "Select and plan the permanent civic core",
    Phase.TERRAFORM: "Prepare coherent city expansion terrain",
    Phase.CITY_BUILD: "Expand the planned megacity",
    Phase.COMPLETE: "Continue planned post-game city growth",
}


def _coordinate(value: Any) -> Optional[list[int]]:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    try:
        return [int(float(value[0])), int(float(value[1])), int(float(value[2]))]
    except (TypeError, ValueError):
        return None


def _append_unique(records: list[Any], record: Any, *, position_key: str = "position") -> None:
    if not isinstance(record, Mapping):
        return
    position = _coordinate(record.get(position_key))
    dimension = str(record.get("dimension", "minecraft:overworld"))
    for existing in records:
        if not isinstance(existing, Mapping):
            continue
        if (
            _coordinate(existing.get(position_key)) == position
            and str(existing.get("dimension", "minecraft:overworld")) == dimension
        ):
            existing.update(dict(record))
            return
    records.append(dict(record))


class StrategicState:
    """Maintain a normalized, inspectable strategic checkpoint record."""

    KEY = "strategic_state"
    VERSION = 1
    HOME_MINIMUM_SCORE = 65
    RELOCATION_MARGIN = 15

    def __init__(self, state: Any):
        self.state = state
        self.data = self._ensure_data()

    def _ensure_data(self) -> dict[str, Any]:
        custom = self.state.custom_data
        raw = custom.get(self.KEY)
        data = raw if isinstance(raw, dict) else {}
        data.setdefault("version", self.VERSION)
        data.setdefault("phase", StrategicPhase.SURVIVE.name)
        data.setdefault("active_phase", data["phase"])
        data.setdefault("survival_override", None)
        data.setdefault("phase_history", [])
        data.setdefault("current_major_objective", OBJECTIVE_TEXT[Phase.BRIDGE_CHECK])
        data.setdefault("blocking_condition", None)
        data.setdefault("spawn_location", None)
        data.setdefault("permanent_home", None)
        data.setdefault("previous_bases", [])
        data.setdefault("candidate_homes", [])
        data.setdefault("known_villages", [])
        data.setdefault("known_structures", [])
        data.setdefault("resource_locations", [])
        data.setdefault("portals", [])
        data.setdefault("stronghold", None)
        data.setdefault("major_infrastructure", [])
        data.setdefault("deaths_and_failures", [])
        data.setdefault("completed_milestones", [])
        data.setdefault("planned_city_expansion", None)
        data.setdefault(
            "phase_completion_criteria",
            {phase.name: list(criteria) for phase, criteria in PHASE_COMPLETION_CRITERIA.items()},
        )
        data.setdefault("last_review", {})
        for key in (
            "phase_history", "previous_bases", "candidate_homes", "known_villages",
            "known_structures", "resource_locations", "portals", "major_infrastructure",
            "deaths_and_failures", "completed_milestones",
        ):
            if not isinstance(data.get(key), list):
                data[key] = []
        custom[self.KEY] = data
        return data

    def _locations(self) -> Mapping[str, Any]:
        locations = self.state.custom_data.get("locations", {})
        return locations if isinstance(locations, Mapping) else {}

    def _import_location_knowledge(self) -> None:
        locations = self._locations()
        spawn = locations.get("spawn", [])
        if self.data.get("spawn_location") is None and isinstance(spawn, list) and spawn:
            first = spawn[0]
            if isinstance(first, Mapping):
                self.data["spawn_location"] = {
                    "position": [first.get("x"), first.get("y"), first.get("z")],
                    "dimension": first.get("dimension", "minecraft:overworld"),
                    "recorded_at": first.get("timestamp"),
                }

        for category, entries in locations.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                record = {
                    "kind": str(category),
                    "position": [entry.get("x"), entry.get("y"), entry.get("z")],
                    "dimension": entry.get("dimension", "minecraft:overworld"),
                    "tags": list(entry.get("tags", [])),
                    "recorded_at": entry.get("timestamp"),
                }
                name = str(category).lower()
                if "village" in name:
                    _append_unique(self.data["known_villages"], record)
                elif "portal" in name or "gateway" in name:
                    _append_unique(self.data["portals"], record)
                elif any(token in name for token in ("ore", "wood", "food", "farm", "mine", "water")):
                    _append_unique(self.data["resource_locations"], record)
                elif name not in {"spawn", "chest"}:
                    _append_unique(self.data["known_structures"], record)

    def _import_legacy_state(self) -> None:
        custom = self.state.custom_data
        self._import_location_knowledge()
        if self.data.get("permanent_home") is None:
            home = _coordinate(custom.get("homestead_anchor"))
            if home is not None:
                self.data["permanent_home"] = {
                    "position": home,
                    "dimension": "minecraft:overworld",
                    "score": None,
                    "status": "legacy_unscored",
                    "selected_at": None,
                    "reason": "Imported from the existing homestead checkpoint",
                }
        stronghold = custom.get("stronghold_coords")
        if self.data.get("stronghold") is None and isinstance(stronghold, (list, tuple)):
            if len(stronghold) >= 2:
                self.data["stronghold"] = {
                    "position": [int(stronghold[0]), None, int(stronghold[1])],
                    "status": "triangulated",
                }
        structures = custom.get("structures", {})
        if isinstance(structures, Mapping):
            for name, record in structures.items():
                if not isinstance(record, Mapping):
                    continue
                infrastructure = dict(record)
                infrastructure["kind"] = str(name)
                if "location" in infrastructure:
                    infrastructure["position"] = infrastructure.pop("location")
                _append_unique(self.data["major_infrastructure"], infrastructure)
        milestones = custom.get("milestones", {})
        if isinstance(milestones, Mapping):
            completed = set(self.data["completed_milestones"])
            completed.update(str(name) for name, value in milestones.items() if value)
            self.data["completed_milestones"] = sorted(completed)
        plan = custom.get("terraform_plan") or custom.get("city_progress")
        if isinstance(plan, Mapping):
            self.data["planned_city_expansion"] = dict(plan)

        for key in ("last_death_location", "death_recovery", "last_abandoned_death_recovery"):
            raw_failure = custom.get(key)
            if not isinstance(raw_failure, Mapping):
                continue
            failure = dict(raw_failure)
            location = failure.get("location", failure.get("pending_location"))
            if location is None and all(axis in failure for axis in ("x", "y", "z")):
                location = [failure.get("x"), failure.get("y"), failure.get("z")]
            failure["position"] = location
            failure["kind"] = key
            _append_unique(self.data["deaths_and_failures"], failure)

        runtime = custom.get("objective_runtime", {})
        if isinstance(runtime, Mapping):
            for name, record in runtime.items():
                if not isinstance(record, Mapping) or not record.get("last_failure"):
                    continue
                failure = {
                    "kind": "objective_failure",
                    "objective": str(name),
                    "reason": str(record["last_failure"]),
                    "attempts": int(record.get("attempts", 0) or 0),
                    "no_progress_streak": int(record.get("no_progress_streak", 0) or 0),
                }
                replaced = False
                for existing in self.data["deaths_and_failures"]:
                    if (
                        isinstance(existing, Mapping)
                        and existing.get("kind") == "objective_failure"
                        and existing.get("objective") == str(name)
                    ):
                        existing.update(failure)
                        replaced = True
                        break
                if not replaced:
                    self.data["deaths_and_failures"].append(failure)

    @staticmethod
    def _desired_phase(current: Phase, completed: set[Phase], milestones: Mapping[str, Any]) -> StrategicPhase:
        if milestones.get("dragon_defeated") or current in {
            Phase.MEGABASE_INIT, Phase.TERRAFORM, Phase.CITY_BUILD, Phase.COMPLETE,
        }:
            return StrategicPhase.BUILD_MEGACITY
        if milestones.get("end_entered"):
            return StrategicPhase.DEFEAT_DRAGON
        if current is Phase.WORLD_UNLOCK or Phase.NETHER_AND_BLAZE in completed:
            return StrategicPhase.PREPARE_FOR_END
        if current in {
            Phase.NETHER_AND_BLAZE, Phase.ENCHANTING_PIPELINE, Phase.XP_ENGINE,
            Phase.TOOL_PERFECTION,
        } or Phase.FOOD_AND_IRON in completed:
            return StrategicPhase.PROGRESS_TECHNOLOGY
        if current in {Phase.FOOD_AND_IRON, Phase.VILLAGER_INFRA, Phase.IRON_FARM} or Phase.BASE_CONSTRUCTION in completed:
            return StrategicPhase.BUILD_INFRASTRUCTURE
        if current in {Phase.BOOT_SEQUENCE, Phase.BASE_CONSTRUCTION} or Phase.INITIAL_GATHERING in completed:
            return StrategicPhase.ESTABLISH_HOME
        return StrategicPhase.SURVIVE

    def _set_phase_monotonic(self, desired: StrategicPhase, reason: str) -> None:
        try:
            existing = StrategicPhase[str(self.data.get("phase", "SURVIVE"))]
        except KeyError:
            existing = StrategicPhase.SURVIVE
        if desired.value <= existing.value:
            if not isinstance(self.data.get("survival_override"), Mapping):
                self.data["active_phase"] = existing.name
            return
        phases = list(StrategicPhase)
        for next_phase in phases[existing.value + 1:desired.value + 1]:
            self.data["phase"] = next_phase.name
            self.data["phase_history"].append({
                "from": existing.name,
                "to": next_phase.name,
                "at": time.time(),
                "reason": reason,
            })
            emit_event("strategic_phase_transition", previous=existing.name, current=next_phase.name, reason=reason)
            existing = next_phase
        if not isinstance(self.data.get("survival_override"), Mapping):
            self.data["active_phase"] = existing.name

    def _blocker(self, current: Phase) -> Optional[str]:
        if current in {Phase.NETHER_AND_BLAZE, Phase.WORLD_UNLOCK}:
            readiness = self.state.custom_data.get("end_readiness", {})
            if isinstance(readiness, Mapping):
                missing = readiness.get("missing")
                if isinstance(missing, list) and missing:
                    return ", ".join(str(value) for value in missing)
        runtime = self.state.custom_data.get("objective_runtime", {})
        if isinstance(runtime, Mapping):
            record = runtime.get(current.name, {})
            if isinstance(record, Mapping) and record.get("last_failure"):
                return str(record["last_failure"])
        return None

    def refresh(self, *, current: Optional[Phase] = None, completed: Iterable[Phase] = ()) -> dict[str, Any]:
        """Reconcile legacy checkpoint facts into the strategic record."""
        # StateManager replaces ``custom_data`` when loading a checkpoint, so
        # rebind here instead of retaining the fresh-start dictionary created
        # during EndGameAutomator construction.
        self.data = self._ensure_data()
        self._import_legacy_state()
        current = current or self.state.get_current_phase()
        completed_set = set(completed)
        milestones = self.state.custom_data.get("milestones", {})
        milestones = milestones if isinstance(milestones, Mapping) else {}
        desired = self._desired_phase(current, completed_set, milestones)
        self._set_phase_monotonic(desired, f"mechanical objective {current.name}")
        self.data["current_major_objective"] = OBJECTIVE_TEXT.get(current, current.name)
        self.data["blocking_condition"] = self._blocker(current)
        completed_names = set(self.data["completed_milestones"])
        completed_names.update(phase.name for phase in completed_set)
        self.data["completed_milestones"] = sorted(completed_names)
        self.data["last_review"] = {
            "at": time.time(),
            "current_phase": self.data["phase"],
            "what_is_blocking": self.data["blocking_condition"],
            "activity_contributes_to_goal": current not in completed_set,
            "repeating_completed_work": current in completed_set,
            "abandoning_infrastructure": False,
            "higher_value_action": self.data["current_major_objective"],
        }
        return self.data

    def suspend_for_survival(self, reason: str) -> bool:
        """Temporarily make survival the active strategy without losing progress."""
        if isinstance(self.data.get("survival_override"), Mapping):
            self.data["blocking_condition"] = str(reason)
            return False
        long_term_phase = str(self.data.get("phase", StrategicPhase.SURVIVE.name))
        self.data["survival_override"] = {
            "resume_phase": long_term_phase,
            "resume_objective": self.data.get("current_major_objective"),
            "since": time.time(),
            "reason": str(reason),
        }
        self.data["active_phase"] = StrategicPhase.SURVIVE.name
        self.data["current_major_objective"] = (
            "Restore health, food, and a safe position before resuming progression"
        )
        self.data["blocking_condition"] = str(reason)
        self.data["last_review"] = {
            "at": time.time(),
            "current_phase": StrategicPhase.SURVIVE.name,
            "what_is_blocking": str(reason),
            "activity_contributes_to_goal": True,
            "repeating_completed_work": False,
            "abandoning_infrastructure": False,
            "higher_value_action": self.data["current_major_objective"],
        }
        emit_event(
            "strategic_phase_transition",
            previous=long_term_phase,
            current=StrategicPhase.SURVIVE.name,
            reason=str(reason),
            temporary=True,
        )
        return True

    def resume_from_survival(self) -> bool:
        """Clear a temporary survival override after the admission gate passes."""
        override = self.data.get("survival_override")
        if not isinstance(override, Mapping):
            return False
        resumed = str(self.data.get("phase", override.get("resume_phase", StrategicPhase.SURVIVE.name)))
        self.data["survival_override"] = None
        self.data["active_phase"] = resumed
        self.data["current_major_objective"] = override.get("resume_objective")
        self.data["blocking_condition"] = None
        emit_event(
            "strategic_phase_transition",
            previous=StrategicPhase.SURVIVE.name,
            current=resumed,
            reason="survival admission restored",
            temporary=True,
        )
        return True

    def status_lines(self) -> list[str]:
        blocker = self.data.get("blocking_condition") or "none recorded"
        home = self.data.get("permanent_home")
        home_score = home.get("score") if isinstance(home, Mapping) else None
        active_phase = self.data.get("active_phase", self.data["phase"])
        phase_text = str(active_phase)
        if active_phase != self.data["phase"]:
            phase_text += f" (temporary; long-term {self.data['phase']})"
        return [
            f"Current strategic phase: {phase_text}",
            f"Current major objective: {self.data['current_major_objective']}",
            f"Blocking condition: {blocker}",
            f"Permanent home score: {home_score if home_score is not None else 'unscored'}",
        ]

    def record_home_candidate(
        self, assessment: Mapping[str, Any], *, trigger: str
    ) -> dict[str, Any]:
        """Remember a surveyed site even when the bot never travels there."""
        candidate = dict(assessment)
        candidate["evaluated_at"] = time.time()
        candidate["trigger"] = str(trigger)
        _append_unique(self.data["candidate_homes"], candidate)
        return candidate

    def consider_home(
        self,
        assessment: Mapping[str, Any],
        *,
        trigger: str,
        current_assessment: Optional[Mapping[str, Any]] = None,
        compelling: bool = False,
    ) -> dict[str, Any]:
        """Persist a home candidate and apply a score margin before moving."""
        candidate = self.record_home_candidate(assessment, trigger=trigger)
        candidate_score = int(candidate.get("score", 0) or 0)
        eligible = bool(candidate.get("eligible")) and candidate_score >= self.HOME_MINIMUM_SCORE
        current_home = self.data.get("permanent_home")
        saved_score = current_home.get("score") if isinstance(current_home, Mapping) else None
        if isinstance(current_assessment, Mapping):
            saved_score = current_assessment.get("score")
        current_score = int(saved_score) if isinstance(saved_score, (int, float)) else None

        if not eligible:
            relocate = False
            reason = f"candidate score {candidate_score}/100 is below {self.HOME_MINIMUM_SCORE}"
        elif current_home is None:
            relocate = True
            reason = f"first permanent home meets the {self.HOME_MINIMUM_SCORE}/100 threshold"
        elif compelling:
            relocate = True
            reason = f"compelling relocation trigger: {trigger}"
        elif current_score is None:
            relocate = False
            reason = "existing permanent home is unscored; preserve its infrastructure until directly compared"
        elif candidate_score >= current_score + self.RELOCATION_MARGIN:
            relocate = True
            reason = (
                f"candidate {candidate_score}/100 exceeds current home {current_score}/100 "
                f"by the {self.RELOCATION_MARGIN}-point relocation margin"
            )
        else:
            relocate = False
            reason = (
                f"candidate {candidate_score}/100 does not exceed current home "
                f"{current_score}/100 by {self.RELOCATION_MARGIN} points"
            )

        decision = {
            "decision": "relocate" if relocate else "remain",
            "reason": reason,
            "candidate_score": candidate_score,
            "current_home_score": current_score,
            "trigger": str(trigger),
            "at": time.time(),
        }
        self.data["last_home_decision"] = decision
        if relocate:
            if isinstance(current_home, Mapping):
                previous = dict(current_home)
                previous["replaced_at"] = decision["at"]
                previous["replacement_reason"] = reason
                _append_unique(self.data["previous_bases"], previous)
            self.data["permanent_home"] = {
                "position": _coordinate(candidate.get("position")),
                "dimension": candidate.get("dimension", "minecraft:overworld"),
                "score": candidate_score,
                "factors": dict(candidate.get("factors", {})),
                "status": "committed",
                "selected_at": decision["at"],
                "reason": reason,
            }
        emit_event("home_decision", **decision, position=candidate.get("position"))
        return decision


def strategy_for(owner: Any) -> StrategicState:
    """Return the owner's strategy, including lightweight test/legacy owners."""
    strategy = getattr(owner, "strategy", None)
    if not isinstance(strategy, StrategicState):
        strategy = StrategicState(owner.state)
        try:
            owner.strategy = strategy
        except (AttributeError, TypeError):
            pass
    return strategy
