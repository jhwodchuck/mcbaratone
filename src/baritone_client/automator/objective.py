"""
Objective planner - goal-graph scheduling over the existing phase handlers.

This replaces the strictly-linear ``advance_phase()`` walk (state_manager) and the
fatal-on-first-failure branch (automator) with a dependency graph of objectives.

Design constraints that keep the blast radius small:
- An objective *is* a ``Phase``.  The persisted checkpoint unit is unchanged, so
  ``StateManager`` versioning and the Suite 1200 acceptance gates keep working.
- The phase *handlers* are untouched: the planner only decides which handler runs
  next and what happens when one fails.
- No cost/reward/utility scoring yet.  Selection is priority + graph depth.  Those
  fields are intentionally deferred until handlers emit real timing/success
  telemetry (otherwise scoring is invented numbers).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Set

from .state_manager import Phase


class ObjStatus(Enum):
    """Lifecycle of a single objective within one run."""
    PENDING = auto()     # not yet run
    ACTIVE = auto()      # currently executing
    DONE = auto()        # handler succeeded
    BLOCKED = auto()     # failed, attempts remain -> retry after other goals move
    ABANDONED = auto()   # failed too many times -> skip, but do NOT halt the run


@dataclass
class Objective:
    """A schedulable goal wrapping one phase handler.

    Attributes:
        phase: The ``Phase`` whose registered handler this objective runs.
        requires: Objectives (by phase) that must be DONE before this is runnable.
        priority: Higher wins when several objectives are simultaneously runnable.
        max_attempts: Failures before the objective is ABANDONED instead of retried.
        terminal: Completing this objective means the whole run is won.
    """
    phase: Phase
    requires: List[Phase] = field(default_factory=list)
    priority: int = 0
    max_attempts: int = 3
    max_interruptions: int = 6
    max_no_progress: int = 3
    terminal: bool = False
    # --- deferred: the utility-planner era fills these in once telemetry is real ---
    # est_cost: Optional[float] = None
    # est_reward: Optional[float] = None
    # interrupt_when: Optional[Callable] = None
    # recover_with: Optional[Callable] = None

    status: ObjStatus = ObjStatus.PENDING
    attempts: int = 0
    interruptions: int = 0
    no_progress_streak: int = 0
    last_evidence: str = ""
    last_failure: str = ""


def default_objectives() -> List[Objective]:
    """The production goal graph.

    Today's linear enum order is the trivial chain; the graph makes real structure
    explicit -- e.g. NETHER_AND_BLAZE depends on FOOD_AND_IRON, *not* on the
    enchanting pipeline, so the two become runnable as siblings.
    """
    return [
        Objective(Phase.BRIDGE_CHECK),
        Objective(Phase.SPAWN_BOOTSTRAP, requires=[Phase.BRIDGE_CHECK]),
        Objective(Phase.INITIAL_GATHERING, requires=[Phase.SPAWN_BOOTSTRAP]),
        # Stabilize tools, food, storage, and a starter farm before committing
        # to a full 7x7 house. A starving bot must not be gated behind a
        # cosmetic construction objective.
        Objective(Phase.BOOT_SEQUENCE, requires=[Phase.INITIAL_GATHERING]),
        Objective(Phase.BASE_CONSTRUCTION, requires=[Phase.BOOT_SEQUENCE]),
        Objective(Phase.FOOD_AND_IRON, requires=[Phase.BASE_CONSTRUCTION]),
        # Nether progression is the shortest critical path to end-game.
        # Keep it ahead of enchanting so stalled bots don't re-enter the
        # enchanting branch before building and traversing a nether portal.
        Objective(Phase.NETHER_AND_BLAZE, requires=[Phase.FOOD_AND_IRON], priority=2),
        Objective(Phase.ENCHANTING_PIPELINE, requires=[Phase.FOOD_AND_IRON], priority=1),
        Objective(Phase.VILLAGER_INFRA, requires=[Phase.FOOD_AND_IRON], priority=0),
        Objective(Phase.XP_ENGINE, requires=[Phase.VILLAGER_INFRA]),
        Objective(Phase.IRON_FARM, requires=[Phase.VILLAGER_INFRA]),
        Objective(Phase.TOOL_PERFECTION,
                  requires=[Phase.XP_ENGINE, Phase.NETHER_AND_BLAZE]),
        Objective(Phase.WORLD_UNLOCK, requires=[Phase.NETHER_AND_BLAZE]),
        Objective(Phase.MEGABASE_INIT,
                  requires=[Phase.WORLD_UNLOCK, Phase.IRON_FARM], terminal=True),
    ]


class ObjectivePlanner:
    """Pure scheduling logic over a set of objectives.

    Deliberately free of any ``Client``/``ResourceManager`` dependency so it can be
    unit-tested in isolation -- readiness is supplied as a callable.
    """

    def __init__(self, objectives: Optional[List[Objective]] = None):
        self.objectives: List[Objective] = objectives or default_objectives()
        self._by_phase: Dict[Phase, Objective] = {o.phase: o for o in self.objectives}

    # -- queries -------------------------------------------------------------

    def completed_phases(self) -> Set[Phase]:
        """Phases whose objective is DONE."""
        return {o.phase for o in self.objectives if o.status is ObjStatus.DONE}

    def runnable(self, is_ready: Optional[Callable[[Phase], bool]] = None) -> List[Objective]:
        """Objectives whose graph prerequisites are satisfied and that still want
        to run.

        Args:
            is_ready: Optional extra gate ``phase -> bool``.  NOTE: do *not* pass
                ``ResourceManager.is_phase_ready`` here -- in this codebase
                PHASE_REQUIREMENTS are a phase's outputs, so that predicate is a
                completion check, not a start check.  The parameter exists for a
                future real precondition gate and for testing.
        """
        done = self.completed_phases()
        result: List[Objective] = []
        for o in self.objectives:
            if o.status not in (ObjStatus.PENDING, ObjStatus.BLOCKED):
                continue
            if not all(req in done for req in o.requires):
                continue
            if is_ready is not None and not is_ready(o.phase):
                continue
            result.append(o)
        return result

    def select(self, ready: Iterable[Objective]) -> Optional[Objective]:
        """Choose the next objective: highest priority, then shallowest graph depth.

        This is the placeholder for ``max(ready, key=utility)`` once utility scoring
        lands.
        """
        candidates = list(ready)
        if not candidates:
            return None
        return max(candidates, key=lambda o: (o.priority, -len(o.requires)))

    def is_complete(self) -> bool:
        """True once every terminal objective is DONE."""
        terminals = [o for o in self.objectives if o.terminal]
        return bool(terminals) and all(o.status is ObjStatus.DONE for o in terminals)

    def is_stalled(self, is_ready: Optional[Callable[[Phase], bool]] = None) -> bool:
        """True when nothing is runnable but the run is not won -- e.g. every
        remaining objective is ABANDONED, or its prerequisites can never be met."""
        if self.is_complete():
            return False
        return not self.runnable(is_ready)

    # -- transitions ---------------------------------------------------------

    def mark_active(self, obj: Objective) -> None:
        obj.status = ObjStatus.ACTIVE
        obj.attempts += 1

    def mark_done(self, obj: Objective) -> None:
        obj.status = ObjStatus.DONE

    def mark_yielded(self, obj: Objective, reason: str = "") -> bool:
        """Budget an interrupted objective instead of retrying it forever."""
        obj.interruptions += 1
        obj.last_failure = str(reason or "interrupted")
        obj.attempts = max(0, obj.attempts - 1)
        if (
            obj.interruptions >= obj.max_interruptions
            or obj.no_progress_streak >= obj.max_no_progress
        ):
            obj.status = ObjStatus.ABANDONED
            return False
        obj.status = ObjStatus.BLOCKED
        return True

    def mark_failed(self, obj: Objective) -> bool:
        """Record a failed attempt.

        Returns:
            True if the objective was re-queued (BLOCKED, will retry after other
            goals advance); False if it was ABANDONED.
        """
        if (
            obj.attempts >= obj.max_attempts
            or obj.no_progress_streak >= obj.max_no_progress
        ):
            obj.status = ObjStatus.ABANDONED
            return False
        obj.status = ObjStatus.BLOCKED
        return True

    def record_evidence(self, obj: Objective, fingerprint: str) -> bool:
        """Record whether an attempt changed durable capability evidence."""
        if not fingerprint:
            return True
        if obj.last_evidence and obj.last_evidence == fingerprint:
            obj.no_progress_streak += 1
            return False
        obj.last_evidence = fingerprint
        obj.no_progress_streak = 0
        return True

    def runtime_state(self) -> Dict[str, dict]:
        """Serialize failure budgets and progress evidence for checkpoints."""
        return {
            objective.phase.name: {
                "status": objective.status.name,
                "attempts": objective.attempts,
                "interruptions": objective.interruptions,
                "no_progress_streak": objective.no_progress_streak,
                "last_evidence": objective.last_evidence,
                "last_failure": objective.last_failure,
            }
            for objective in self.objectives
        }

    # -- resume --------------------------------------------------------------

    def restore(
        self,
        completed: Iterable[Phase],
        runtime: Optional[Mapping[str, dict]] = None,
    ) -> None:
        """Mark the given phases DONE (checkpoint resume).

        Objectives not listed are reset to PENDING so the graph re-derives what is
        runnable from the restored completion set.
        """
        completed_set = set(completed)
        runtime = runtime if isinstance(runtime, Mapping) else {}
        for o in self.objectives:
            if o.phase in completed_set:
                o.status = ObjStatus.DONE
            else:
                saved = runtime.get(o.phase.name, {})
                if not isinstance(saved, Mapping):
                    saved = {}
                status_name = str(saved.get("status", "PENDING"))
                try:
                    saved_status = ObjStatus[status_name]
                except KeyError:
                    saved_status = ObjStatus.PENDING
                # Runtime data carries budgets, not completion authority.
                # A killed process cannot still own ACTIVE, and DONE is only
                # restored from the separately verified completion set.
                if saved_status is ObjStatus.ACTIVE:
                    o.status = ObjStatus.BLOCKED
                elif saved_status is ObjStatus.DONE:
                    o.status = ObjStatus.PENDING
                else:
                    o.status = saved_status
                o.attempts = max(0, int(saved.get("attempts", 0) or 0))
                o.interruptions = max(
                    0, int(saved.get("interruptions", 0) or 0)
                )
                o.no_progress_streak = max(
                    0, int(saved.get("no_progress_streak", 0) or 0)
                )
                o.last_evidence = str(saved.get("last_evidence", "") or "")
                o.last_failure = str(saved.get("last_failure", "") or "")

    def restore_linear(self, current_phase: Phase) -> None:
        """Back-compat resume for old checkpoints that only persisted a single
        ``current_phase``: treat every phase earlier in enum order as DONE."""
        order = list(Phase)
        try:
            idx = order.index(current_phase)
        except ValueError:
            return
        self.restore([p for p in order[:idx] if p in self._by_phase])
