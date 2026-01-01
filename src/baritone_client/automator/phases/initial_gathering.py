"""
Initial Gathering Phase - Wood, stone, food, basic tools.
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import gather_wood, gather_stone, craft
from ...common.base import build_emergency_shelter, sleep_through_night
from ...common.combat import hunt_passive_mobs
from ...common.tasks import TaskResult, SequentialTask, ActionTask


def gather_leather(client) -> bool:
    """Gather leather by hunting cows/sheep."""
    from ...common.combat import hunt_mobs
    result = hunt_mobs(
        client,
        mob_types=["cow", "sheep"],
        required_loot={"minecraft:leather": 16},  # For 4 armor pieces, need about 16 leather
        search_radius=50,
        timeout=300,
        heal_threshold=5.0,
    )
    return result.success


def craft_leather_armor(client) -> bool:
    """Craft leather armor pieces."""
    armor_pieces = [
        "minecraft:leather_helmet",
        "minecraft:leather_chestplate",
        "minecraft:leather_leggings",
        "minecraft:leather_boots"
    ]
    for piece in armor_pieces:
        if not craft(client, piece, 1):
            return False
    return True


class InitialGatheringHandler(PhaseHandler):
    """Handler for initial resource gathering phase using common library functions."""

    def get_name(self) -> str:
        return "Initial Gathering"

    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        """
        Gather initial resources following progression:
        1. Gather 16+ wood logs
        2. Craft wooden tools
        3. Mine 16+ cobblestone
        4. Craft stone tools
        5. Craft leather armor
        6. Hunt for 10+ food items
        """
        if ready:
            return ready

        # 0. Safety Check
        # If night falls and we have no bed, build emergency shelter
        if not sleep_through_night(client):
             # Sleep failed (no bed or not night). If night, build shelter.
             state_data = client.transport.dispatch("get_state", {})
             time_val = state_data.get("world_time", 0) % 24000
             if time_val >= 13000:
                 print("Nightfall detected! Building Emergency Shelter...")
                 build_emergency_shelter(client)
                 # Wait for morning
                 import time
                 time.sleep(30) # Wait a bit
                 return TaskResult.fail("Built emergency shelter due to night")

        # Define subtasks
        tasks = [
            ActionTask("Gather wood", gather_wood, count=16, timeout=180),
            ActionTask("Craft wooden tools", self._craft_wooden_tools),
            ActionTask("Mine cobblestone", gather_stone, count=16, timeout=180),
            ActionTask("Craft stone tools", self._craft_stone_tools),
            ActionTask("Gather leather", gather_leather),
            ActionTask("Craft leather armor", craft_leather_armor),
            ActionTask("Hunt food", hunt_passive_mobs, target_count=10, timeout=300),
        ]

        # Execute sequentially
        sequential_task = SequentialTask("Initial Gathering", tasks)
        result = sequential_task.run(client)

        if result.success:
            resources.refresh_inventory()
            summary = resources.get_summary()
            return TaskResult.ok("Initial gathering complete", inventory=summary["inventory"])
        else:
            missing = resources.check_phase_requirements(Phase.INITIAL_GATHERING)
            return TaskResult.fail(f"Initial gathering failed: {result.reason}", missing=missing)

    def _craft_wooden_tools(self, client) -> bool:
        """Craft wooden tools."""
        craft(client, "minecraft:oak_planks", 32)
        craft(client, "minecraft:stick", 16)
        return craft(client, "minecraft:wooden_pickaxe", 1)

    def _craft_stone_tools(self, client) -> bool:
        """Craft stone tools."""
        return (
            craft(client, "minecraft:stone_pickaxe", 1) and
            craft(client, "minecraft:stone_sword", 1) and
            craft(client, "minecraft:stone_axe", 1)
        )
