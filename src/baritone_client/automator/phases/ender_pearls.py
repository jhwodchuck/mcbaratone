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
        1. Return to Overworld
        2. Hunt endermen (12+ pearls)
        3. Craft eyes of ender
        """
        ready = resources.phase_ready_result(Phase.ENDER_PEARL_FARM, "Pearls already gathered")
        if ready:
            return ready

        # 1. Return to Overworld if not already there
        from ...common import enter_nether_portal
        snapshot = client.transport.dispatch("get_state", {})
        dimension = snapshot.get("dimension", snapshot.get("world", {}).get("dimension", ""))
        if "overworld" not in dimension.lower():
            print("Returning to Overworld...")
            success = enter_nether_portal(client, timeout=60)
            if not success:
                missing = resources.check_phase_requirements(Phase.ENDER_PEARL_FARM)
                return TaskResult.fail("Failed to return to Overworld", missing=missing)

        # 2. Hunt Endermen
        print("Gathering ender pearls...")
        pearls = hunt_endermen(client, target_count=12)

        # 3. Craft Eyes of Ender
        if pearls >= 12:
            print("Crafting eyes of ender...")
            if craft_eyes_of_ender(client, required=12):
                eyes_crafted = 12
            else:
                eyes_crafted = 0
        else:
            eyes_crafted = 0

        resources.refresh_inventory()
        missing = resources.check_phase_requirements(Phase.ENDER_PEARL_FARM)
        if pearls < 12 or eyes_crafted < 12 or missing:
            return TaskResult.fail(
                "Pearl phase incomplete",
                pearls=pearls,
                eyes=eyes_crafted,
                missing=missing,
            )

        return TaskResult.ok(
            "Pearls and eyes ready",
            pearls=pearls,
            eyes=eyes_crafted,
        )
