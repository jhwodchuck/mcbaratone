"""
Offline tests for the goal-graph scheduler (automator/objective.py).

Covers the two behaviors that distinguish it from the old linear phase walk:
  1. Sibling objectives become runnable together and are ordered by priority/depth.
  2. A failed objective is non-fatal: it is re-queued (BLOCKED) then ABANDONED,
     and the planner keeps offering other runnable objectives instead of halting.
"""

import unittest

from baritone_client.automator.state_manager import Phase
from baritone_client.automator.objective import (
    Objective,
    ObjStatus,
    ObjectivePlanner,
    default_objectives,
)


class SiblingOrderingTest(unittest.TestCase):
    """The production graph exposes real parallelism the linear enum could not."""

    def test_food_and_iron_unlocks_three_siblings(self):
        planner = ObjectivePlanner(default_objectives())
        # Drive the chain up to and including FOOD_AND_IRON.
        for phase in (Phase.BRIDGE_CHECK, Phase.SPAWN_BOOTSTRAP,
                      Phase.INITIAL_GATHERING, Phase.BOOT_SEQUENCE,
                      Phase.BASE_CONSTRUCTION, Phase.FOOD_AND_IRON):
            ready = planner.runnable()
            obj = planner.select(ready)
            self.assertEqual(obj.phase, phase,
                             f"expected {phase.name} to be the only runnable objective")
            planner.mark_active(obj)
            planner.mark_done(obj)

        runnable_phases = {o.phase for o in planner.runnable()}
        self.assertEqual(
            runnable_phases,
            {Phase.ENCHANTING_PIPELINE, Phase.NETHER_AND_BLAZE, Phase.VILLAGER_INFRA},
            "all three post-food objectives should be runnable as siblings",
        )

    def test_priority_breaks_the_sibling_tie(self):
        planner = ObjectivePlanner(default_objectives())
        for phase in (Phase.BRIDGE_CHECK, Phase.SPAWN_BOOTSTRAP,
                      Phase.INITIAL_GATHERING, Phase.BOOT_SEQUENCE,
                      Phase.BASE_CONSTRUCTION, Phase.FOOD_AND_IRON):
            obj = planner.select(planner.runnable())
            planner.mark_active(obj)
            planner.mark_done(obj)

        chosen = planner.select(planner.runnable())
        # NETHER_AND_BLAZE carries highest post-FOOD_AND_IRON priority.
        self.assertEqual(chosen.phase, Phase.NETHER_AND_BLAZE)


class NonFatalFailureTest(unittest.TestCase):
    """A failing objective must not stop the whole run."""

    def _linear(self):
        # a -> b, plus an independent c, with a terminal d gated on b and c.
        a = Objective(Phase.BRIDGE_CHECK)
        b = Objective(Phase.SPAWN_BOOTSTRAP, requires=[Phase.BRIDGE_CHECK], max_attempts=2)
        c = Objective(Phase.INITIAL_GATHERING, requires=[Phase.BRIDGE_CHECK])
        d = Objective(Phase.BASE_CONSTRUCTION,
                      requires=[Phase.SPAWN_BOOTSTRAP, Phase.INITIAL_GATHERING],
                      terminal=True)
        return ObjectivePlanner([a, b, c, d])

    def test_failure_requeues_then_abandons_without_halting(self):
        planner = self._linear()
        a = planner.select(planner.runnable())
        planner.mark_active(a)
        planner.mark_done(a)

        b = planner._by_phase[Phase.SPAWN_BOOTSTRAP]

        # First failure -> BLOCKED (re-queued), still runnable next pass.
        planner.mark_active(b)
        self.assertTrue(planner.mark_failed(b))
        self.assertEqual(b.status, ObjStatus.BLOCKED)
        self.assertIn(b, planner.runnable())
        # The independent sibling c is available meanwhile -> run never stalls.
        self.assertIn(planner._by_phase[Phase.INITIAL_GATHERING], planner.runnable())

        # Second failure hits max_attempts -> ABANDONED, dropped from runnable.
        planner.mark_active(b)
        self.assertFalse(planner.mark_failed(b))
        self.assertEqual(b.status, ObjStatus.ABANDONED)
        self.assertNotIn(b, planner.runnable())

    def test_abandoned_prerequisite_stalls_gracefully_not_crashes(self):
        planner = self._linear()
        for phase in (Phase.BRIDGE_CHECK, Phase.INITIAL_GATHERING):
            obj = planner._by_phase[phase]
            planner.mark_active(obj)
            planner.mark_done(obj)

        b = planner._by_phase[Phase.SPAWN_BOOTSTRAP]
        for _ in range(b.max_attempts):
            planner.mark_active(b)
            planner.mark_failed(b)
        self.assertEqual(b.status, ObjStatus.ABANDONED)

        # Terminal d is gated on the abandoned b: not runnable, not complete, stalled.
        self.assertIsNone(planner.select(planner.runnable()))
        self.assertFalse(planner.is_complete())
        self.assertTrue(planner.is_stalled())


class CompletionAndResumeTest(unittest.TestCase):

    def test_yield_budget_eventually_abandons_repeated_recovery(self):
        objective = Objective(
            Phase.BRIDGE_CHECK,
            max_interruptions=2,
        )
        planner = ObjectivePlanner([objective])

        planner.mark_active(objective)
        self.assertTrue(planner.mark_yielded(objective, "survival_recovery"))
        self.assertEqual(objective.status, ObjStatus.BLOCKED)
        self.assertEqual(objective.interruptions, 1)

        planner.mark_active(objective)
        self.assertFalse(planner.mark_yielded(objective, "survival_recovery"))
        self.assertEqual(objective.status, ObjStatus.ABANDONED)
        self.assertEqual(objective.interruptions, 2)

    def test_runtime_state_survives_restore(self):
        planner = ObjectivePlanner(default_objectives())
        objective = planner._by_phase[Phase.INITIAL_GATHERING]
        objective.status = ObjStatus.BLOCKED
        objective.attempts = 2
        objective.interruptions = 4
        objective.no_progress_streak = 2
        objective.last_evidence = "same-evidence"

        restored = ObjectivePlanner(default_objectives())
        restored.restore(
            [Phase.BRIDGE_CHECK, Phase.SPAWN_BOOTSTRAP],
            runtime=planner.runtime_state(),
        )
        recovered = restored._by_phase[Phase.INITIAL_GATHERING]

        self.assertEqual(recovered.status, ObjStatus.BLOCKED)
        self.assertEqual(recovered.attempts, 2)
        self.assertEqual(recovered.interruptions, 4)
        self.assertEqual(recovered.no_progress_streak, 2)
        self.assertEqual(recovered.last_evidence, "same-evidence")

    def test_runtime_status_cannot_restore_unverified_completion(self):
        planner = ObjectivePlanner(default_objectives())
        planner.restore(
            [],
            runtime={
                "BRIDGE_CHECK": {
                    "status": "DONE",
                    "attempts": 1,
                }
            },
        )

        self.assertNotIn(Phase.BRIDGE_CHECK, planner.completed_phases())
        self.assertEqual(
            planner._by_phase[Phase.BRIDGE_CHECK].status,
            ObjStatus.PENDING,
        )

    def test_default_graph_stabilizes_survival_before_full_house(self):
        planner = ObjectivePlanner(default_objectives())
        for phase in (
            Phase.BRIDGE_CHECK,
            Phase.SPAWN_BOOTSTRAP,
            Phase.INITIAL_GATHERING,
        ):
            objective = planner.select(planner.runnable())
            self.assertEqual(objective.phase, phase)
            planner.mark_active(objective)
            planner.mark_done(objective)

        self.assertEqual(
            planner.select(planner.runnable()).phase,
            Phase.BOOT_SEQUENCE,
        )

    def test_is_complete_only_when_terminal_done(self):
        planner = ObjectivePlanner(default_objectives())
        # Complete everything except the terminal CITY_BUILD objective.
        for o in planner.objectives:
            if not o.terminal:
                o.status = ObjStatus.DONE
        self.assertFalse(planner.is_complete())

        planner._by_phase[Phase.CITY_BUILD].status = ObjStatus.DONE
        self.assertTrue(planner.is_complete())

    def test_restore_marks_completed_and_resets_rest(self):
        planner = ObjectivePlanner(default_objectives())
        planner.restore([Phase.BRIDGE_CHECK, Phase.SPAWN_BOOTSTRAP])
        self.assertEqual(planner.completed_phases(),
                         {Phase.BRIDGE_CHECK, Phase.SPAWN_BOOTSTRAP})
        # INITIAL_GATHERING (requires SPAWN_BOOTSTRAP) is now the runnable frontier.
        self.assertEqual(planner.select(planner.runnable()).phase,
                         Phase.INITIAL_GATHERING)

    def test_restore_linear_backfills_enum_prefix(self):
        planner = ObjectivePlanner(default_objectives())
        planner.restore_linear(Phase.BOOT_SEQUENCE)
        # Everything before BOOT_SEQUENCE in enum order is DONE...
        self.assertIn(Phase.BASE_CONSTRUCTION, planner.completed_phases())
        self.assertNotIn(Phase.BOOT_SEQUENCE, planner.completed_phases())
        # ...so BOOT_SEQUENCE is the frontier.
        self.assertEqual(planner.select(planner.runnable()).phase, Phase.BOOT_SEQUENCE)

    def test_legacy_complete_does_not_attest_new_postgame_phases(self):
        planner = ObjectivePlanner(default_objectives())
        planner.restore_linear(Phase.COMPLETE)

        self.assertIn(Phase.MEGABASE_INIT, planner.completed_phases())
        self.assertNotIn(Phase.TERRAFORM, planner.completed_phases())
        self.assertNotIn(Phase.CITY_BUILD, planner.completed_phases())
        self.assertFalse(planner.is_complete())
        self.assertEqual(
            planner.select(planner.runnable()).phase,
            Phase.TERRAFORM,
        )


if __name__ == "__main__":
    unittest.main()
