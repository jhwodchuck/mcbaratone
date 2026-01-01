"""
End Portal Phase - Activate and enter the End portal.
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import activate_end_portal, enter_end_portal, find_end_portal
from ...common.tasks import TaskResult


class EndPortalHandler(PhaseHandler):
    """Handler for end portal activation phase."""
    
    def get_name(self) -> str:
        return "End Portal Activation"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        """Fill and enter the End portal."""
        ready = resources.phase_ready_result(Phase.END_PORTAL, "End portal already activated")
        if ready:
            return ready

        # 1. Find Portal
        print("Searching for End Portal...")
        found = find_end_portal(client)
        if not found:
            return TaskResult.fail("Failed to find end portal nearby.")
            
        # 2. Activate Portal
        print("Placing Eyes of Ender...")
        if not activate_end_portal(client):
            return TaskResult.fail("Failed to activate portal.")
            
        # 3. Enter Portal
        print("Entering The End...")
        if not enter_end_portal(client):
            return TaskResult.fail("Failed to enter portal.")
            
        resources.refresh_inventory()
        missing = resources.check_phase_requirements(Phase.END_PORTAL)
        if missing:
            return TaskResult.fail("Portal entered but requirements flagged", missing=missing)
        return TaskResult.ok("Entered The End", portal_found=found)
