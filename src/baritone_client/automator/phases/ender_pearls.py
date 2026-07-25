"""
Ender Pearl Farm Phase - Hunt endermen for pearls.
"""

import time

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import hunt_endermen, craft_eyes_of_ender
from ...common.tasks import TaskResult


class EnderPearlHandler(PhaseHandler):
    """Handler for ender pearl farming phase."""
    
    def get_name(self) -> str:
        return "Ender Pearl Farming"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        """
        Enhanced Enderman farming with optimized spawn location detection and mob luring.

        Process:
        1. Return to Overworld if not already there
        2. Hunt endermen using enhanced tactics (optimal spawn locations + luring)
        3. Craft eyes of ender from collected pearls
        """
        ready = resources.phase_ready_result(Phase.ENDER_PEARL_FARM, "Pearls already gathered")
        if ready:
            return ready

        # 1. Return to Overworld if not already there
        from ...common import enter_nether_portal
        snapshot = client.transport.dispatch("get_state", {})
        dimension = snapshot.get("dimension", snapshot.get("world", {}).get("dimension", ""))
        if "overworld" not in dimension.lower():
            print("Returning to Overworld for Enderman farming...")
            success = enter_nether_portal(
                client,
                timeout=60,
                target_dimension="minecraft:overworld",
            )
            if not success:
                missing = resources.check_phase_requirements(Phase.ENDER_PEARL_FARM)
                return TaskResult.fail("Failed to return to Overworld", missing=missing)

        # 2. Enhanced Enderman hunting with spawn location optimization and mob luring
        print("Starting enhanced Enderman farming (finding optimal spawn locations and using luring tactics)...")
        pearls = hunt_endermen(client, target_count=12, timeout=1200)  # Extended timeout for systematic hunting

        print(f"Enderman hunt complete. Collected {pearls} pearls.")

        # 3. Craft Eyes of Ender with collected pearls
        eyes_crafted = 0
        if pearls >= 12:
            print("Crafting eyes of ender from collected pearls...")
            if craft_eyes_of_ender(client, required=12):
                eyes_crafted = 12
                print("Successfully crafted 12 eyes of ender.")
            else:
                print("Failed to craft eyes of ender despite having sufficient pearls.")
        else:
            print(f"Insufficient pearls for crafting eyes (need 12, have {pearls})")

        resources.refresh_inventory()
        missing = resources.check_phase_requirements(Phase.ENDER_PEARL_FARM)
        if pearls < 12 or eyes_crafted < 12 or missing:
            return TaskResult.fail(
                f"Pearl phase incomplete - collected {pearls} pearls, crafted {eyes_crafted} eyes",
                pearls=pearls,
                eyes=eyes_crafted,
                missing=missing,
            )

        print(f"Enderman farming phase completed successfully: {pearls} pearls collected, {eyes_crafted} eyes crafted.")
        return TaskResult.ok(
            "Pearls and eyes ready for stronghold/end portal activation",
            pearls=pearls,
            eyes=eyes_crafted,
        )
