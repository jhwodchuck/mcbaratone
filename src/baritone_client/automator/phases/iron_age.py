"""
Iron Age Phase - Mine iron ore, smelt ingots, craft iron tools and armor, mining loop.
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import craft
from ...common.resources import gather_ores, ensure_supplies, gather_wood
from ...common.combat import hunt_passive_mobs
from ...common.tasks import TaskResult, SequentialTask, ActionTask
from ...common.base import open_furnace, sleep_through_night, build_good_house
from ...common.automation_utils import get_player_pos
from ...common.inventory import find_item_slot, count_item, equip_offhand
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
        4. Establish mining loop: mine -> smelt -> upgrade gear -> repeat
        """
        ready = resources.phase_ready_result(Phase.IRON_AGE, "Iron age already satisfied")
        if ready:
            return ready

        # Define initial subtasks
        # Define initial subtasks
        tasks = [
            ActionTask("Restock Food", self._restock_food),
            ActionTask("Mine iron ore", gather_ores, ore_type="iron", count=24, timeout=600),
            ActionTask("Smelt iron ingots", smelt_iron, required_ingots=24),
            ActionTask("Craft iron tools", craft_iron_tools),
            ActionTask("Craft iron armor", craft_iron_armor),
            ActionTask("Craft Shield", self._craft_shield),
            ActionTask("Build Good Base", self._build_good_base),
            ActionTask("Acquire Water Bucket", self._acquire_water_bucket),
            ActionTask("Acquire Ranged Weapon", self._acquire_bow_and_arrows),

            ActionTask("Acquire Ranged Weapon", self._acquire_bow_and_arrows),

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
        """Establish mining loop: mine more iron -> smelt -> upgrade gear -> repeat."""
        iterations = 0
        max_iterations = 5  # Limit iterations to prevent infinite loop
        failures = 0

        while iterations < max_iterations:
            # Check for sleep
            sleep_through_night(client)

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

    def _acquire_bow_and_arrows(self, client) -> bool:
        """Gather materials and craft bow + arrows."""
        # 1. Gather raw materials (Strings, Feathers, Flint) and Wood
        print("Gathering combat gear materials...")
        
        # Ensure wood for sticks
        if count_item(client, "minecraft:oak_log") < 4:
            gather_wood(client, count=8)
        
        # Craft sticks if needed
        if count_item(client, "minecraft:stick") < 8:
            craft(client, "minecraft:oak_planks", 4)
            craft(client, "minecraft:stick", 8)

        # Gather main ingredients
        # Bow: 3 string, 3 sticks
        # Arrow: 1 flint, 1 stick, 1 feather -> 4 arrows.
        # Target 32 arrows = 8 crafts = 8 flint, 8 feathers, 8 sticks.
        result = ensure_supplies(client, {
            "minecraft:string": 3,
            "minecraft:feather": 8, 
            "minecraft:flint": 8,
        })
        if not result.success:
            print(f"Failed to gather materials: {result.missing}")
            # Try to craft anyway with what we have
        
        # 2. Craft Bow
        print("Crafting Bow...")
        if not ensure_supplies(client, {"minecraft:bow": 1}).success:
            print("Failed to craft bow")
            
        # 3. Craft Arrows
        print("Crafting Arrows...")
        if not ensure_supplies(client, {"minecraft:arrow": 32}).success:
            print("Failed to craft arrows")
            
        return count_item(client, "minecraft:bow") > 0

    def _restock_food(self, client) -> bool:
        """Hunt for food if low."""
        food_items = ["minecraft:cooked_beef", "minecraft:cooked_porkchop", 
                      "minecraft:cooked_chicken", "minecraft:cooked_mutton",
                      "minecraft:bread"]
        current_food = sum(count_item(client, item) for item in food_items)
        if current_food < 16:
            print(f"Food low ({current_food}), hunting...")
            hunt_passive_mobs(client, target_count=10)
        return True

    def _craft_shield(self, client) -> bool:
        """Craft and equip shield."""
        print("Crafting and equipping Shield...")
        # Needs 1 iron, 6 planks
        if count_item(client, "minecraft:iron_ingot") < 1:
            smelt_iron(client, required_ingots=count_item(client, "minecraft:iron_ingot") + 1)
        
        if count_item(client, "minecraft:oak_planks") < 6:
            gather_wood(client, count=2) # 2 logs = 8 planks
            craft(client, "minecraft:oak_planks", 2)
            
        if craft(client, "minecraft:shield"):
            time.sleep(1)
            equip_offhand(client, "minecraft:shield")
            return True
        return False

    def _acquire_water_bucket(self, client) -> bool:
        """Acquire a water bucket."""
        print("Acquiring Water Bucket...")
        # Needs 3 iron for bucket
        if count_item(client, "minecraft:iron_ingot") < 3:
             smelt_iron(client, required_ingots=count_item(client, "minecraft:iron_ingot") + 3)
             
        if count_item(client, "minecraft:iron_ingot") < 3:
             smelt_iron(client, required_ingots=count_item(client, "minecraft:iron_ingot") + 3)
             
        # Strategy for water_bucket handles bucket crafting and water gathering
        result = ensure_supplies(client, {"minecraft:water_bucket": 1})
        return result.success

    def _build_good_base(self, client) -> bool:
        """Build an upgraded 'Good' base."""
        # Use current position or find new spot
        from ...common.navigation import get_player_pos
        pos = get_player_pos(client)
        if not pos:
            return False
        return build_good_house(client, int(pos[0]), int(pos[1]), int(pos[2]))
