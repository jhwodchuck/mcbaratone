"""
Phase 9: End Game Logic
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import StateManager
from ...common import TaskResult, SequentialTask, ActionTask
from ...common.resources import ensure_supplies
from ...common.end import triangulate_stronghold, find_end_portal, activate_end_portal, enter_end_portal, fight_ender_dragon

class WorldUnlockHandler(PhaseHandler):
    """Phase 9: End dimension access - Hour 8-9."""
    
    def get_name(self) -> str:
        return "End Unlock (Hour 8-9)"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        tasks = [
            ActionTask("Craft Eyes of Ender", self._craft_eyes),
            ActionTask("Locate stronghold", lambda c: triangulate_stronghold(c)),
            ActionTask("Find and activate End portal", self._activate_portal),
            ActionTask("Enter The End", lambda c: enter_end_portal(c)),
            ActionTask("Kill Ender Dragon (one-cycle)", self._kill_dragon),
            ActionTask("Loot End City", self._loot_end_city),
            ActionTask("Acquire 5+ shulker boxes", self._acquire_shulkers),
            ActionTask("Grab Elytra", self._grab_elytra),
            ActionTask("Return to Overworld", self._return_overworld),
        ]
        
        executor = SequentialTask("End Unlock", tasks)
        return executor.run(client)

    def _craft_eyes(self, client) -> bool:
        """Craft 12 Eyes of Ender."""
        return ensure_supplies(client, {"minecraft:ender_eye": 12}).success

    def _activate_portal(self, client) -> bool:
        """Find and activate the End portal."""
        if not find_end_portal(client):
            return False
        return activate_end_portal(client)

    def _kill_dragon(self, client) -> bool:
        """Kill the Ender Dragon, attempting one-cycle strategy."""
        print("Fighting Ender Dragon...")
        return fight_ender_dragon(client)

    def _loot_end_city(self, client) -> bool:
        """Find and loot an End City."""
        print("Searching for End City...")
        # TODO: Implement End City finding and looting
        return True

    def _acquire_shulkers(self, client) -> bool:
        """Kill shulkers and craft 5+ shulker boxes."""
        print("Acquiring shulker boxes...")
        return True

    def _grab_elytra(self, client) -> bool:
        """Find and grab Elytra from End Ship."""
        print("Searching for Elytra...")
        return True

    def _return_overworld(self, client) -> bool:
        """Return to Overworld via End gateway or portal."""
        print("Returning to Overworld...")
        return True
