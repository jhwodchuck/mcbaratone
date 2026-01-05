"""
Phase 3: Enchanting Phase
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import StateManager
from ...common import TaskResult, SequentialTask, ActionTask
from ...common.resources import ensure_supplies
from ...common.inventory import count_item
from ...common.combat import hunt_passive_mobs

class EnchantingPipelineHandler(PhaseHandler):
    """Phase 3: Enchanting setup - Hour 2-3."""
    
    def get_name(self) -> str:
        return "Enchanting Core (Hour 2-3)"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        tasks = [
            ActionTask("Gather 45 leather", self._gather_leather),
            ActionTask("Harvest sugarcane -> 45 paper", self._harvest_sugarcane),
            ActionTask("Craft enchanting table", lambda c: ensure_supplies(c, {"minecraft:enchanting_table": 1}).success),
            ActionTask("Craft 15 bookshelves", lambda c: ensure_supplies(c, {"minecraft:bookshelf": 15}).success),
            ActionTask("Build enchanting room", self._build_enchanting_room),
            ActionTask("Begin rolling enchants", self._roll_enchants),
        ]
        
        executor = SequentialTask("Enchanting Core", tasks)
        return executor.run(client)

    def _gather_leather(self, client) -> bool:
        """Hunt cows for 45 leather."""
        if count_item(client, "minecraft:leather") >= 45:
            print("  Already have 45 leather.")
            return True
            
        hunt_passive_mobs(client, target_count=45)
        return count_item(client, "minecraft:leather") >= 45

    def _harvest_sugarcane(self, client) -> bool:
        """Harvest sugarcane and craft into 45 paper."""
        # TODO: Find and harvest sugarcane
        return ensure_supplies(client, {"minecraft:paper": 45}).success

    def _build_enchanting_room(self, client) -> bool:
        """Place enchanting table surrounded by bookshelves."""
        # TODO: Implement room construction
        return True

    def _roll_enchants(self, client) -> bool:
        """Begin enchanting primary tools."""
        # TODO: Implement enchanting logic
        return True
