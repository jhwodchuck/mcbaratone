"""
Nether Travel Phase - Blaze rods and gold.
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import enter_nether_portal, find_nether_fortress, hunt_blazes
from ...common.tasks import TaskResult


class NetherTravelHandler(PhaseHandler):
    """Handler for nether travel phase."""
    
    def get_name(self) -> str:
        return "Nether Travel (Blazes & Gold)"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        """
        1. Enter Nether portal
        2. Find fortress
        3. Hunt blazes (10+ rods)
        """
        ready = resources.phase_ready_result(Phase.NETHER_TRAVEL, "Nether travel already satisfied")
        if ready:
            return ready

        # 1. Enter portal if in Overworld
        snapshot = client.transport.dispatch("get_state", {})
        dimension = snapshot.get("dimension", snapshot.get("world", {}).get("dimension", ""))
        if "overworld" in dimension.lower():
            print("Entering Nether...")
            if not enter_nether_portal(client):
                missing = resources.check_phase_requirements(Phase.NETHER_TRAVEL)
                return TaskResult.fail("Failed to enter Nether", missing=missing)
        
        # 2. Find Fortress
        print("Searching for Nether Fortress...")
        fortress_coords = find_nether_fortress(client)
        if not fortress_coords:
            return TaskResult.fail("Failed to find fortress", missing={"fortress": 1})
            
        # 3. Hunt Blazes
        print("Farming blaze rods...")
        rods = hunt_blazes(client, target_count=10)
        resources.refresh_inventory()
        missing = resources.check_phase_requirements(Phase.NETHER_TRAVEL)
        if rods < 10 or missing:
            return TaskResult.fail(
                f"Only gathered {rods} blaze rods",
                blaze_rods=rods,
                fortress=fortress_coords,
                missing=missing,
            )
            
        return TaskResult.ok(
            f"Collected {rods} blaze rods",
            blaze_rods=rods,
            fortress=fortress_coords,
            inventory=resources.get_summary()["inventory"],
        )
