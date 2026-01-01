"""
Iron Age Phase - Mine iron ore, smelt ingots, craft iron tools and armor, mining loop.
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import craft
from ...common.resources import gather_ores
from ...common.tasks import TaskResult, SequentialTask, ActionTask
from ...common.base import open_furnace
from ...common.inventory import find_item_slot, count_item
import time


def smelt_iron(client, required_ingots: int = 24) -> bool:
    """Smelt raw iron into ingots using furnace."""
    # Check current iron ingots
    current_ingots = count_item(client, "minecraft:iron_ingot")
    if current_ingots >= required_ingots:
        return True

    needed_ingots = required_ingots - current_ingots
    raw_iron_needed = needed_ingots  # 1 raw iron = 1 ingot

    # Check if we have enough raw iron
    raw_iron_count = count_item(client, "minecraft:raw_iron")
    if raw_iron_count < raw_iron_needed:
        return False  # Not enough raw iron to smelt

    # Open furnace
    if not open_furnace(client):
        return False
        
    # Find slots
    iron_slot = find_item_slot(client, "minecraft:raw_iron")
    fuel_slot = find_item_slot(client, "minecraft:coal") or find_item_slot(client, "minecraft:charcoal")
    
    if iron_slot is None:
        return False
    if fuel_slot is None:
        # Try finding wood if no coal
        fuel_slot = find_item_slot(client, "minecraft:oak_log") or find_item_slot(client, "minecraft:oak_planks")
        if fuel_slot is None:
            return False

    try:
        params = {
            "input_slot": iron_slot,
            "fuel_slot": fuel_slot,
        }
        result = client.transport.dispatch("smelt_items", params)
        success = result.get("status") == "ok" if result else False
        
        if success:
            print(f"  Smelting {raw_iron_count} raw iron... (waiting 30s)")
            time.sleep(30) # Wait some time for smelting
            client.transport.dispatch("close_screen", {})
            return True
        return False
    except Exception as e:
        print(f"Smelting error: {e}")
        return False


def craft_iron_tools(client) -> bool:
    """Craft iron pickaxe, sword, and axe."""
    tools = [
        "minecraft:iron_pickaxe",
        "minecraft:iron_sword",
        "minecraft:iron_axe"
    ]
    for tool in tools:
        if not craft(client, tool, 1):
            return False
    return True


def craft_iron_armor(client) -> bool:
    """Craft full iron armor set."""
    armor_pieces = [
        "minecraft:iron_helmet",
        "minecraft:iron_chestplate",
        "minecraft:iron_leggings",
        "minecraft:iron_boots"
    ]
    for piece in armor_pieces:
        if not craft(client, piece, 1):
            return False
    return True


class IronAgeHandler(PhaseHandler):
    """Handler for iron age phase using common library functions."""

    def get_name(self) -> str:
        return "Iron Age"

    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        """
        Execute iron age following progression:
        1. Mine 24+ iron ore at appropriate depths
        2. Smelt raw iron to ingots
        3. Craft full iron tool set and armor
        4. Establish mining loop: mine → smelt → upgrade gear → repeat
        """
        ready = resources.phase_ready_result(Phase.IRON_AGE, "Iron age already satisfied")
        if ready:
            return ready

        # Define initial subtasks
        tasks = [
            ActionTask("Mine iron ore", gather_ores, ore_type="iron", count=24, timeout=600),
            ActionTask("Smelt iron ingots", smelt_iron, required_ingots=24),
            ActionTask("Craft iron tools", craft_iron_tools),
            ActionTask("Craft iron armor", craft_iron_armor),
        ]

        # Execute initial setup sequentially
        sequential_task = SequentialTask("Iron Age Setup", tasks)
        result = sequential_task.run(client)

        if not result.success:
            missing = resources.check_phase_requirements(Phase.IRON_AGE)
            return TaskResult.fail(f"Iron age setup failed: {result.reason}", missing=missing)

        # After initial setup, enter mining loop
        loop_result = self._mining_loop(client, resources, state)
        if loop_result.success:
            resources.refresh_inventory()
            summary = resources.get_summary()
            return TaskResult.ok("Iron age complete with mining loop", inventory=summary["inventory"])
        else:
            return TaskResult.fail(f"Mining loop failed: {loop_result.reason}")

    def _mining_loop(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        """Establish mining loop: mine more iron → smelt → upgrade gear → repeat."""
        iterations = 0
        max_iterations = 5  # Limit iterations to prevent infinite loop
        failures = 0

        while iterations < max_iterations:
            # Mine additional iron ore
            if not gather_ores(client, "iron", count=10, timeout=300):
                failures += 1
                break  # No more iron to mine

            # Smelt the additional iron
            if not smelt_iron(client, required_ingots=count_item(client, "minecraft:iron_ingot") + 10):
                failures += 1
                break

            iterations += 1
            resources.refresh_inventory()
            if resources.is_phase_ready(Phase.IRON_AGE):
                break

        iron_ingots = resources.get_item_count("minecraft:iron_ingot")
        if failures:
            return TaskResult.fail(
                "Mining loop stalled",
                iterations=iterations,
                failures=failures,
                iron_ingots=iron_ingots,
            )
        return TaskResult.ok(
            f"Mining loop completed {iterations} iterations",
            iterations=iterations,
            iron_ingots=iron_ingots,
        )
