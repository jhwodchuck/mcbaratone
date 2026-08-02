"""
Action suites corresponding to the Integration & Milestone Tests (T900-T999) 
defined in extended_test_plan.md.
"""

from .composition import (
    CompositeAction,
    sequence,
)
from .initial_gathering import WoodCollectionPhase, ToolProgressionPhase, StoneCollectionPhase, BedPreparationPhase, SurvivalPhase
from .base import BaseAction
from ..core.interfaces import ActionResult
from ..common.tasks import normalize_task_result

# Placeholder for actions that might not directly exist in this form yet
class PlaceholderAction(BaseAction):
    def __init__(self, name):
        super().__init__()
        self.name = name
    
    def execute(self, context):
        return ActionResult.fail(
            f"Placeholder action '{self.name}' is not implemented",
            action_name=self.name,
        )


class SuiteSequenceAction(CompositeAction):
    """Concrete composite that delegates to the standard fail-fast sequence."""

    def __init__(self, *actions):
        super().__init__(list(actions))
        self.action = sequence(*actions)

    def execute(self, context):
        return self.action.execute(context)


class ProductionPhaseAction(BaseAction):
    """Run one production handler and apply its production postcondition gate."""

    def __init__(self, phase, handler_factory):
        super().__init__()
        self.phase = phase
        self.handler_factory = handler_factory

    def execute(self, context):
        if context.resources is None or context.state is None:
            return ActionResult.fail(
                f"{self.phase.name} suite requires production resources and state"
            )
        handler = self.handler_factory()
        result = normalize_task_result(
            handler.execute(context.client, context.resources, context.state)
        )
        if not result.success:
            return ActionResult.fail(result.reason, **result.data)

        from ..automator.phase_verifier import PhaseVerifier

        verification = PhaseVerifier(
            context.client, context.resources, context.state
        ).verify(self.phase, result)
        if not verification.success:
            return ActionResult.fail(
                f"Production phase verification failed: {verification.reason}",
                gate_ids=list(verification.gate_ids),
                handler_result=result.data,
            )
        context.state.record_phase_payload(self.phase, result.data)
        return ActionResult.ok(result.reason, **result.data)


class SurvivalLoopAction(SuiteSequenceAction):
    """
    T900: Survival Loop
    Goal: Gather wood -> craft tools -> mine stone -> build shelter
    Timeout: 180s
    """
    def __init__(self):
        super().__init__(
            WoodCollectionPhase(),
            ToolProgressionPhase(),
            StoneCollectionPhase(),
            BedPreparationPhase(), # Includes shelter concepts usually
            SurvivalPhase()        # Basic survival checks
        )

class IronAgeAction(SuiteSequenceAction):
    """
    T901: Iron Age Progression
    Goal: Find iron -> smelt -> craft iron gear
    Timeout: 300s
    """
    def __init__(self):
        from ..automator.phases.iron_age import FoodAndIronHandler
        from ..automator.state_manager import Phase

        super().__init__(
            ProductionPhaseAction(Phase.FOOD_AND_IRON, FoodAndIronHandler)
        )

class NetherJourneyAction(SuiteSequenceAction):
    """
    T902: Nether Journey
    Goal: Build/light portal -> enter nether -> find fortress -> farm blaze rods
    Timeout: 600s
    """
    def __init__(self):
        from ..automator.phases.nether_prep import NetherAndBlazeHandler
        from ..automator.state_manager import Phase

        super().__init__(
            ProductionPhaseAction(Phase.NETHER_AND_BLAZE, NetherAndBlazeHandler)
        )

class StrongholdAction(SuiteSequenceAction):
    """
    T903: Stronghold to End
    Goal: Triangulate -> navigate -> open portal -> enter end
    Timeout: 900s
    """
    def __init__(self):
        from ..automator.phases.end_game import WorldUnlockHandler
        from ..automator.state_manager import Phase

        super().__init__(
            ProductionPhaseAction(Phase.WORLD_UNLOCK, WorldUnlockHandler)
        )

class DragonFightAction(SuiteSequenceAction):
    """
    Part of T904: Dragon Combat
    """
    def __init__(self):
        super().__init__(
            PlaceholderAction(
                "DragonFightAction is included in the WORLD_UNLOCK handler"
            )
        )

class CompleteRunAction(SuiteSequenceAction):
    """
    T904: Complete Run
    Goal: Full spawn to dragon defeat
    Timeout: 3600s
    """
    def __init__(self):
        super().__init__(
            PlaceholderAction(
                "T904 one-shot complete run is unavailable; use EndGameAutomator"
            )
        )

# Mapping of suite names to Action classes
SUITES = {
    "T900": SurvivalLoopAction,
    "T901": IronAgeAction,
    "T902": NetherJourneyAction,
    "T903": StrongholdAction,
    "T904": CompleteRunAction
}
