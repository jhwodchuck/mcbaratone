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
    RECIPES: Dict[str, Dict[str, Any]] = {
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
        return counts
    
    def get_item_count(self, item_id: str) -> int:
        """
        Get count of specific item in inventory.
        
        Args:
            item_id: Minecraft item ID (e.g., "minecraft:diamond")
            
        Returns:
            Count of item in inventory
        """
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
