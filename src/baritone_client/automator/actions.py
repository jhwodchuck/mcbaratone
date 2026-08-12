"""
Actions Module - Structured action abstraction for Minecraft automation.

Provides:
- Action base class for all bot actions
- Specific action implementations (Mine, Craft, Goto)
- ActionOptimizer for batching and reordering
- PhaseCondition for weighted phase transitions
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..common.combat_action import dispatch_held_item_use

logger = logging.getLogger(__name__)


class ActionStatus(Enum):
    """Status of an action execution."""
    PENDING = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()


@dataclass
class ActionResult:
    """Result of an action execution."""
    success: bool
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def ok(cls, message: str = "Success", **data) -> "ActionResult":
        return cls(success=True, message=message, data=data)
    
    @classmethod
    def fail(cls, message: str = "Failed", **data) -> "ActionResult":
        return cls(success=False, message=message, data=data)


class Action(ABC):
    """
    Abstract base class for all bot actions.
    
    Actions encapsulate discrete, atomic operations that the bot can perform.
    They can be queued, optimized, and executed by the ActionExecutor.
    """
    
    def __init__(self, priority: int = 0):
        """
        Initialize action.
        
        Args:
            priority: Higher priority actions execute first (default 0).
        """
        self.priority = priority
        self.status = ActionStatus.PENDING
        self._result: Optional[ActionResult] = None
    
    @abstractmethod
    def execute(self, client) -> ActionResult:
        """
        Execute the action.
        
        Args:
            client: Baritone client instance.
            
        Returns:
            ActionResult describing success/failure.
        """
        pass
    
    @abstractmethod
    def get_name(self) -> str:
        """Get human-readable action name."""
        pass
    
    def get_estimated_duration(self) -> float:
        """Get estimated execution time in seconds."""
        return 1.0
    
    def can_batch_with(self, other: "Action") -> bool:
        """Check if this action can be batched with another."""
        return False
    
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(priority={self.priority}, status={self.status.name})"


class MineAction(Action):
    """Action to mine specific blocks."""
    
    def __init__(self, block_type: str, quantity: int = 1, priority: int = 0):
        """
        Initialize mine action.
        
        Args:
            block_type: Minecraft block ID (e.g., "minecraft:oak_log").
            quantity: Number of blocks to mine.
            priority: Action priority.
        """
        super().__init__(priority)
        self.block_type = block_type
        self.quantity = quantity
    
    def execute(self, client) -> ActionResult:
        """Execute mining via Baritone."""
        try:
            self.status = ActionStatus.RUNNING
            
            response = client.transport.dispatch("mine", {
                "block_type": self.block_type,
                "count": self.quantity
            })
            
            if response.get("started"):
                self.status = ActionStatus.COMPLETED
                return ActionResult.ok(
                    f"Mining {self.quantity} {self.block_type}",
                    block_type=self.block_type,
                    quantity=self.quantity
                )
            else:
                self.status = ActionStatus.FAILED
                return ActionResult.fail(response.get("error", "Mine failed"))
                
        except Exception as e:
            self.status = ActionStatus.FAILED
            return ActionResult.fail(str(e))
    
    def get_name(self) -> str:
        return f"Mine {self.quantity}x {self.block_type}"
    
    def get_estimated_duration(self) -> float:
        return self.quantity * 2.0  # ~2s per block
    
    def can_batch_with(self, other: Action) -> bool:
        if isinstance(other, MineAction):
            return other.block_type == self.block_type
        return False


class CraftAction(Action):
    """Action to craft items."""
    
    def __init__(self, item_id: str, quantity: int = 1, priority: int = 0):
        """
        Initialize craft action.
        
        Args:
            item_id: Minecraft item ID to craft.
            quantity: Number to craft.
            priority: Action priority.
        """
        super().__init__(priority)
        self.item_id = item_id
        self.quantity = quantity
    
    def execute(self, client) -> ActionResult:
        """Execute crafting."""
        try:
            self.status = ActionStatus.RUNNING
            
            response = client.transport.dispatch("craft", {
                "item": self.item_id,
                "count": self.quantity
            })
            
            if response.get("success") or response.get("crafted"):
                self.status = ActionStatus.COMPLETED
                return ActionResult.ok(
                    f"Crafted {self.quantity} {self.item_id}",
                    item_id=self.item_id,
                    quantity=self.quantity
                )
            else:
                self.status = ActionStatus.FAILED
                return ActionResult.fail(response.get("error", "Craft failed"))
                
        except Exception as e:
            self.status = ActionStatus.FAILED
            return ActionResult.fail(str(e))
    
    def get_name(self) -> str:
        return f"Craft {self.quantity}x {self.item_id}"
    
    def get_estimated_duration(self) -> float:
        return self.quantity * 0.5  # ~0.5s per craft
    
    def can_batch_with(self, other: Action) -> bool:
        if isinstance(other, CraftAction):
            return other.item_id == self.item_id
        return False


class GotoAction(Action):
    """Action to navigate to coordinates."""
    
    def __init__(self, x: int, y: int, z: int, priority: int = 0):
        """
        Initialize goto action.
        
        Args:
            x, y, z: Target coordinates.
            priority: Action priority.
        """
        super().__init__(priority)
        self.x = x
        self.y = y
        self.z = z
    
    def execute(self, client) -> ActionResult:
        """Execute navigation via Baritone."""
        try:
            self.status = ActionStatus.RUNNING
            
            response = client.transport.dispatch("goto", {
                "x": self.x,
                "y": self.y,
                "z": self.z
            })
            
            if response.get("started") or response.get("success"):
                self.status = ActionStatus.COMPLETED
                return ActionResult.ok(
                    f"Navigating to ({self.x}, {self.y}, {self.z})",
                    x=self.x, y=self.y, z=self.z
                )
            else:
                self.status = ActionStatus.FAILED
                return ActionResult.fail(response.get("error", "Goto failed"))
                
        except Exception as e:
            self.status = ActionStatus.FAILED
            return ActionResult.fail(str(e))
    
    def get_name(self) -> str:
        return f"Goto ({self.x}, {self.y}, {self.z})"
    
    def get_estimated_duration(self) -> float:
        return 10.0  # Variable, use default estimate


class EatAction(Action):
    """Action to eat food."""
    
    def __init__(self, item_id: str, priority: int = 10):
        """
        Initialize eat action.
        
        Args:
            item_id: Food item (e.g., "minecraft:cooked_beef")
            priority: Action priority (default high)
        """
        super().__init__(priority)
        self.item_id = item_id
    
    def execute(self, client) -> ActionResult:
        """Execute eating."""
        try:
            self.status = ActionStatus.RUNNING
            
            # Find item in inventory
            response = client.transport.dispatch("get_inventory", {})
            found_slot = -1
            
            # Check hotbar first (slots 0-8)
            # In serialized inventory, hotbar is usually present.
            # We need to find the item and its slot.
            
            # Scan inventory response structure
            # Bridge returns: "inventory": [...], "armor": [...], "offhand": [...]
            # Items have "slot" field.
            
            # Flatten search
            all_items = response.get("inventory", []) + response.get("offhand", [])
            
            target_slot = -1
            
            for item in all_items:
                if item.get("id") == self.item_id and item.get("count", 0) > 0:
                    target_slot = item.get("slot")
                    break
            
            if target_slot == -1:
                return ActionResult.fail(f"Food not found: {self.item_id}")
            
            # If in main inventory (9-35), swap to hotbar (0-8)
            # For simplicity, bridge's select_slot only works for hotbar.
            # If item is not in hotbar, we must swap it.
            # Ideally, we swap to currently selected slot.
            
            current_slot = response.get("selected_slot", 0)
            
            # Determine if we need to swap
            # Slots 0-8 are hotbar
            # Slots 9-35 are main inventory
            # Slot 40 is offhand (sometimes 45 depending on version/protocol)
            
            if 0 <= target_slot <= 8:
                # Already in hotbar, just select it
                if current_slot != target_slot:
                    client.transport.dispatch("select_slot", {"slot": target_slot})
            else:
                # Need to swap to hotbar
                # Swap target_slot with current_slot (which is in hotbar 0-8)
                # Bridge handleInventoryClick takes "slot" and "type"="SWAP" maybe?
                # Or PICKUP logic. "PICKUP" click on target, then click on hotbar slot.
                
                # Simplified: Just fail if not in hotbar for V1, or try simple swap
                # Let's try to swap: Click source, Click dest
                
                # 1. Click source (pickup)
                client.transport.dispatch("inventory_click", {
                    "slot": target_slot,
                    "type": "PICKUP"
                })
                import time
                time.sleep(0.1)
                
                # 2. Click dest (current hotbar slot)
                # Note: This swaps them.
                # Slot mapping in inventory_click might be container-based (0 is crafting output in inventory screen)
                # This is risky without robust inventory manager.
                # SAFETY FALLBACK: Only eat if in hotbar for now.
                return ActionResult.fail(f"Food {self.item_id} at slot {target_slot} must be in hotbar (0-8) to eat.")

            # Eat (use item)
            # Duration for food is usually 32 ticks (1.6s)
            dispatch_held_item_use(client, 1600)
            
            self.status = ActionStatus.COMPLETED
            return ActionResult.ok(f"Ate {self.item_id}")

        except Exception as e:
            self.status = ActionStatus.FAILED
            return ActionResult.fail(str(e))
            
    def get_name(self) -> str:
        return f"Eat {self.item_id}"
        
    def get_estimated_duration(self) -> float:
        return 1.8


class ActionOptimizer:
    """
    Optimizes a list of actions by batching and reordering.
    
    Strategies:
    - Batch similar mining actions
    - Reorder by priority
    - Group crafting actions
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def optimize(self, actions: List[Action]) -> List[Action]:
        """
        Optimize a list of actions.
        
        Args:
            actions: List of actions to optimize.
            
        Returns:
            Optimized list of actions.
        """
        if not actions:
            return []
        
        # Step 1: Sort by priority (higher first)
        sorted_actions = sorted(actions, key=lambda a: -a.priority)
        
        # Step 2: Batch similar actions
        batched = self._batch_similar(sorted_actions)
        
        self.logger.debug(f"Optimized {len(actions)} actions to {len(batched)}")
        return batched
    
    def _batch_similar(self, actions: List[Action]) -> List[Action]:
        """Batch consecutive similar actions."""
        if not actions:
            return []
        
        result = []
        current = actions[0]
        
        for next_action in actions[1:]:
            if current.can_batch_with(next_action):
                # Merge quantities for mine/craft actions
                if isinstance(current, MineAction) and isinstance(next_action, MineAction):
                    current.quantity += next_action.quantity
                elif isinstance(current, CraftAction) and isinstance(next_action, CraftAction):
                    current.quantity += next_action.quantity
            else:
                result.append(current)
                current = next_action
        
        result.append(current)
        return result
    
    def estimate_total_time(self, actions: List[Action]) -> float:
        """Estimate total execution time for actions."""
        return sum(a.get_estimated_duration() for a in actions)


@dataclass
class PhaseCondition:
    """
    A weighted condition for phase transitions.
    
    Used to determine readiness for phase advancement using fuzzy logic.
    """
    name: str
    check: Callable[[], bool]
    weight: float = 1.0
    required: bool = False
    
    def evaluate(self) -> Tuple[bool, float]:
        """
        Evaluate the condition.
        
        Returns:
            Tuple of (condition_met, weight_contributed).
        """
        met = self.check()
        return (met, self.weight if met else 0.0)


class PhaseReadinessEvaluator:
    """
    Evaluates phase readiness using weighted conditions.
    
    Uses fuzzy logic: transition if sum(met_weights) / sum(all_weights) > threshold.
    """
    
    def __init__(self, threshold: float = 0.8):
        """
        Initialize evaluator.
        
        Args:
            threshold: Minimum ratio of weights to transition (0.0 - 1.0).
        """
        self.threshold = threshold
        self.conditions: List[PhaseCondition] = []
    
    def add_condition(self, condition: PhaseCondition) -> None:
        """Add a condition to evaluate."""
        self.conditions.append(condition)
    
    def is_ready(self) -> bool:
        """
        Check if phase transition is ready.
        
        Returns:
            True if readiness score exceeds threshold.
        """
        if not self.conditions:
            return True
        
        # Check required conditions first
        for cond in self.conditions:
            if cond.required:
                met, _ = cond.evaluate()
                if not met:
                    return False
        
        # Calculate weighted score
        total_weight = sum(c.weight for c in self.conditions)
        met_weight = sum(c.evaluate()[1] for c in self.conditions)
        
        if total_weight == 0:
            return True
        
        score = met_weight / total_weight
        return score >= self.threshold
    
    def get_readiness_score(self) -> float:
        """Get current readiness score (0.0 - 1.0)."""
        if not self.conditions:
            return 1.0
        
        total_weight = sum(c.weight for c in self.conditions)
        if total_weight == 0:
            return 1.0
        
        met_weight = sum(c.evaluate()[1] for c in self.conditions)
        return met_weight / total_weight
    
    def get_missing_conditions(self) -> List[str]:
        """Get names of unmet conditions."""
        return [c.name for c in self.conditions if not c.evaluate()[0]]
