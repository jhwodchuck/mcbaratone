"""
Base Construction Phase - Shelter, furnace, crafting table, bed, farm.
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import craft
from ...common.base import find_flat_ground, build_dirt_shelter, setup_base
from ...common.tasks import TaskResult, SequentialTask, ActionTask
from ...common.navigation import goto
from ...common.inventory import count_item, select_item
import time


def plant_wheat_farm(client, x: int, y: int, z: int, size: int = 5) -> bool:
    """
    Plant a wheat farm near the base location.
    
    Args:
        client: Baritone client
        x, y, z: Base location
        size: Farm size (square)
        
    Returns:
        True if farm planted
    """
    # Find suitable farmland nearby (dirt/grass)
    from ...common.navigation import find_nearby_block
    
    farm_spot = find_nearby_block(client, ["minecraft:dirt", "minecraft:grass_block"], radius=20)
    if farm_spot is None:
        print("  No suitable farmland found nearby")
        return False
    
    fx, fy, fz = farm_spot
    
    # Go to farm location
    if not goto(client, fx, fy, fz, timeout=30, tolerance=2):
        print("  Could not reach farm location")
        return False
    
    # Ensure we have wheat seeds
    seeds_needed = size * size
    if count_item(client, "minecraft:wheat_seeds") < seeds_needed:
        # Try to get seeds from grass (destroy grass blocks)
        print("  Gathering wheat seeds...")
        client.transport.dispatch("mine", {"blocks": ["minecraft:grass", "minecraft:tall_grass"], "quantity": seeds_needed // 2})
        time.sleep(5)
        client.transport.dispatch("cancel", {})
    
    if count_item(client, "minecraft:wheat_seeds") < seeds_needed:
        print(f"  Need {seeds_needed} wheat seeds, have {count_item(client, 'minecraft:wheat_seeds')}")
        return False
    
    # Till the soil and plant
    if not select_item(client, "minecraft:wheat_seeds"):
        return False
    
    planted = 0
    for dx in range(size):
        for dz in range(size):
            # Till soil first (hoe needed, assume wooden hoe or craft one)
            if count_item(client, "minecraft:wooden_hoe") == 0:
                if count_item(client, "minecraft:oak_planks") >= 2 and count_item(client, "minecraft:stick") >= 2:
                    craft(client, "minecraft:wooden_hoe", 1)
            
            if select_item(client, "minecraft:wooden_hoe"):
                # Right-click to till
                client.transport.dispatch("interact_block", {"x": fx + dx, "y": fy, "z": fz + dz})
                time.sleep(0.2)
            
            # Plant seed
            if select_item(client, "minecraft:wheat_seeds"):
                client.transport.dispatch("place_block", {"x": fx + dx, "y": fy + 1, "z": fz + dz})
                planted += 1
                time.sleep(0.2)
    
    print(f"  Planted {planted} wheat crops")
    return planted > 0


class BaseConstructionHandler(PhaseHandler):
    """Handler for base construction phase using common library functions."""
    
    def get_name(self) -> str:
        return "Base Construction"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        """
        Execute base construction following progression:
        1. Find flat ground location
        2. Build basic shelter
        3. Establish base infrastructure (crafting table, furnace, chest, bed)
        4. Plant wheat farm
        """
        ready = resources.phase_ready_result(Phase.BASE_CONSTRUCTION, "Base construction already satisfied")
        if ready:
            return ready

        # Define subtasks
        tasks = [
            ActionTask("Find flat ground location", self._find_location),
            ActionTask("Build basic shelter", self._build_shelter),
            ActionTask("Establish base infrastructure", self._setup_infrastructure),
            ActionTask("Plant wheat farm", self._plant_farm),
        ]
        
        # Execute sequentially
        sequential_task = SequentialTask("Base Construction", tasks)
        result = sequential_task.run(client)
        
        if result.success:
            resources.refresh_inventory()
            summary = resources.get_summary()
            return TaskResult.ok("Base construction complete", inventory=summary["inventory"])
        else:
            missing = resources.check_phase_requirements(Phase.BASE_CONSTRUCTION)
            return TaskResult.fail(f"Base construction failed: {result.reason}", missing=missing)
    
    def _find_location(self, client) -> bool:
        """Find a flat ground location for the base."""
        location = find_flat_ground(client)
        if location is None:
            return False
        # Store location in state for other tasks
        state = client._state if hasattr(client, '_state') else {}  # Assuming client has state
        # Actually, better to return the location or use a shared context
        # For now, just return True if found
        return location is not None
    
    def _build_shelter(self, client) -> bool:
        """Build a basic dirt shelter."""
        location = find_flat_ground(client)
        if location is None:
            return False
        x, y, z = location
        return build_dirt_shelter(client, x, y, z)
    
    def _setup_infrastructure(self, client) -> bool:
        """Set up base infrastructure with crafting table, furnace, chest, bed."""
        location = find_flat_ground(client)
        if location is None:
            return False
        success, _ = setup_base(client, location)
        return success
    
    def _plant_farm(self, client) -> bool:
        """Plant a wheat farm near the base."""
        location = find_flat_ground(client)
        if location is None:
            return False
        x, y, z = location
        return plant_wheat_farm(client, x, y, z)
