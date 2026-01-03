"""
Stronghold Location Phase - Use eyes of ender to find stronghold.
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import triangulate_stronghold, spiral_stronghold_search, goto, find_end_portal
from ...common.tasks import TaskResult


class StrongholdHandler(PhaseHandler):
    """Handler for stronghold location phase."""

    def get_name(self) -> str:
        return "Stronghold Location"

    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        """Use eyes of ender to triangulate, navigate to stronghold, and locate end portal."""
        ready = resources.phase_ready_result(Phase.STRONGHOLD_LOCATE, "Stronghold requirements already satisfied")
        if ready:
            return ready

        snapshot = client.transport.dispatch("get_state", {})
        dimension = snapshot.get("dimension", snapshot.get("world", {}).get("dimension", ""))
        if "overworld" not in dimension.lower():
            print("Must be in Overworld to find stronghold. Returning...")
            return TaskResult.fail("Not in Overworld for stronghold hunt")
        position = snapshot.get("block_position", snapshot.get("position", {}))
        target_y = int(position.get("y", snapshot.get("y", 64)))

        print("Locating Stronghold using Eye of Ender triangulation...")
        coords = triangulate_stronghold(client)

        if coords:
            print(f"Stronghold estimated at: {coords}")
            print("Navigating to estimated stronghold area...")
            if goto(client, coords[0], target_y, coords[1], timeout=600):
                print("Reached stronghold area. Starting stronghold exploration...")
                # Start the stronghold exploration macro
                client.mission.macro("locate_stronghold", {})

                # Wait for the end portal to be found
                print("Exploring stronghold to find End Portal...")
                found = find_end_portal(client, timeout=900)  # Increased timeout for exploration

                if found:
                    resources.refresh_inventory()
                    missing = resources.check_phase_requirements(Phase.STRONGHOLD_LOCATE)
                    if missing:
                        return TaskResult.fail("End portal found but requirements unmet", coords=coords, missing=missing)
                    return TaskResult.ok(
                        "End portal located in stronghold",
                        coords=coords,
                        target_y=target_y,
                        portal_found=True,
                        inventory=resources.get_summary()["inventory"],
                    )
                else:
                    return TaskResult.fail(
                        "Reached stronghold but failed to locate end portal",
                        coords=coords,
                        target_y=target_y,
                    )
        else:
            # Triangulation failed, attempt spiral search
            print("Triangulation failed. Starting spiral search for stronghold...")
            spiral_coords = spiral_stronghold_search(client, max_radius=2000, step_size=200)

            if spiral_coords:
                print(f"Spiral search found potential stronghold at: {spiral_coords}")
                print("Navigating to spiral-found stronghold area...")
                if goto(client, spiral_coords[0], target_y, spiral_coords[1], timeout=600):
                    print("Reached spiral-found stronghold area. Starting stronghold exploration...")
                    # Start the stronghold exploration macro
                    client.mission.macro("locate_stronghold", {})

                    # Wait for the end portal to be found
                    print("Exploring stronghold to find End Portal...")
                    found = find_end_portal(client, timeout=900)

                    if found:
                        resources.refresh_inventory()
                        missing = resources.check_phase_requirements(Phase.STRONGHOLD_LOCATE)
                        if missing:
                            return TaskResult.fail("End portal found but requirements unmet", coords=spiral_coords, missing=missing)
                        return TaskResult.ok(
                            "End portal located via spiral search",
                            coords=spiral_coords,
                            target_y=target_y,
                            portal_found=True,
                            inventory=resources.get_summary()["inventory"],
                        )
                    else:
                        return TaskResult.fail(
                            "Reached spiral-found stronghold but failed to locate end portal",
                            coords=spiral_coords,
                            target_y=target_y,
                        )
                else:
                    return TaskResult.fail(
                        "Failed to navigate to spiral-found stronghold location",
                        coords=spiral_coords,
                    )
            else:
                return TaskResult.fail("Spiral search also failed to locate stronghold")

        return TaskResult.fail("Failed to locate stronghold")
