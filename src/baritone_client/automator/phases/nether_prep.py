"""
Phase 4: Nether Logic
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import StateManager
from ...common import TaskResult, SequentialTask, ActionTask
from ...common.resources import gather_ores
from ...common.nether import build_nether_portal, enter_nether_portal, find_nether_fortress, hunt_blazes, barter_with_piglins

class NetherAndBlazeHandler(PhaseHandler):
    """Phase 4: Nether exploration - Hour 3-4."""
    
    def get_name(self) -> str:
        return "Nether Phase (Hour 3-4)"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        tasks = [
            ActionTask("Build lava-cast portal", self._lava_cast_portal),
            ActionTask("Enter Nether", lambda c: enter_nether_portal(c)),
            ActionTask("Mine nether gold", self._mine_nether_gold),
            ActionTask("Barter with piglins", lambda c: barter_with_piglins(c, gold_count=20)),
            ActionTask("Locate fortress", lambda c: find_nether_fortress(c)),
            ActionTask("Kill blazes -> 6+ rods", lambda c: hunt_blazes(c, target_rods=6)),
            ActionTask("Collect quartz, soul sand, glowstone", self._collect_nether_resources),
            ActionTask("Return to Overworld", self._return_to_overworld),
        ]
        
        executor = SequentialTask("Nether Phase", tasks)
        return executor.run(client)

    def _lava_cast_portal(self, client) -> bool:
        """Build portal using lava casting method."""
        # Get current position
        state = client.transport.dispatch("get_state", {})
        pos = state.get("block_position", state.get("position", {}))
        px = int(pos.get("x", 0))
        py = int(pos.get("y", 64))
        pz = int(pos.get("z", 0))
        
        # Build portal 3 blocks ahead of player
        # A real implementation might be smarter about placement, but this fixes the crash
        return build_nether_portal(client, px + 3, py, pz)

    def _mine_nether_gold(self, client) -> bool:
        """Mine nether gold ore for piglin bartering."""
        return gather_ores(client, "nether_gold", count=20, timeout=300)

    def _collect_nether_resources(self, client) -> bool:
        """Collect quartz, soul sand, glowstone."""
        gather_ores(client, "quartz", count=32, timeout=180)
        # TODO: Collect soul sand and glowstone
        return True

    def _return_to_overworld(self, client) -> bool:
        """Return through portal to Overworld."""
        return enter_nether_portal(client, timeout=60)
