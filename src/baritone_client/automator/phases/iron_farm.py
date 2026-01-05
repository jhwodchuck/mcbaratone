"""
Phase 7: Iron Farm Logic
"""

from typing import Tuple
from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import TaskResult, SequentialTask, ActionTask

class IronFarmHandler(PhaseHandler):
    """Phase 7: Iron farm construction - Hour 6-7."""
    
    def get_name(self) -> str:
        return "Iron Farm (Hour 6-7)"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        # Set farm location in state
        pos_response = client.transport.dispatch("get_state", {})
        if 'error' in pos_response:
            return TaskResult.fail("Could not get player position")

        pos = pos_response.get('position', [0, 64, 0])
        # Handle position as dict or list
        if isinstance(pos, list) and len(pos) >= 3:
             center_x, center_y, center_z = int(pos[0]), int(pos[1]), int(pos[2])
        else:
             center_x = int(pos.get('x', 0))
             center_y = int(pos.get('y', 64))
             center_z = int(pos.get('z', 0))
             
        farm_location = (center_x + 40, center_y, center_z + 40)  # Offset from current position

        state.record_phase_payload(Phase.IRON_FARM, {"farm_location": farm_location})

        tasks = [
            ActionTask("Move 3 villagers to farm location", lambda c: self._move_villagers(c, farm_location)),
            ActionTask("Add zombie to farm", lambda c: self._add_zombie(c, farm_location)),
            ActionTask("Construct iron farm", lambda c: self._build_iron_farm(c, farm_location)),
            ActionTask("Start iron production", lambda c: self._start_production(c, farm_location)),
        ]

        executor = SequentialTask("Iron Farm", tasks)
        result = executor.run(client)

        if result.success:
            state.record_phase_payload(Phase.IRON_FARM, {
                "farm_location": farm_location,
                "villagers_moved": True,
                "zombie_added": True,
                "farm_constructed": True,
                "production_started": True
            })

        return result

    def _move_villagers(self, client, farm_location: Tuple[int, int, int]) -> bool:
        """Transport 3 villagers to the iron farm location."""
        # TODO: Implement villager transport logic
        print(f"Moving 3 villagers to {farm_location}...")
        return True

    def _add_zombie(self, client, farm_location: Tuple[int, int, int]) -> bool:
        """Capture and add a zombie to scare villagers."""
        # TODO: Implement zombie capture logic
        print(f"Adding zombie to farm at {farm_location}...")
        return True

    def _build_iron_farm(self, client, farm_location: Tuple[int, int, int]) -> bool:
        """Construct the iron farm structure."""
        # TODO: Implement iron farm construction
        x, y, z = farm_location
        print(f"Building iron farm at ({x}, {y}, {z})...")
        return True

    def _start_production(self, client, farm_location: Tuple[int, int, int]) -> bool:
        """Verify iron golems are spawning."""
        # TODO: Implement production verification
        print(f"Verifying iron production at {farm_location}...")
        return True
