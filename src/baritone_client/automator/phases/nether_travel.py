"""
Nether Travel Phase - Blaze rods and gold.
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import enter_nether_portal, find_nether_fortress, hunt_blazes, mine_nether_gold, barter_with_piglins, craft, find_nearest_portal
from ...common.inventory import count_item
from ...common.tasks import TaskResult


class NetherTravelHandler(PhaseHandler):
    """Handler for nether travel phase."""
    
    def get_name(self) -> str:
        return "Nether Travel (Blazes & Gold)"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        """
        1. Enter Nether portal
        2. Find fortress
        3. Hunt blazes (10+ rods)
        """
        ready = resources.phase_ready_result(Phase.NETHER_TRAVEL, "Nether travel already satisfied")
        if ready:
            return ready

        # 1. Enter portal if in Overworld
        snapshot = client.transport.dispatch("get_state", {})
        dimension = snapshot.get("dimension", snapshot.get("world", {}).get("dimension", ""))
        if "overworld" in dimension.lower():
            print("Entering Nether...")
            if not enter_nether_portal(client):
                missing = resources.check_phase_requirements(Phase.NETHER_TRAVEL)
                return TaskResult.fail("Failed to enter Nether", missing=missing)

            # Record Nether portal location for death recovery
            nether_portal = find_nearest_portal(client, "nether")
            if nether_portal:
                state.add_location("portal", nether_portal[0], nether_portal[1], nether_portal[2], "nether", ["nether_portal", "entered"])
                print(f"Recorded Nether portal at {nether_portal}")
            else:
                print("Warning: Could not locate Nether portal after entering")
        
        # 1.5. Mine Gold and Barter (if needed)
        pearls = count_item(client, "minecraft:ender_pearl")
        if pearls < 12:
            print(f"Need pearls ({pearls}/12). Starting Gold/Barter loop...")
            
            # Mine Gold Nuggets
            # Need approx 40 ingots -> 360 nuggets. Let's try 20 ingots first (180 nuggets).
            current_nuggets = count_item(client, "minecraft:gold_nugget")
            if current_nuggets < 180:
                print("Mining Nether Gold...")
                mine_nether_gold(client, count=180)
            
            # Craft Ingots
            print("Crafting Gold Ingots...")
            gold_needed = 20
            # 9 nuggets = 1 ingot
            craft(client, "minecraft:gold_ingot", gold_needed)
            
            # Barter
            ingots = count_item(client, "minecraft:gold_ingot")
            if ingots > 0:
                print(f"Bartering with Piglins ({ingots} ingots)...")
                barter_with_piglins(client, gold_ingots_count=ingots)
                
            # Check results
            pearls = count_item(client, "minecraft:ender_pearl")
            print(f"Pearl count after bartering: {pearls}")

        # 2. Find Fortress
        print("Searching for Nether Fortress...")
        fortress_coords = find_nether_fortress(client)
        if not fortress_coords:
            return TaskResult.fail("Failed to find fortress", missing={"fortress": 1})
            
        # 3. Hunt Blazes
        print("Farming blaze rods...")
        rods = hunt_blazes(client, target_count=10)
        
        # 4. Fallback Hunt Endermen if pearls still missing
        pearls = count_item(client, "minecraft:ender_pearl")
        if pearls < 12:
            print(f"Bartering insufficient ({pearls}/12). Hunting Endermen...")
            # Import strictly inside method to avoid circular imports if any, though likely fine at top if available
            from ...common import hunt_endermen 
            pearls = hunt_endermen(client, target_count=12)

        resources.refresh_inventory()
        missing = resources.check_phase_requirements(Phase.NETHER_TRAVEL)
        if rods < 10 or pearls < 12 or missing:
            return TaskResult.fail(
                f"Nether phase incomplete",
                blaze_rods=rods,
                ender_pearls=pearls,
                fortress=fortress_coords,
                missing=missing,
            )
            
        return TaskResult.ok(
            f"Collected {rods} blaze rods and {pearls} pearls",
            blaze_rods=rods,
            ender_pearls=pearls,
            fortress=fortress_coords,
            inventory=resources.get_summary()["inventory"],
        )
