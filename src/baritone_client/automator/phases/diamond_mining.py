"""
Diamond Mining Phase - Mine diamonds and craft diamond gear.
"""

import time

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import gather_ores, craft, equip_best_armor, go_to_y_level, sleep_through_night
from ...common.tasks import TaskResult
from ...common.combat import hunt_passive_mobs
from ...common.inventory import count_item


class DiamondMiningHandler(PhaseHandler):
    """Handler for diamond mining phase."""
    
    def get_name(self) -> str:
        return "Diamond Mining"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        """
        1. Mine diamonds (20+)
        2. Mine obsidian (14+)
        3. Craft diamond gear
        """
        ready = resources.phase_ready_result(Phase.DIAMOND_MINING, "Diamond phase already satisfied")
        if ready:
            return ready

        resources.refresh_inventory()
        self._restock_food(client)
        starting_diamonds = resources.get_item_count("minecraft:diamond")
        starting_obsidian = resources.get_item_count("minecraft:obsidian")

        # Go to diamond level
        print("Moving to diamond mining level (Y=-59)...")
        go_to_y_level(client, -59)

        # 1. Mine Diamonds
        target_diamonds = 20
        print(f"Mining diamonds (Target: {target_diamonds})...")
        
        # Loop to allow sleeping
        start_mining = time.time()
        while resources.get_item_count("minecraft:diamond") < target_diamonds:
            # Sleep check
            sleep_through_night(client)
            
            # Mine (short timeout to allow sleep checks)
            gather_ores(client, "diamond", count=target_diamonds, timeout=180)
            
            resources.refresh_inventory()
            if time.time() - start_mining > 1200: # 20 mins total cap
                 break

        if resources.get_item_count("minecraft:diamond") < target_diamonds:
            missing = resources.check_phase_requirements(Phase.DIAMOND_MINING)
            return TaskResult.fail("Failed to gather diamonds", missing=missing)
        resources.refresh_inventory()
        diamonds_mined = max(0, resources.get_item_count("minecraft:diamond") - starting_diamonds)

        # 2. Mine Obsidian
        print("Gathering obsidian...")
        client.transport.dispatch("mine", {
            "blocks": ["minecraft:obsidian"],
            "quantity": 14
        })
        start = time.time()
        while time.time() - start < 600:
            current_obsidian = resources.refresh_inventory().get("minecraft:obsidian", 0)
            if current_obsidian >= starting_obsidian + 14:
                client.transport.dispatch("cancel", {})
                break
            time.sleep(10)
        obsidian_collected = max(0, resources.get_item_count("minecraft:obsidian") - starting_obsidian)

        # 3. Craft Diamond Gear
        print("Crafting diamond gear...")
        crafted_pick = craft(client, "minecraft:diamond_pickaxe", 1)
        crafted_sword = craft(client, "minecraft:diamond_sword", 1)

        # Equip armor
        print("Equipping best armor...")
        equip_best_armor(client)
        resources.refresh_inventory()
        missing = resources.check_phase_requirements(Phase.DIAMOND_MINING)
        if missing:
            return TaskResult.fail(
                "Diamond phase incomplete",
                missing=missing,
                diamonds_mined=diamonds_mined,
                obsidian_collected=obsidian_collected,
                crafted_pick=crafted_pick,
                crafted_sword=crafted_sword,
            )
        summary = resources.get_summary()
        return TaskResult.ok(
            "Diamond gear secured",
            inventory=summary["inventory"],
            diamonds_mined=diamonds_mined,
            obsidian_collected=obsidian_collected,
            crafted_pick=crafted_pick,
            crafted_sword=crafted_sword,
        )

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
