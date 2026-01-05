"""
Phase 2: Iron & Diamond Phase
"""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import StateManager
from ...common import TaskResult, SequentialTask, ActionTask
from ...common.resources import gather_ores, ensure_supplies, go_to_y_level, gather_stone
from ...common.inventory import count_item

class FoodAndIronHandler(PhaseHandler):
    """Phase 2: Iron & Diamond mining - Hour 1-2."""
    
    def get_name(self) -> str:
        return "Iron & Diamond (Hour 1-2)"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        tasks = [
            # Phase 2a: Get initial iron (Baritone will dig to reach it)
            ActionTask("Ensure stone pickaxe", lambda c: ensure_supplies(c, {"minecraft:stone_pickaxe": 1}).success),
            ActionTask("Mine initial iron (15)", self._mine_initial_iron),  # Creates tunnels naturally!
            ActionTask("Smelt iron ingots", self._smelt_iron),
            ActionTask("Craft iron pickaxe + bucket", self._craft_essential_iron),
            
            # Phase 2b: Now mine deep diamonds with iron tools
            ActionTask("Dig to diamond level Y-58", self._dig_staircase),
            ActionTask("Mine diamonds & remaining iron", self._bulk_mine),
            ActionTask("Craft full iron armor", self._craft_iron_armor),
            ActionTask("Craft iron tools", self._craft_iron_tools),
        ]
        
        executor = SequentialTask("Iron & Diamond", tasks)
        return executor.run(client)

    def _dig_staircase(self, client) -> bool:
        """Dig a proper staircase down to Y-58."""
        print("Digging staircase to Y-58...")
        return go_to_y_level(client, -58)

    def _mine_initial_iron(self, client) -> bool:
        """Mine just enough iron for basic tools (15 = pickaxe + bucket + spare)."""
        print("  Mining initial iron (15 ore)...")
        return gather_ores(client, "iron", count=15, timeout=300)

    def _smelt_iron(self, client) -> bool:
        """Smelt raw iron into ingots using furnace."""
        # Uses imports from file header: ensure_supplies, gather_stone, count_item
        
        # First ensure we have a furnace (requires 8 cobblestone)
        if count_item(client, "minecraft:furnace") == 0:
            print("  Need furnace - getting cobblestone...")
            gather_stone(client, count=16, timeout=120)
            ensure_supplies(client, {"minecraft:furnace": 1})
        
        # Check how much raw iron we have
        raw_iron = count_item(client, "minecraft:raw_iron")
        if raw_iron == 0:
            print("  No raw iron to smelt!")
            return False
        
        # Need coal for smelting
        coal = count_item(client, "minecraft:coal")
        if coal < raw_iron // 8 + 1:
            print("  Need more coal for smelting...")
            gather_ores(client, "coal", count=max(8, raw_iron // 8 + 2), timeout=120)
        
        # Use ensure_supplies which handles smelting
        print(f"  Smelting {raw_iron} raw iron...")
        return ensure_supplies(client, {"minecraft:iron_ingot": raw_iron}).success

    def _craft_essential_iron(self, client) -> bool:
        """Craft iron pickaxe and bucket first."""
        return ensure_supplies(client, {
            "minecraft:iron_pickaxe": 1,
            "minecraft:bucket": 1,
        }).success

    def _bulk_mine(self, client) -> bool:
        """Mine remaining resources with iron pickaxe."""
        targets = [
            ("iron", 40),  # Reduced from 64 (we already have 15)
            ("coal", 32),
            ("diamond", 5),
        ]
        for ore_type, count in targets:
            print(f"  Mining {ore_type} (target: {count})...")
            gather_ores(client, ore_type, count=count, timeout=600)
        return True

    def _craft_iron_armor(self, client) -> bool:
        """Craft full iron armor set."""
        return ensure_supplies(client, {
            "minecraft:iron_helmet": 1,
            "minecraft:iron_chestplate": 1,
            "minecraft:iron_leggings": 1,
            "minecraft:iron_boots": 1,
        }).success

    def _craft_iron_tools(self, client) -> bool:
        """Craft iron tools."""
        return ensure_supplies(client, {
            "minecraft:iron_pickaxe": 1,
            "minecraft:iron_sword": 1,
            "minecraft:iron_axe": 1,
            "minecraft:iron_shovel": 1,
        }).success
