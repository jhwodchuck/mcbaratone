
import asyncio
import logging
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Dict, List, Callable, Any, Optional
from datetime import datetime
from enum import Enum, IntEnum, auto
from dataclasses import dataclass, field
from typing import Dict, List, Callable, Any, Optional

logger = logging.getLogger(__name__)

class EventType(Enum):
    """Types of events that can be broadcast within the system."""
    # Resource Events
    RESOURCE_ACQUIRED = "resource_acquired"
    RESOURCE_LOW = "resource_low"
    INVENTORY_FULL = "inventory_full"
    
    # Task/Phase Events
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    PHASE_CHANGE = "phase_change"
    
    # Vital Events
    HEALTH_CRITICAL = "health_critical"
    HUNGER_CRITICAL = "hunger_critical"
    PLAYER_DEATH = "player_death"
    
    # System Events
    ERROR = "error"
    SYSTEM_SHUTDOWN = "system_shutdown"

class SystemPriority(IntEnum):
    """Priority levels for systems and requests."""
    CRITICAL = 10
    HIGH = 8
    MEDIUM = 5
    LOW = 2
    BACKGROUND = 0

@dataclass
class SystemEvent:
    """Data class representing a single event in the system."""
    event_type: EventType
    source: str
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class ResourceRequest:
    """Represents a request for resources."""
    resource_type: str
    quantity: int
    priority: SystemPriority
    requester: str
    timeout: float = 5.0
    created_at: datetime = field(default_factory=datetime.now)

@dataclass
class ResourceAllocation:
    """Represents an allocation of resources to a system."""
    resource_type: str
    quantity: int
    owner: str
    allocated_at: datetime = field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None

class CoordinationHub:
    """
    Central hub for managing events, resource coordination, and system interactions.
    Allows decoupling of components via a publish-subscribe pattern.
    """
    
    def __init__(self):
        # List of tuples (priority, callback)
        self._subscribers: Dict[EventType, List[tuple]] = {}
        # Map of resource_name -> quantity_locked
        self._resource_locks: Dict[str, int] = {}
        self.logger = logging.getLogger(__name__)

    def subscribe(self, event_type: EventType, callback: Callable[[SystemEvent], Any], priority: int = 0) -> None:
        """
        Subscribe a callback function to a specific event type.
        
        Args:
            event_type: The EventType to listen for.
            callback: The function to call when the event occurs.
            priority: Higher numbers run first. Default 0.
        """
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
            
        # Remove existing if present to update priority
        self.unsubscribe(event_type, callback)
        
        self._subscribers[event_type].append((priority, callback))
        # Sort by priority desc
        self._subscribers[event_type].sort(key=lambda x: x[0], reverse=True)
        
        self.logger.debug(f"Subscribed to {event_type.value} with priority {priority}")

    def unsubscribe(self, event_type: EventType, callback: Callable[[SystemEvent], Any]) -> None:
        """
        Unsubscribe a callback from a specific event type.
        """
        if event_type in self._subscribers:
            # Filter out tuples where the second element is the callback
            self._subscribers[event_type] = [
                sub for sub in self._subscribers[event_type] 
                if sub[1] != callback
            ]
            self.logger.debug(f"Unsubscribed from {event_type.value}")

    def broadcast(self, event: SystemEvent) -> None:
        """
        Broadcast an event to all subscribers.
        
        Args:
            event: The SystemEvent to broadcast.
        """
        self.logger.info(f"Broadcasting event: {event.event_type.value} from {event.source}")
        
        if event.event_type not in self._subscribers:
            return

        for priority, callback in self._subscribers[event.event_type]:
            try:
                if asyncio.iscoroutinefunction(callback):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(callback(event))
                    except RuntimeError:
                        self.logger.warning(f"Async callback for {event.event_type.value} skipped (no event loop)")
                else:
                    callback(event)
            except Exception as e:
                self.logger.error(f"Error in event handler for {event.event_type.value}: {e}")

    def request_resources(self, request: ResourceRequest, current_inventory: Dict[str, int]) -> Optional[ResourceAllocation]:
        """
        Request resources for a system.
        
        Args:
            request: The resource request details.
            current_inventory: Current snapshot of available resources (e.g. from ResourceManager).
            
        Returns:
            ResourceAllocation if successful, None otherwise.
        """
        available = current_inventory.get(request.resource_type, 0)
        locked = self._resource_locks.get(request.resource_type, 0)
        
        free_amount = available - locked
        
        if free_amount >= request.quantity:
            # Grant allocation
            if request.resource_type not in self._resource_locks:
                self._resource_locks[request.resource_type] = 0
            self._resource_locks[request.resource_type] += request.quantity
            
            expiration = datetime.now().timestamp() + request.timeout if request.timeout else None
            expires_dt = datetime.fromtimestamp(expiration) if expiration else None
            
            allocation = ResourceAllocation(
                resource_type=request.resource_type,
                quantity=request.quantity,
                owner=request.requester,
                expires_at=expires_dt
            )
            
            self.logger.info(f"Allocated {request.quantity} {request.resource_type} to {request.requester}")
            return allocation
        
        self.logger.info(f"Denied request for {request.quantity} {request.resource_type} from {request.requester} (Free: {free_amount})")
        return None

    def release_resources(self, allocation: ResourceAllocation) -> None:
        """Release previously allocated resources."""
        if allocation.resource_type in self._resource_locks:
            self._resource_locks[allocation.resource_type] -= allocation.quantity
            if self._resource_locks[allocation.resource_type] <= 0:
                del self._resource_locks[allocation.resource_type]
            self.logger.info(f"Released {allocation.quantity} {allocation.resource_type} from {allocation.owner}")
