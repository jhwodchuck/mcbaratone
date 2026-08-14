"""
Base Construction Phase - Shelter, furnace, crafting table, bed, farm.
"""

from typing import Optional

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
from ...common.tasks import (
    ActionTask,
    ProgressRecoveryRequired,
    SequentialTask,
    SurvivalRecoveryRequired,
    TaskResult,
)
from ...common.navigation import goto
from ...common.inventory import count_item, select_item
from ...common.resources import _wait_for_path_completion
from ...common.automation_utils import get_player_pos
from ...common.site_selection import surface_y_at
from ...common.combat import acquire_emergency_food, eat_until_hunger, recover_health
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

    @staticmethod
    def _recovery_center(state: StateManager) -> Optional[tuple[float, float]]:
        """Anchor emergency searches to the persisted build site."""
        origin = state.custom_data.get("base_build_origin")
        if isinstance(origin, (list, tuple)) and len(origin) == 3:
            return (float(origin[0]), float(origin[2]))
        return None
    
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
            if not acquire_emergency_food(
                client,
                minimum_health=12.0,
                exploration_center=self._recovery_center(state),
            ):
                self._raise_after_survival_failure(
                    client,
                    state=state,
                    inventory_summary=resources.get_summary()["inventory"],
                    reason="base construction health remains below 12 after bounded recovery",
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
        location = self._resolve_build_location(client, resources, state)
        if location is None:
            return TaskResult.fail("No suitable flat ground location found for base")

        x, y, z = location

        # Death recovery may deliberately secure a grave locally when the
        # house is hundreds of blocks away. Resume by making one explicit
        # trip back to the persisted site; otherwise the builder attempts
        # every floor/wall coordinate from the remote grave and burns minutes
        # of failed movement without changing the house.
        if not self._stage_at_build_site(client, x, y, z):
            return self._record_unreachable_build_site(
                state,
                resources.get_summary()["inventory"],
            )
        state.custom_data.pop("base_site_return_failures", None)

        # Build or repair the starter house (plank walls, cobble floor, door).
        # Do not overlay a dirt fallback on the same footprint: a verified
        # partial shell is resumable, while the fallback corrupts its floor
        # and walls and makes the next retry harder.
        repair_attempt = int(state.custom_data.get("base_construction_repair_attempts", 0))
        house_built = False
        base_setup_complete = False
        if repair_attempt >= 3:
            existing_progress = house_utils.summarize_house_progress(client, x, y, z)
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
            # Building and its supporting wood gathering deliberately stop at
            # low hunger. That is a survival hold, not evidence that the
            # resumable shell is defective, so recover food (bounded) and
            # yield without consuming a repair/objective attempt if recovery
            # cannot restore a working margin.
            self._recover_build_survival_or_yield(
                client,
                state=state,
                inventory_summary=resources.get_summary()["inventory"],
                recovery_center=(x, z),
            )
            repair_attempt += 1
            state.custom_data["base_construction_repair_attempts"] = repair_attempt
            house_progress = house_utils.summarize_house_progress(client, x, y, z)
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
            elif repair_attempt >= 5:
                # Durable storage and workstations are the progression boundary.
                # After five failed shell repairs, establish those inside the
                # surviving footprint instead of spending hours rebuilding.
                print(
                    "  Full starter house exceeded repair budget; "
                    "establishing functional infrastructure in the partial shell."
                )
                base_setup_complete, _ = setup_base(client, (x, y + 1, z))
                if not base_setup_complete:
                    state.save_checkpoint(resources.get_summary()["inventory"])
                    return TaskResult.fail(
                        "Starter house repair budget exhausted and functional base setup failed"
                    )
                state.custom_data["base_construction_degraded"] = {
                    "repair_attempts": repair_attempt,
                    "progress": house_progress,
                }
                house_built = True
            else:
                print("  Starter house build fell short; preserving it for repair retry...")
                state.save_checkpoint(resources.get_summary()["inventory"])
                return TaskResult.fail("Starter house was not completed; retry required")
        state.custom_data.pop("base_construction_repair_attempts", None)
        if not house_built:
            return TaskResult.fail("Starter house was not completed; retry required")

        # Set up base infrastructure inside the house, on top of its floor
        if not base_setup_complete:
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

    @staticmethod
    def _record_unreachable_build_site(
        state: StateManager,
        inventory_summary: dict,
    ) -> TaskResult:
        """Persist a return failure and retire a repeatedly unreachable site."""
        return_failures = int(
            state.custom_data.get("base_site_return_failures", 0)
        ) + 1
        state.custom_data["base_site_return_failures"] = return_failures
        repair_attempts = int(
            state.custom_data.get("base_construction_repair_attempts", 0)
        )
        if repair_attempts >= 3 or return_failures >= 2:
            # Preserve the blocks in-world; only retire the stale checkpoint
            # pointer so the next attempt can select a reachable surface site.
            state.custom_data.pop("base_build_origin", None)
            state.custom_data.pop("base_construction_repair_attempts", None)
            state.custom_data.pop("base_site_return_failures", None)
            print(
                "  Persisted starter-house site remained unreachable "
                f"(returns={return_failures}, repairs={repair_attempts}); "
                "preserving the old shell and relocating on the next attempt."
            )
        state.save_checkpoint(inventory_summary)
        return TaskResult.fail(
            "Could not return to the persisted starter-house build site"
        )

    @staticmethod
    def _resolve_build_location(
        client,
        resources: ResourceManager,
        state: StateManager,
    ) -> Optional[tuple[int, int, int]]:
        """Reuse a saved origin or relocate until a new footprint is visible."""
        saved_origin = state.custom_data.get("base_build_origin")
        if isinstance(saved_origin, (list, tuple)) and len(saved_origin) == 3:
            location = tuple(int(value) for value in saved_origin)
            from ...common.build_site_recovery import (
                unprovisioned_remote_build_site,
            )

            inventory = resources.get_summary()["inventory"]
            if not unprovisioned_remote_build_site(
                client,
                site=location,
                inventory_summary=inventory,
            ):
                print(f"  Resuming starter house at {location}")
                return location
            state.custom_data.pop("base_build_origin", None)
            state.custom_data.pop("base_construction_repair_attempts", None)
            state.custom_data.pop("base_site_return_failures", None)
            state.save_checkpoint(inventory)
            print(
                "  Persisted starter house is too remote to return to without "
                "carried food; preserving that shell and selecting a local site."
            )

        location = find_flat_ground(client, radius=24, footprint=7)
        if location is None:
            from ...common.build_site_recovery import relocate_build_site_search

            px, py, pz = get_player_pos(client)
            expected_surface = surface_y_at(client, int(px), int(pz))
            minimum_y = (
                int(expected_surface) - 2
                if expected_surface is not None and py < expected_surface - 3
                else None
            )
            search_attempt = int(
                state.custom_data.get("base_site_search_attempts", 0)
            ) + 1
            state.custom_data["base_site_search_attempts"] = search_attempt
            if relocate_build_site_search(
                client,
                attempt=search_attempt,
                goto=goto,
                minimum_y=minimum_y,
            ):
                location = find_flat_ground(client, radius=24, footprint=7)
            if location is None:
                state.save_checkpoint(resources.get_summary()["inventory"])
                raise ProgressRecoveryRequired(
                    "base site search could not reach a new dry-surface view "
                    f"(attempt={search_attempt}, minimum_y={minimum_y})"
                )

        state.custom_data.pop("base_site_search_attempts", None)
        state.custom_data["base_build_origin"] = list(location)
        px, py, pz = get_player_pos(client)
        state.update_position(px, py, pz)
        state.save_checkpoint(resources.get_summary()["inventory"])
        print(f"  Saved in-progress starter house origin {location}")
        return location

    @staticmethod
    def _within_staging_band(client, x: int, y: int, z: int):
        """Return ``(staged, py)`` for the current position vs the house band.

        The builder only needs to be close enough to place the shell, so the
        acceptance band is deliberately loose (16m horizontal, 8m vertical of
        the staging point). ``staged`` is ``None`` when live state cannot be
        read, ``py`` is the current Y (or ``None``).
        """
        transport = getattr(client, "transport", None)
        if transport is None:
            return True, None
        try:
            state = transport.dispatch("get_state", {})
        except Exception:
            return None, None
        if not isinstance(state, dict):
            return None, None
        nested = state.get("data")
        if isinstance(nested, dict):
            state = {**state, **nested}
        position = state.get("block_position", state.get("position", {}))
        px = float(position.get("x", state.get("x", x)) or x)
        py = float(position.get("y", state.get("y", y + 1)) or (y + 1))
        pz = float(position.get("z", state.get("z", z)) or z)
        horizontal = ((px - (x + 3)) ** 2 + (pz - (z - 2)) ** 2) ** 0.5
        return (horizontal <= 16.0 and abs(py - (y + 1)) <= 8.0), py

    @classmethod
    def _stage_at_build_site(cls, client, x: int, y: int, z: int) -> bool:
        """Reach one safe exterior staging point before per-block placement."""
        transport = getattr(client, "transport", None)
        if transport is None:
            return True
        # House placement temporarily disables breaking so Baritone cannot
        # tunnel through the shell.  If that controller is externally stopped,
        # the client-side setting survives the Python process.  Always restore
        # the travel policy before a recovery/surface route.
        for command in (
            "#set allowBreak true",
            "#set allowPlace true",
            "#set allowDownward false",
        ):
            transport.dispatch("chat", {"message": command})
        staged, py = cls._within_staging_band(client, x, y, z)
        if staged is None:
            return False
        if staged:
            return True
        target = (x + 3, y + 1, z - 2)
        if py is not None and py < y - 8:
            print(
                f"  Recovery position is {y - py:.0f} blocks below the house; "
                "surfacing before the return trip..."
            )
            transport.dispatch("chat", {"message": "#surface"})
            _wait_for_path_completion(client, timeout=120.0)
            # Surfacing alone can land us inside the band; re-check before a
            # full return trip rather than assuming we still need to travel.
            staged, _ = cls._within_staging_band(client, x, y, z)
            if staged:
                return True
        print(
            "  Returning from recovery position to persisted house staging "
            f"point {target}..."
        )
        if goto(client, target[0], target[1], target[2], timeout=300, tolerance=4.0):
            return True
        # Baritone can stop short of the strict 4m goal (e.g. it cannot stand
        # on the exact staging block) yet leave us close enough to build. Trust
        # the loose staging band, not goto's tight tolerance -- confirmed live:
        # Bot08 looped here failing a 6.4m return it had effectively already
        # completed.
        staged, _ = cls._within_staging_band(client, x, y, z)
        return bool(staged)

    @classmethod
    def _recover_build_survival_or_yield(
        cls,
        client,
        minimum_food: int = 12,
        recovery_center: Optional[tuple[float, float]] = None,
        state: Optional[StateManager] = None,
        inventory_summary: Optional[dict] = None,
    ) -> None:
        """Restore building health/hunger or yield without charging a retry."""
        transport = getattr(client, "transport", None)
        if transport is None:
            return
        try:
            live = transport.dispatch("get_state", {})
        except Exception:
            return
        if not isinstance(live, dict):
            return
        nested = live.get("data")
        if isinstance(nested, dict):
            live = {**live, **nested}
        health = float(live.get("health", 20) or 0)
        food = int(live.get("food_level", live.get("food", 20)) or 0)
        if health >= 12.0 and food >= minimum_food:
            return

        print(
            f"  Building stopped at health={health:.1f}/20 food={food}/20; "
            "attempting bounded survival recovery before charging a repair attempt..."
        )
        recovered = health >= 12.0 and eat_until_hunger(
            client, minimum_food=minimum_food
        )
        if health < 12.0:
            recovered = recover_health(client, minimum_health=12.0, timeout=10.0)
        if not recovered:
            recovered = acquire_emergency_food(
                client,
                minimum_health=12.0,
                minimum_food=minimum_food,
                timeout=120.0,
                max_exploration_distance=128.0,
                exploration_center=recovery_center,
            )
        if recovered:
            if state is not None:
                state.custom_data.pop("base_survival_recovery_failures", None)
            return
        cls._raise_after_survival_failure(
            client,
            state=state,
            inventory_summary=inventory_summary,
            reason=(
                "base construction health or hunger remains below its working "
                "margin after bounded recovery"
            ),
        )

    @staticmethod
    def _raise_after_survival_failure(
        client,
        *,
        state: Optional[StateManager],
        inventory_summary: Optional[dict],
        reason: str,
    ) -> None:
        """Persist repeated recovery failures and leave an unsafe build area."""
        if state is None:
            raise SurvivalRecoveryRequired(reason)
        failures = int(
            state.custom_data.get("base_survival_recovery_failures", 0)
        ) + 1
        state.custom_data["base_survival_recovery_failures"] = failures
        if failures >= 3:
            from ...common.build_site_recovery import relocate_build_site_search

            if relocate_build_site_search(
                client,
                attempt=failures,
                goto=goto,
                attempt_limit=2,
            ):
                state.custom_data.pop("base_build_origin", None)
                state.custom_data.pop("base_construction_repair_attempts", None)
                state.custom_data.pop("base_site_return_failures", None)
                state.custom_data.pop("base_survival_recovery_failures", None)
                print(
                    "  Repeated survival recovery failed at this build origin; "
                    "relocated to safer terrain and retired the unsafe site."
                )
        state.save_checkpoint(inventory_summary or {})
        raise SurvivalRecoveryRequired(reason)

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
