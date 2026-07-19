"""
Base Construction Phase - Shelter, furnace, crafting table, bed, farm.
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import craft
from ...common.base import (
    find_flat_ground,
    build_dirt_shelter,
    build_good_house,
    setup_base,
    wait_for_safe_daylight,
)
from ...common.tasks import TaskResult, SequentialTask, ActionTask
from ...common.navigation import goto
from ...common.inventory import count_item, select_item
from ...common.automation_utils import get_player_pos
from ...common.combat import acquire_emergency_food, recover_health
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
                client.transport.dispatch("interact_block", {"x": fx + dx, "y": fy, "z": fz + dz})
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

        if not recover_health(client, minimum_health=12.0):
            if not acquire_emergency_food(client, minimum_health=12.0):
                return TaskResult.fail(
                    "Base construction blocked: health recovery or emergency food required"
                )

        # A base build starts with exposed gathering and many slow placement
        # operations.  Entering this phase at night caused the player to be
        # attacked while the builder was occupied, so make daylight a hard
        # precondition.  The helper returns immediately during the day.
        if not wait_for_safe_daylight(client):
            return TaskResult.fail(
                "Base construction blocked: could not establish safe daylight"
            )

        # Gather necessary materials if missing
        missing = resources.check_phase_requirements(Phase.BASE_CONSTRUCTION)
        if missing:
            ensure_result = resources.ensure_phase_supplies(client, Phase.BASE_CONSTRUCTION)
            if not ensure_result.success:
                return TaskResult.fail("Failed to gather materials for base construction", missing=missing)

        # Reuse an in-progress build origin after a failure or process restart.
        # Selecting a fresh flat site on every retry strands the previous shell
        # and consumes a complete second set of materials.
        saved_origin = state.custom_data.get("base_build_origin")
        if isinstance(saved_origin, (list, tuple)) and len(saved_origin) == 3:
            location = tuple(int(value) for value in saved_origin)
            print(f"  Resuming starter house at {location}")
        else:
            location = find_flat_ground(client, radius=24, footprint=7)
            if location is None:
                return TaskResult.fail("No suitable flat ground location found for base")
            state.custom_data["base_build_origin"] = list(location)
            px, py, pz = get_player_pos(client)
            state.update_position(px, py, pz)
            state.save_checkpoint(resources.get_summary()["inventory"])
            print(f"  Saved in-progress starter house origin {location}")

        x, y, z = location

        # Build or repair the starter house (plank walls, cobble floor, door).
        # Do not overlay a dirt fallback on the same footprint: a verified
        # partial shell is resumable, while the fallback corrupts its floor
        # and walls and makes the next retry harder.
        if not build_good_house(client, x, y, z):
            print("  Starter house build fell short; preserving it for repair retry...")
            state.save_checkpoint(resources.get_summary()["inventory"])
            return TaskResult.fail("Starter house was not completed; retry required")

        # Set up base infrastructure inside the house, on top of its floor
        success, _ = setup_base(client, (x, y + 1, z))
        if not success:
            return TaskResult.fail("Failed to set up base infrastructure")
            
        # Save base location for future phases (e.g. Iron Age smelting)
        state.custom_data["base_location"] = location
        state.custom_data.pop("base_build_origin", None)
        structures = state.custom_data.setdefault("structures", {})
        structures["starter_house"] = {
            "origin": list(location),
            "door": [x + 3, y + 1, z],
            "crafting_table": [x + 1, y + 1, z + 1],
            "furnace": [x + 2, y + 1, z + 1],
            "supply_chest": [x + 1, y + 1, z + 2],
            "bed": [x + 2, y + 1, z + 2],
        }
        px, py, pz = get_player_pos(client)
        state.update_position(px, py, pz)
        state.save_checkpoint(resources.get_summary()["inventory"])

        # Plant wheat farm (Optional)
        if not plant_wheat_farm(client, x, y, z):
            print("  Warning: Failed to plant wheat farm (Skipping)")
            # Do not fail phase, just proceed
        else:
            print("  Wheat farm planted successfully")

        # Success
        resources.refresh_inventory()
        summary = resources.get_summary()
        return TaskResult.ok("Base construction complete", inventory=summary["inventory"])
    
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
