"""
Core interfaces and types for the modular architecture.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from baritone_client.core.client import Client
    from baritone_client.common.state import WorldState
    from baritone_client.automator.resource_manager import CraftingTask
    from baritone_client.automator.state_manager import Phase

@dataclass
class ActionResult:
    """Result of an action execution."""
    success: bool
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def ok(cls, message: str = "", **kwargs) -> 'ActionResult':
        return cls(True, message, kwargs)
    
    @classmethod
    def fail(cls, message: str = "", **kwargs) -> 'ActionResult':
        return cls(False, message, kwargs)


class IResourceManager(Protocol):
    """Protocol for resource management capabilities."""
    def ensure_supplies(self, requirements: Dict[str, int], **kwargs) -> Any: ...


class IResourceProvider(Protocol):
    """Contract for providing resource information."""

    def refresh_inventory(self) -> Dict[str, int]:
        """Refresh and return current inventory counts."""
        ...

    def get_item_count(self, item_id: str) -> int:
        """Get count of specific item or item group."""
        ...

    def get_inventory_snapshot(self) -> Dict[str, int]:
        """Get a snapshot of current inventory."""
        ...


class IInventoryProvider(Protocol):
    """Contract for inventory access and management."""

    def find_item_slot(self, item_id: str) -> Optional[int]:
        """Find slot containing specified item."""
        ...

    def reserve_resource(self, item_id: str, quantity: int) -> bool:
        """Attempt to reserve a resource for exclusive use."""
        ...

    def release_resource(self, item_id: str, quantity: int) -> None:
        """Release reserved resources."""
        ...


class IRequirementChecker(Protocol):
    """Contract for checking resource requirements."""

    def can_satisfy_requirement(self, item_id: str, required_quantity: int) -> bool:
        """Check if a requirement can be satisfied."""
        ...

    def has_items(self, requirements: Dict[str, int]) -> bool:
        """Check if inventory has all required items."""
        ...

    def check_phase_requirements(self, phase: 'Phase') -> Dict[str, int]:
        """Check what's missing for a phase."""
        ...


class IResourceAllocator(Protocol):
    """Contract for allocating resources to tasks."""

    def request_resources(self, item_id: str, quantity: int, priority: int = 10) -> None:
        """Register a dynamic resource request."""
        ...

    def queue_crafting(self, item_id: str, quantity: int, priority: int = 0) -> None:
        """Add item to crafting queue."""
        ...

    def get_next_crafting_task(self) -> Optional['CraftingTask']:
        """Get next item to craft from queue."""
        ...


class IPersistence(Protocol):
    """Contract for data persistence operations."""

    def save(self, key: str, data: Any) -> None:
        """Save data with the specified key."""
        ...

    def load(self, key: str) -> Any:
        """Load data for the specified key."""
        ...

    def exists(self, key: str) -> bool:
        """Check if data exists for the specified key."""
        ...


class ICheckpointManager(Protocol):
    """Contract for managing checkpoints."""

    def create_checkpoint(self, name: str, data: Any) -> None:
        """Create a checkpoint with the given name and data."""
        ...

    def restore_checkpoint(self, name: str) -> Any:
        """Restore data from the specified checkpoint."""
        ...

    def list_checkpoints(self) -> List[str]:
        """List all available checkpoint names."""
        ...


class IProgressTracker(Protocol):
    """Contract for tracking progress."""

    def update_progress(self, progress: float) -> None:
        """Update the current progress value."""
        ...

    def get_progress(self) -> float:
        """Get the current progress value."""
        ...

    def reset_progress(self) -> None:
        """Reset progress to initial state."""
        ...


@dataclass
class ActionContext:
    """Context passed to actions execution."""
    client: 'Client'
    state: 'WorldState'
    # using Any for now as ResourceManager is being refactored
    resources: Any = None 
    
    # Optional coordination hub for complex interactions
    coordination: Any = None


class IAction(ABC):
    """Abstract base class for all actions."""
    
    @abstractmethod
    def execute(self, context: ActionContext) -> ActionResult:
        """Execute the action logic."""
        pass
        
    def __call__(self, context: ActionContext) -> ActionResult:
        return self.execute(context)
