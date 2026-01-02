"""
Resource Manager - Inventory monitoring and requirement tracking.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..common.resources import ensure_supplies
from ..common.tasks import TaskResult
from .state_manager import Phase


@dataclass
class CraftingTask:
    """A queued crafting task."""
    item_id: str
    quantity: int
    priority: int = 0


class ResourceManager:
    """
    Manages inventory tracking and resource requirements.
    
    Provides:
    - Inventory aggregation from bridge
    - Per-phase requirement checking
    - Crafting queue management
    - Resource counting utilities
    """
    
    # Crafting Recipes (item_id -> ingredients)
    # Ingredients: List of (item_id, count)
    # This is a simplified model.
    # Default fallback recipes
    DEFAULT_RECIPES: Dict[str, Dict[str, Any]] = {
        "minecraft:oak_planks": {"ingredients": [("minecraft:oak_log", 1)], "yield": 4},
        "minecraft:birch_planks": {"ingredients": [("minecraft:birch_log", 1)], "yield": 4},
        "minecraft:spruce_planks": {"ingredients": [("minecraft:spruce_log", 1)], "yield": 4},
        "minecraft:dark_oak_planks": {"ingredients": [("minecraft:dark_oak_log", 1)], "yield": 4},
        "minecraft:acacia_planks": {"ingredients": [("minecraft:acacia_log", 1)], "yield": 4},
        "minecraft:jungle_planks": {"ingredients": [("minecraft:jungle_log", 1)], "yield": 4},
        "minecraft:stick": {"ingredients": [("minecraft:oak_planks", 2)], "yield": 4}, # Generic fallback
        "minecraft:crafting_table": {"ingredients": [("minecraft:oak_planks", 4)], "yield": 1},
        "minecraft:wooden_pickaxe": {"ingredients": [("minecraft:oak_planks", 3), ("minecraft:stick", 2)], "yield": 1},
        "minecraft:stone_pickaxe": {"ingredients": [("minecraft:cobblestone", 3), ("minecraft:stick", 2)], "yield": 1},
        "minecraft:stone_sword": {"ingredients": [("minecraft:cobblestone", 2), ("minecraft:stick", 1)], "yield": 1},
        "minecraft:stone_axe": {"ingredients": [("minecraft:cobblestone", 3), ("minecraft:stick", 2)], "yield": 1},
        "minecraft:furnace": {"ingredients": [("minecraft:cobblestone", 8)], "yield": 1},
    }

    # Phase requirements (item_id -> minimum count)
    PHASE_REQUIREMENTS: Dict[Phase, Dict[str, int]] = {
        Phase.SPAWN_BOOTSTRAP: {
            "minecraft:oak_log": 8,
            "minecraft:cooked_beef": 4,
        },
        Phase.INITIAL_GATHERING: {
            "minecraft:oak_log": 16,
            "minecraft:cobblestone": 16,
        },
        Phase.BASE_CONSTRUCTION: {
            "minecraft:crafting_table": 1,
            "minecraft:furnace": 1,
            "minecraft:chest": 2,
        },
        Phase.IRON_AGE: {
            "minecraft:iron_ingot": 24,
            "minecraft:iron_pickaxe": 1,
            "minecraft:iron_sword": 1,
        },
        Phase.DIAMOND_MINING: {
            "minecraft:diamond": 20,
            "minecraft:diamond_pickaxe": 1,
            "minecraft:diamond_sword": 1,
        },
        Phase.ENCHANTING: {
            "minecraft:enchanting_table": 1,
            "minecraft:bookshelf": 15,
            "minecraft:experience_bottle": 0,  # Experience tracked separately
        },
        Phase.NETHER_PREP: {
            "minecraft:obsidian": 14,
            "minecraft:flint_and_steel": 1,
        },
        Phase.NETHER_TRAVEL: {
            "minecraft:blaze_rod": 10,
            "minecraft:gold_ingot": 20,
        },
        Phase.ENDER_PEARL_FARM: {
            "minecraft:ender_pearl": 20,
        },
        Phase.STRONGHOLD_LOCATE: {
            "minecraft:ender_eye": 12,
        },
        Phase.END_PORTAL: {},  # No specific items needed
        Phase.DRAGON_FIGHT: {
            "minecraft:arrow": 64,
            "minecraft:bow": 1,
        },
        Phase.COMPLETE: {},
    }
    
    def __init__(self, client):
        """
        Initialize resource manager.
        
        Args:
            client: Baritone client instance
        """
        self.client = client
        self.cached_inventory: Dict[str, int] = {}
        self.crafting_queue: List[CraftingTask] = []
        self.recipes = self.DEFAULT_RECIPES.copy()
        
        # Resource locking
        self.reserved_resources: Dict[str, int] = {}
        self.active_requests: List[Dict] = []  # List of {item: str, amount: int, priority: int}
        
        # Efficiency Tracking
        self.stats = {
            "gathered": {},
            "start_time": {},
            "rates": {}
        }

    # Material Equivalencies
    EQUIVALENCIES = {
        "#logs": [
            "minecraft:oak_log", "minecraft:birch_log", "minecraft:spruce_log",
            "minecraft:jungle_log", "minecraft:acacia_log", "minecraft:dark_oak_log",
            "minecraft:crimson_stem", "minecraft:warped_stem"
        ],
        "#planks": [
            "minecraft:oak_planks", "minecraft:birch_planks", "minecraft:spruce_planks",
            "minecraft:jungle_planks", "minecraft:acacia_planks", "minecraft:dark_oak_planks",
            "minecraft:crimson_planks", "minecraft:warped_planks"
        ],
        "#stone_tool_material": ["minecraft:cobblestone", "minecraft:blackstone"],
        "#coals": ["minecraft:coal", "minecraft:charcoal"],
    }

    def initialize_recipes(self) -> None:
        """Fetch recipes from the game if available."""
        try:
            print("Fetching recipes from bridge...")
            data = self.client.transport.dispatch("get_recipes", {})
            
            fetched_count = 0
            if "recipes" in data:
                raw_recipes = data["recipes"]
                for r in raw_recipes:
                    recipe_id = r.get("id")
                    output = r.get("output", {})
                    
                    # Some responses might be simplified
                    output_item = output.get("item")
                    output_count = output.get("count", 1)
                    
                    if not output_item: 
                        # Try to infer from ID if output not explicit (depends on bridge version)
                        # The simple bridge might just return raw data
                         continue

                    ingredients = []
                    # Parse ingredients... (Bridge format varies, assuming simplified here)
                    # For now, we trust the defaults more but this is the hook to expand.
                    # Implementation depends on bridge 'get_recipes' structure.
                    
                    # self.recipes[output_item] = ... 
                    fetched_count += 1
            
            print(f"Fetched {fetched_count} recipes (Support limited in this version). Using fallback defaults.")
            
        except Exception as e:
            print(f"Failed to fetch recipes: {e}. Using defaults.")

    def request_resources(self, item_id: str, quantity: int, priority: int = 10) -> None:
        """
        Register a dynamic resource request.
        
        Args:
            item_id: Item needed
            quantity: Amount needed
            priority: Importance (higher = more urgent)
        """
        self.active_requests.append({
            "item": item_id,
            "quantity": quantity,
            "priority": priority
        })
        self.active_requests.sort(key=lambda x: -x["priority"])

    def reserve_resource(self, item_id: str, quantity: int) -> bool:
        """
        Attempt to reserve a resource for exclusive use.
        
        Returns:
            True if sufficient unreserved resources exist.
        """
        current_total = self.get_item_count(item_id)
        current_reserved = self.reserved_resources.get(item_id, 0)
        
        available = current_total - current_reserved
        if available >= quantity:
            self.reserved_resources[item_id] = current_reserved + quantity
            return True
        return False

    def release_resource(self, item_id: str, quantity: int) -> None:
        """Release reserved resources."""
        if item_id in self.reserved_resources:
            self.reserved_resources[item_id] = max(0, self.reserved_resources[item_id] - quantity)
    
    def refresh_inventory(self) -> Dict[str, int]:
        """
        Refresh inventory from bridge and return aggregated counts.

        Returns:
            Dict of item_id -> total count
        """
        import time
        start_time = time.time()
        try:
            # Inventory requests should be immediate: if the bridge does not respond
            # promptly, return the last cached inventory rather than retrying.
            data = self.client.transport.dispatch("get_inventory", {}, timeout=0.2)
            elapsed = time.time() - start_time
            print(f"DEBUG: refresh_inventory took {elapsed:.2f}s")
        except Exception as e:
            elapsed = time.time() - start_time
            print(f"Failed to refresh inventory after {elapsed:.2f}s: {e}")
            return self.cached_inventory

        counts: Dict[str, int] = {}
        # Aggregate all inventory slots
        for section in ["inventory", "armor", "offhand"]:
            for item in data.get(section, []):
                item_id = item.get("id", "")
                count = item.get("count", 0)
                if item_id and count > 0:
                    counts[item_id] = counts.get(item_id, 0) + count

        self.cached_inventory = counts
        self._update_efficiency_stats(counts)
        return counts
        
    def _update_efficiency_stats(self, current_inventory: Dict[str, int]):
        """Update efficiency tracking metrics."""
        import time
        now = time.time()
        
        for item, count in current_inventory.items():
            if item not in self.stats["start_time"]:
                self.stats["start_time"][item] = now
            
            # Simple rate tracking (items per minute since tracking started)
            start = self.stats["start_time"][item]
            duration = (now - start) / 60.0
            if duration > 0.1:
                self.stats["rates"][item] = count / duration
    
    def get_item_count(self, item_id: str, include_reserved: bool = True) -> int:
        """
        Get count of specific item or item group (starting with #).
        
        Args:
            item_id: Minecraft item ID or Group ID (e.g., "#logs")
            
        Returns:
            Count of item in inventory
        """
        if item_id.startswith("#"):
            # Sum all items in the group
            group = self.EQUIVALENCIES.get(item_id, [])
            total = 0
            for specific_item in group:
                total += self.cached_inventory.get(specific_item, 0)
            return total
            
        return self.cached_inventory.get(item_id, 0)
    
    def has_items(self, requirements: Dict[str, int]) -> bool:
        """
        Check if inventory has all required items.
        
        Args:
            requirements: Dict of item_id -> minimum count
            
        Returns:
            True if all requirements met
        """
        for item_id, required in requirements.items():
            if self.get_item_count(item_id) < required:
                return False
        return True
    
    def check_phase_requirements(self, phase: Phase) -> Dict[str, int]:
        """
        Check what's missing for a phase.
        
        Args:
            phase: Phase to check requirements for
            
        Returns:
            Dict of missing item_id -> shortfall count
        """
        requirements = self.PHASE_REQUIREMENTS.get(phase, {})
        missing = {}
        
        for item_id, required in requirements.items():
            current = self.get_item_count(item_id)
            if current < required:
                missing[item_id] = required - current
        
        return missing
    
    def get_phase_requirements(self, phase: Phase) -> Dict[str, int]:
        """Return a copy of the requirements for a phase."""
        return dict(self.PHASE_REQUIREMENTS.get(phase, {}))

    def phase_ready_result(self, phase: Phase, message: str) -> Optional[TaskResult]:
        """
        If the phase already satisfies its requirements, return a TaskResult describing the skip.
        Otherwise, return None so the handler can continue work.
        """
        requirements = self.PHASE_REQUIREMENTS.get(phase, {})
        if not requirements:
            return None

        self.refresh_inventory()
        if self.is_phase_ready(phase):
            return TaskResult.ok(
                message,
                inventory=self.get_summary()["inventory"],
                skipped=True,
            )
        return None
    
    def is_phase_ready(self, phase: Phase) -> bool:
        """Check if all requirements for a phase are met."""
        return len(self.check_phase_requirements(phase)) == 0
    
    def queue_crafting(self, item_id: str, quantity: int, priority: int = 0) -> None:
        """
        Add item to crafting queue.
        
        Args:
            item_id: Item to craft
            quantity: Number to craft
            priority: Higher priority = craft first
        """
        task = CraftingTask(item_id, quantity, priority)
        self.crafting_queue.append(task)
        self.crafting_queue.sort(key=lambda t: -t.priority)
    
    def get_next_crafting_task(self) -> Optional[CraftingTask]:
        """Get next item to craft from queue."""
        if self.crafting_queue:
            return self.crafting_queue.pop(0)
        return None
    
    def clear_crafting_queue(self) -> None:
        """Clear all queued crafting tasks."""
        self.crafting_queue.clear()
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of current resources."""
        return {
            "total_items": sum(self.cached_inventory.values()),
            "unique_items": len(self.cached_inventory),
            "queued_crafts": len(self.crafting_queue),
            "inventory": dict(self.cached_inventory),
        }
    
    def find_item_slot(self, item_id: str) -> Optional[int]:
        """
        Find slot containing specified item.
        
        Returns:
            Slot index or None if not found
        """
        try:
            data = self.client.transport.dispatch("get_inventory", {}, timeout=0.2)
            
            for item in data.get("inventory", []):
                if item.get("id") == item_id and item.get("count", 0) > 0:
                    return item.get("slot")
            
            return None
            
        except Exception:
            return None

    def ensure_phase_supplies(self, client, phase: Phase) -> TaskResult:
        """Validate phase requirements and trigger mission macros if needed."""
        requirements = self.PHASE_REQUIREMENTS.get(phase, {})
        if not requirements:
            return TaskResult.ok("No requirements for this phase")
        return ensure_supplies(client, requirements)
