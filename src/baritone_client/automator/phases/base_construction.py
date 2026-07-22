"""
Base Construction Phase - Shelter, furnace, crafting table, bed, farm.
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import craft
from ...common import base as house_utils
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
from ...common.farming import establish_wheat_farm
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
        repair_attempt = int(state.custom_data.get("base_construction_repair_attempts", 0))
        house_built = False
        if repair_attempt >= 3:
            existing_progress = self._summarize_starter_house_progress(client, x, y, z)
            if self._should_continue_from_recovered_house(
                existing_progress, repair_attempt
            ):
                print(
                    "  Existing starter shell is already recoverable; "
                    "skipping another material-gather/build loop."
                )
                house_built = self._ensure_starter_house_entryway(client, x, y, z)

        if not house_built:
            house_built = build_good_house(client, x, y, z)
        if not house_built:
            repair_attempt += 1
            state.custom_data["base_construction_repair_attempts"] = repair_attempt
            house_progress = self._summarize_starter_house_progress(client, x, y, z)
            print(
                "  Starter house build status: "
                f"floor={house_progress['floor']}/{house_progress['floor_total']} "
                f"shell={house_progress['shell']}/{house_progress['shell_total']} "
                f"roof={house_progress['roof']}/{house_progress['roof_total']} "
                f"door={'yes' if house_progress['door_present'] else 'no'} "
                f"attempt={repair_attempt}"
            )
            if self._should_continue_from_recovered_house(house_progress, repair_attempt):
                print("  Starter house is recoverable; finalizing base build from partial shell.")
                if not self._ensure_starter_house_entryway(client, x, y, z):
                    state.save_checkpoint(resources.get_summary()["inventory"])
                    return TaskResult.fail(
                        "Starter house was not completed and recovery could not place an entryway"
                    )
                house_built = True
            else:
                print("  Starter house build fell short; preserving it for repair retry...")
                state.save_checkpoint(resources.get_summary()["inventory"])
                return TaskResult.fail("Starter house was not completed; retry required")
        state.custom_data.pop("base_construction_repair_attempts", None)
        if not house_built:
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

        # Sleep in the new bed at the first opportunity so a death anywhere
        # in a later phase respawns near base instead of at world spawn. See
        # wait_and_establish_respawn_anchor's docstring for the confirmed
        # live failure this prevents.
        if house_utils.wait_and_establish_respawn_anchor(client, state):
            print("  Respawn anchor established at the starter bed.")
        else:
            print("  Could not establish a respawn anchor yet; continuing anyway.")

        # Establish an irrigated wheat farm nearby (Optional). This is the
        # renewable food/breeding-item supply for bases in animal-sparse
        # biomes where hunting alone can never restock food or leather --
        # find_flat_ground searches from the player's current position
        # rather than assuming a fixed offset from the house, since terrain
        # near the house is not guaranteed flat at the house's exact Y level.
        farm_spot = find_flat_ground(client, radius=20, footprint=5)
        if farm_spot is None:
            print("  No flat ground found for a wheat farm; skipping.")
        else:
            fx, fy, fz = farm_spot
            farm_origin = establish_wheat_farm(client, fx, fy, fz, size=5)
            if farm_origin is None:
                print("  Warning: Failed to establish wheat farm (Skipping)")
            else:
                state.custom_data["wheat_farm"] = {"origin": list(farm_origin)}
                print(f"  Wheat farm established at {farm_origin}")

        # Success
        resources.refresh_inventory()
        summary = resources.get_summary()
        return TaskResult.ok("Base construction complete", inventory=summary["inventory"])

    def _summarize_starter_house_progress(self, client, x: int, y: int, z: int) -> dict[str, int | bool]:
        """Return current starter-house completion state by block role."""
        # A reconnect can land the player back at base before the chunk
        # finishes streaming in; querying it too early misreads an intact
        # or partial house as void_air ("missing"), wasting a rebuild
        # attempt. Give it a moment to load first.
        house_utils.wait_for_chunk_loaded(client, x, y, z)
        plan = house_utils._good_house_plan(x, y, z)
        floor_total = 0
        floor_ok = 0
        shell_total = 0
        shell_ok = 0
        roof_total = 0
        roof_ok = 0

        for tx, ty, tz, role in plan:
            role_id = house_utils._house_block_id(client, tx, ty, tz)
            if role == "floor":
                floor_total += 1
                floor_ok += int(house_utils._matches_house_role(role_id, "floor"))
            elif role == "shell":
                shell_total += 1
                shell_ok += int(house_utils._matches_house_role(role_id, "shell"))
            elif role == "roof":
                roof_total += 1
                roof_ok += int(house_utils._matches_house_role(role_id, "roof"))

        door_x = x + 3
        door_present = house_utils._house_door_aligned(client, door_x, y + 1, z)
        return {
            "floor": floor_ok,
            "shell": shell_ok,
            "roof": roof_ok,
            "floor_total": floor_total,
            "shell_total": shell_total,
            "roof_total": roof_total,
            "door_present": door_present,
        }

    @staticmethod
    def _should_continue_from_recovered_house(progress: dict[str, int | bool], attempt: int) -> bool:
        """Permit forward progress when reconnect recovery already built most of the house."""
        if attempt <= 1:
            return (
                progress["floor"] >= 45
                and progress["shell"] >= 63
                and progress["roof"] >= 20
                and progress["door_present"]
            )

        # On a retry after a reconnect, allow completion even when the final roof
        # layer is not yet in place, but require a largely intact footprint.
        # Very old partial builds can have floor gaps below otherwise complete
        # walls/roof (for example after the player reconnects underneath it).
        # At that point the shell is safe enough for setup_base to establish the
        # functional interior and stop rebuilding the same house indefinitely.
        if attempt >= 3:
            return (
                progress["floor"] >= 30
                and progress["shell"] >= 63
                and progress["roof"] >= 35
            )
        return (
            progress["floor"] >= 45
            and progress["shell"] >= 56
            and (progress["door_present"] or progress["roof"] >= 10)
        )

    def _ensure_starter_house_entryway(self, client, x: int, y: int, z: int) -> bool:
        """Restore missing starter-house door if possible before proceeding."""
        if house_utils._house_door_aligned(client, x + 3, y + 1, z):
            return True

        door_x, door_y, door_z = x + 3, y + 1, z
        door_item = house_utils.first_available_item(client, house_utils._ALL_DOORS)
        if door_item is None:
            if sum(
                count_item(client, item_id) for item_id in house_utils._ALL_PLANKS
            ) >= 6:
                # Recreate the original crafting order to avoid introducing
                # wrong-wood door types that can block the north-facing door.
                plank_counts = sorted(
                    (
                        count_item(client, item_id),
                        item_id,
                    )
                    for item_id in house_utils._ALL_PLANKS
                )
                plank_count, planks = plank_counts[-1]
                if planks and plank_count >= 6:
                    wood = planks.split(":")[-1].replace("_planks", "")
                    if not craft(client, f"minecraft:{wood}_door", 1):
                        craft(client, "minecraft:oak_door", 1)
                door_item = house_utils.first_available_item(client, house_utils._ALL_DOORS)

        if door_item is None:
            return False
        return house_utils._place_north_wall_door(client, door_x, door_y, door_z, door_item)
    
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
