"""
Dragon Fight Phase - Defeat the Ender Dragon.
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import StateManager
from ...common.end import fight_ender_dragon
from ...common.tasks import TaskResult


class DragonFightHandler(PhaseHandler):
    """Handler for dragon fight phase."""
    
    def get_name(self) -> str:
        return "Ender Dragon Fight"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        """Run the retired phase entrypoint through the shared controller."""
        print("Initiating final battle with the Ender Dragon...")

        # Ensure we are in The End
        snapshot = client.transport.dispatch("get_state", {})
        dimension = snapshot.get("dimension", snapshot.get("world", {}).get("dimension", ""))
        if "the_end" not in dimension.lower():
            return TaskResult.fail("Must be in The End to fight the dragon.")

        # Start the dragon fight logic
        if self._fight_dragon(client):
            resources.refresh_inventory()
            return TaskResult.ok(
                "Ender Dragon defeated",
                inventory=resources.get_summary()["inventory"],
            )
        
        return TaskResult.fail("Dragon fight failed")

    def _fight_dragon(self, client, timeout: int = 1200) -> bool:
        """Delegate legacy callers to the production shared boss controller."""
        return fight_ender_dragon(client, timeout=timeout)
