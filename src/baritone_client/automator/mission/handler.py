"""
Mission Instance Handler Module
"""

import threading
from typing import Dict, Optional, Set, Callable
from datetime import datetime

from ...models.models import (
    MissionInstance, MissionStatus, MissionPriority, MissionState,
    MissionRequirements, MissionEvent
)
from .context import MissionContext


class MissionInstanceHandler:
    """
    Isolated mission state and resource management.

    Provides encapsulation for individual mission execution with dedicated
    resource allocation and state tracking.
    """

    def __init__(
        self,
        mission_id: str,
        name: str,
        requirements: MissionRequirements,
        priority: MissionPriority = MissionPriority.NORMAL,
        description: Optional[str] = None,
        event_callback: Optional[Callable[[MissionEvent], None]] = None
    ):
        """
        Initialize mission instance handler.

        Args:
            mission_id: Unique mission identifier
            name: Human-readable mission name
            requirements: Resource and dependency requirements
            priority: Mission execution priority
            description: Optional mission description
            event_callback: Optional callback for mission events
        """
        now = datetime.now()

        self.state = MissionState(
            mission_id=mission_id,
            status=MissionStatus.PENDING,
            phase="initialized",
            progress=0.0,
            last_update=now,
            metadata={}
        )

        self.instance_model = MissionInstance(
            id=mission_id,
            name=name,
            description=description,
            priority=priority,
            requirements=requirements,
            state=self.state,
            created_at=now,
            updated_at=now
        )

        self.context = MissionContext(
            mission=self.instance_model,
            event_callback=event_callback
        )

        self._lock = threading.RLock()

    def update_status(self, status: MissionStatus, phase: Optional[str] = None, error: Optional[str] = None) -> None:
        """
        Update mission status with thread safety.

        Args:
            status: New mission status
            phase: Optional phase update
            error: Optional error message for failed status
        """
        with self._lock:
            now = datetime.now()
            self.state = MissionState(
                mission_id=self.state.mission_id,
                status=status,
                phase=phase or self.state.phase,
                progress=self.state.progress,
                start_time=self.state.start_time,
                completed_time=now if status in [MissionStatus.COMPLETED, MissionStatus.FAILED, MissionStatus.CANCELLED] else None,
                last_update=now,
                error_message=error,
                metadata=self.state.metadata
            )
            self.instance_model = self.instance_model.model_copy(update={
                'state': self.state,
                'updated_at': now
            })

            # Emit event if callback registered
            if self.context.event_callback:
                event = MissionEvent(
                    event_type=f"mission_{status.value}",
                    mission_id=self.state.mission_id,
                    timestamp=now,
                    data={
                        'phase': self.state.phase,
                        'progress': self.state.progress,
                        'error': error
                    }
                )
                self.context.event_callback(event)

    def update_progress(self, progress: float, phase: Optional[str] = None) -> None:
        """
        Update mission progress.

        Args:
            progress: Progress value (0.0 to 1.0)
            phase: Optional phase update
        """
        with self._lock:
            now = datetime.now()
            self.state = MissionState(
                mission_id=self.state.mission_id,
                status=self.state.status,
                phase=phase or self.state.phase,
                progress=max(0.0, min(1.0, progress)),
                start_time=self.state.start_time,
                completed_time=self.state.completed_time,
                last_update=now,
                error_message=self.state.error_message,
                metadata=self.state.metadata
            )
            self.instance_model = self.instance_model.model_copy(update={
                'state': self.state,
                'updated_at': now
            })

    def allocate_resource(self, resource_id: str) -> None:
        """Mark resource as allocated to this mission."""
        with self._lock:
            self.context.allocated_resources.add(resource_id)

    def deallocate_resource(self, resource_id: str) -> None:
        """Release resource allocation."""
        with self._lock:
            self.context.allocated_resources.discard(resource_id)

    def get_allocated_resources(self) -> Set[str]:
        """Get set of currently allocated resources."""
        with self._lock:
            return self.context.allocated_resources.copy()

    def can_start(self, available_resources: Dict[str, int]) -> bool:
        """
        Check if mission can start based on available resources.

        Args:
            available_resources: Dict of resource_id -> available_quantity

        Returns:
            True if mission requirements can be satisfied
        """
        for resource_id, required_qty in self.requirements.resources.items():
            available_qty = available_resources.get(resource_id, 0)
            if available_qty < required_qty:
                return False
        return True

    def get_snapshot(self) -> MissionInstance:
        """Get a snapshot of current mission state."""
        with self._lock:
            return self.instance_model.model_copy()

    def cleanup(self) -> None:
        """Clean up mission resources."""
        self.context.cleanup()
