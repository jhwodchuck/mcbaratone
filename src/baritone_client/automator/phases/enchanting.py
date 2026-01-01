"""
Enchanting Phase - Ensure enchanting infrastructure exists.
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common.tasks import TaskResult


class EnchantingHandler(PhaseHandler):
    """Best-effort handler for the enchanting progression step."""

    def get_name(self) -> str:
        return "Enchanting"

    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        """
        Placeholder implementation that validates inventory requirements.

        If the phase is already satisfied (per ResourceManager), the handler
        reports success immediately. Otherwise it surfaces the missing items so
        the orchestration layer can decide whether to retry or move on.
        """
        ready = resources.phase_ready_result(Phase.ENCHANTING, "Enchanting requirements already met")
        if ready:
            return ready

        resources.refresh_inventory()
        missing = resources.check_phase_requirements(Phase.ENCHANTING)
        if missing:
            return TaskResult.fail("Enchanting materials missing", missing=missing)
        return TaskResult.ok("Enchanting complete")
