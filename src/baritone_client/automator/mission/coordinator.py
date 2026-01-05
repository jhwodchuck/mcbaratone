"""
Mission Coordinator Module
"""

import uuid
import threading
from typing import Dict, List, Optional, Set, Any, Tuple
from datetime import datetime

from ...models.models import (
    MissionInstance, MissionStatus, MissionPriority,
    MissionRequirements, MissionDependency,
    MissionCoordinatorState, ResourceConflict, MissionEvent
)
from ...events.event_manager import EventManager
from ..resource_manager import ResourceManager
from .handler import MissionInstanceHandler


class MissionCoordinator:
    """
    Manages concurrent mission execution with resource allocation and conflict detection.

    Provides centralized coordination for multiple mission instances with:
    - Resource conflict prevention
    - Priority-based scheduling
    - Real-time state synchronization
    - Mission dependency management
    """

    def __init__(
        self,
        resource_manager: ResourceManager,
        event_manager: EventManager,
        max_concurrent_missions: int = 5
    ):
        """
        Initialize mission coordinator.

        Args:
            resource_manager: Resource manager for allocation tracking
            event_manager: Event manager for mission state broadcasting
            max_concurrent_missions: Maximum missions that can run simultaneously
        """
        self.resource_manager = resource_manager
        self.event_manager = event_manager
        self.max_concurrent_missions = max_concurrent_missions

        self._active_missions: Dict[str, MissionInstanceHandler] = {}
        self._pending_missions: List[MissionInstanceHandler] = []
        self._completed_missions: Dict[str, MissionInstanceHandler] = {}
        self._lock = threading.RLock()

        # Mission dependency tracking
        self._mission_dependencies: Dict[str, Set[str]] = {}  # mission_id -> set of blocking mission_ids

        # Event subscription for mission state updates
        self._mission_event_callback = self._handle_mission_event
        self.event_manager.subscribe(self._mission_event_callback, event_types={"mission_*"})

    def create_mission(
        self,
        name: str,
        requirements: MissionRequirements,
        priority: MissionPriority = MissionPriority.NORMAL,
        dependencies: Optional[List[MissionDependency]] = None,
        description: Optional[str] = None
    ) -> str:
        """
        Create a new mission instance.

        Args:
            name: Mission name
            requirements: Resource and dependency requirements
            priority: Mission priority
            dependencies: Optional mission dependencies
            description: Optional mission description

        Returns:
            Mission ID for the created mission
        """
        mission_id = str(uuid.uuid4())

        mission_handler = MissionInstanceHandler(
            mission_id=mission_id,
            name=name,
            requirements=requirements,
            priority=priority,
            description=description,
            event_callback=self._handle_mission_event
        )

        with self._lock:
            # Track dependencies
            if dependencies:
                blocking_missions = set()
                for dep in dependencies:
                    if dep.dependency_type == "prerequisite":
                        blocking_missions.add(dep.mission_id)
                if blocking_missions:
                    self._mission_dependencies[mission_id] = blocking_missions

            # Try to start immediately if possible
            if self._can_start_mission(mission_handler):
                self._start_mission(mission_handler)
            else:
                self._pending_missions.append(mission_handler)
                self._sort_pending_missions()

        return mission_id

    def cancel_mission(self, mission_id: str) -> bool:
        """
        Cancel a mission if it's running or pending.

        Args:
            mission_id: Mission to cancel

        Returns:
            True if mission was cancelled
        """
        with self._lock:
            # Check active missions
            if mission_id in self._active_missions:
                mission = self._active_missions[mission_id]
                mission.update_status(MissionStatus.CANCELLED)
                self._cleanup_mission(mission_id)
                return True

            # Check pending missions
            for i, mission in enumerate(self._pending_missions):
                if mission.instance_model.id == mission_id:
                    mission.update_status(MissionStatus.CANCELLED)
                    self._pending_missions.pop(i)
                    return True

        return False

    def get_mission_status(self, mission_id: str) -> Optional[MissionInstance]:
        """
        Get current status of a mission.

        Args:
            mission_id: Mission ID to query

        Returns:
            Mission instance if found, None otherwise
        """
        with self._lock:
            if mission_id in self._active_missions:
                return self._active_missions[mission_id].get_snapshot()
            elif mission_id in self._completed_missions:
                return self._completed_missions[mission_id].get_snapshot()
            else:
                for mission in self._pending_missions:
                    if mission.instance_model.id == mission_id:
                        return mission.get_snapshot()
        return None

    def list_missions(self, status_filter: Optional[MissionStatus] = None) -> List[MissionInstance]:
        """
        List all missions, optionally filtered by status.

        Args:
            status_filter: Optional status to filter by

        Returns:
            List of mission instances
        """
        missions = []

        with self._lock:
            # Active missions
            for mission in self._active_missions.values():
                if not status_filter or mission.state.status == status_filter:
                    missions.append(mission.get_snapshot())

            # Pending missions
            for mission in self._pending_missions:
                if not status_filter or mission.state.status == status_filter:
                    missions.append(mission.get_snapshot())

            # Completed missions
            for mission in self._completed_missions.values():
                if not status_filter or mission.state.status == status_filter:
                    missions.append(mission.get_snapshot())

        return missions

    def check_resource_conflicts(self, mission: MissionInstanceHandler) -> List[ResourceConflict]:
        """
        Check for resource conflicts with currently running missions.

        Args:
            mission: Mission to check conflicts for

        Returns:
            List of resource conflicts
        """
        conflicts = []
        available_resources = self.resource_manager.get_inventory_snapshot()

        with self._lock:
            # Account for resources already allocated to active missions
            for active_mission in self._active_missions.values():
                for resource_id in active_mission.get_allocated_resources():
                    if resource_id in available_resources:
                        available_resources[resource_id] -= mission.requirements.resources.get(resource_id, 0)

            # Check conflicts for exclusive resources
            for exclusive_resource in mission.requirements.exclusive_resources:
                if exclusive_resource in available_resources and available_resources[exclusive_resource] > 0:
                    # Find which missions are using this resource
                    conflicting_missions = []
                    for active_mission in self._active_missions.values():
                        if exclusive_resource in active_mission.get_allocated_resources():
                            conflicting_missions.append(active_mission.instance_model.id)

                    if conflicting_missions:
                        conflicts.append(ResourceConflict(
                            resource_id=exclusive_resource,
                            required_quantity=1,  # Exclusive resources need full ownership
                            available_quantity=0,
                            conflicting_missions=conflicting_missions
                        ))

            # Check regular resource conflicts
            for resource_id, required_qty in mission.requirements.resources.items():
                available_qty = available_resources.get(resource_id, 0)
                if available_qty < required_qty:
                    # Find missions using this resource
                    conflicting_missions = []
                    for active_mission in self._active_missions.values():
                        if resource_id in active_mission.get_allocated_resources():
                            conflicting_missions.append(active_mission.instance_model.id)

                    conflicts.append(ResourceConflict(
                        resource_id=resource_id,
                        required_quantity=required_qty,
                        available_quantity=available_qty,
                        conflicting_missions=conflicting_missions
                    ))

        return conflicts

    def _can_start_mission(self, mission: MissionInstanceHandler) -> bool:
        """Check if a mission can start based on resources and dependencies."""
        # Check concurrent mission limit
        if len(self._active_missions) >= self.max_concurrent_missions:
            return False

        # Check dependencies
        mission_id = mission.instance_model.id
        if mission_id in self._mission_dependencies:
            for blocking_id in self._mission_dependencies[mission_id]:
                if blocking_id not in self._completed_missions:
                    return False

        # Check resource availability
        conflicts = self.check_resource_conflicts(mission)
        return len(conflicts) == 0

    def _start_mission(self, mission: MissionInstanceHandler) -> None:
        """Start a mission and allocate its resources."""
        mission_id = mission.instance_model.id
        self._active_missions[mission_id] = mission

        # Allocate resources
        for resource_id, quantity in mission.requirements.resources.items():
            mission.allocate_resource(resource_id)

        # Update status
        mission.update_status(MissionStatus.RUNNING)

        # Emit start event
        self._publish_coordinator_event("mission_started", mission_id, {
            'allocated_resources': list(mission.requirements.resources.keys())
        })

    def _sort_pending_missions(self) -> None:
        """Sort pending missions by priority."""
        priority_order = {
            MissionPriority.CRITICAL: 0,
            MissionPriority.HIGH: 1,
            MissionPriority.NORMAL: 2,
            MissionPriority.LOW: 3,
            MissionPriority.IDLE: 4
        }

        self._pending_missions.sort(key=lambda m: priority_order[m.instance_model.priority])

    def _handle_mission_event(self, event: Any) -> None:
        """Handle mission state change events."""
        if not hasattr(event, 'type') or not hasattr(event, 'data'):
            return

        event_type = event.type
        data = event.data

        if 'mission_id' not in data:
            return

        mission_id = data['mission_id']

        with self._lock:
            if mission_id in self._active_missions:
                mission = self._active_missions[mission_id]

                if event_type in ['mission_completed', 'mission_failed', 'mission_cancelled']:
                    self._complete_mission(mission_id, event_type, data.get('error'))
                    self._try_start_pending_missions()

                elif event_type == 'mission_progress':
                    # Progress updates don't require coordinator action
                    pass

    def _complete_mission(self, mission_id: str, completion_type: str, error: Optional[str] = None) -> None:
        """Handle mission completion."""
        if mission_id not in self._active_missions:
            return

        mission = self._active_missions[mission_id]

        # Update status based on completion type
        if completion_type == 'mission_completed':
            mission.update_status(MissionStatus.COMPLETED)
        elif completion_type == 'mission_failed':
            mission.update_status(MissionStatus.FAILED, error=error)
        elif completion_type == 'mission_cancelled':
            mission.update_status(MissionStatus.CANCELLED)

        # Move to completed
        self._completed_missions[mission_id] = mission
        del self._active_missions[mission_id]

        # Deallocate resources
        mission.cleanup()

        # Publish completion event
        self._publish_coordinator_event("mission_completed", mission_id, {
            'completion_type': completion_type,
            'error': error
        })

    def _cleanup_mission(self, mission_id: str) -> None:
        """Clean up mission resources and remove from tracking."""
        if mission_id in self._active_missions:
            mission = self._active_missions[mission_id]
            mission.cleanup()
            del self._active_missions[mission_id]

        if mission_id in self._mission_dependencies:
            del self._mission_dependencies[mission_id]

    def _try_start_pending_missions(self) -> None:
        """Try to start pending missions that can now run."""
        missions_to_start = []

        for mission in self._pending_missions[:]:
            if self._can_start_mission(mission):
                missions_to_start.append(mission)
                self._pending_missions.remove(mission)

        for mission in missions_to_start:
            self._start_mission(mission)

    def _publish_coordinator_event(self, event_type: str, mission_id: str, data: Dict[str, Any]) -> None:
        """Publish a coordinator-level event."""
        coordinator_event = MissionEvent(
            event_type=f"coordinator_{event_type}",
            mission_id=mission_id,
            timestamp=datetime.now(),
            data=data
        )

        # Publish via event manager
        self.event_manager.publish_event(
            event_type=f"coordinator_{event_type}",
            data={'mission_id': mission_id, **data}
        )

    def get_coordinator_state(self) -> MissionCoordinatorState:
        """Get current coordinator state snapshot."""
        with self._lock:
            return MissionCoordinatorState(
                active_missions={
                    mid: handler.get_snapshot()
                    for mid, handler in self._active_missions.items()
                },
                pending_missions=[
                    handler.get_snapshot() for handler in self._pending_missions
                ],
                resource_allocations={
                    mid: list(handler.get_allocated_resources())
                    for mid, handler in self._active_missions.items()
                },
                conflicts=[],  # Could be computed on demand
                last_sync=datetime.now()
            )

    def shutdown(self) -> None:
        """Shutdown the mission coordinator."""
        with self._lock:
            # Cancel all active missions
            for mission_id in list(self._active_missions.keys()):
                self.cancel_mission(mission_id)

            # Clear pending missions
            for mission in self._pending_missions:
                mission.update_status(MissionStatus.CANCELLED)

            self._pending_missions.clear()
            self._completed_missions.clear()
            self._mission_dependencies.clear()

        # Unsubscribe from events
        if hasattr(self, '_mission_event_callback'):
            self.event_manager.unsubscribe(self._mission_event_callback)

    # Advanced Resource Allocation - Phase 3 Extensions

    def enable_advanced_allocation(self, enable: bool = True) -> None:
        """
        Enable advanced resource allocation for conflict prevention.

        Args:
            enable: Whether to enable advanced features
        """
        self.resource_manager.enable_advanced_allocation(enable)

    def request_mission_resources(
        self,
        mission_id: str,
        resource_requirements: Dict[str, int],
        priority: int = 0,
        duration_estimate: Optional[int] = None,
        flexible_requirements: bool = False
    ) -> Tuple[bool, Optional[dict], List[dict]]:
        """
        Request resource allocation for a mission using advanced allocation.

        Args:
            mission_id: Mission requesting resources
            resource_requirements: Dict of resource_id -> quantity
            priority: Allocation priority
            duration_estimate: Estimated usage duration in seconds
            flexible_requirements: Can allocate less than requested

        Returns:
            Tuple of (success, grant_info, conflicts)
        """
        if not hasattr(self.resource_manager, 'request_advanced_allocation'):
            # Fallback to basic allocation
            return self._basic_resource_request(mission_id, resource_requirements)

        # Convert requirements to individual allocation requests
        alternatives = []
        for resource_id, quantity in resource_requirements.items():
            success, grant, conflicts = self.resource_manager.request_advanced_allocation(
                resource_id=resource_id,
                quantity=quantity,
                mission_id=mission_id,
                priority=priority,
                duration_estimate=duration_estimate,
                flexible_quantity=flexible_requirements,
                alternatives=alternatives
            )

            if not success:
                return False, None, conflicts

        # All allocations successful
        return True, {"mission_id": mission_id, "allocated_resources": list(resource_requirements.keys())}, []

    def check_resource_conflicts_advanced(
        self,
        mission_id: str,
        resource_requirements: Dict[str, int]
    ) -> List[dict]:
        """
        Check for resource conflicts using advanced allocation system.

        Args:
            mission_id: Mission to check conflicts for
            resource_requirements: Required resources

        Returns:
            List of conflict information
        """
        conflicts = []

        # Try allocation to detect conflicts
        for resource_id, quantity in resource_requirements.items():
            success, _, conflict_list = self.resource_manager.request_advanced_allocation(
                resource_id=resource_id,
                quantity=quantity,
                mission_id=mission_id,
                priority=0,
                duration_estimate=None,
                flexible_quantity=False,
                alternatives=[]
            )

            if not success:
                conflicts.extend(conflict_list)

        return conflicts

    def get_resource_forecasts(self, resource_ids: List[str], horizon_minutes: int = 60) -> Dict[str, Optional[dict]]:
        """
        Get resource forecasts for planning purposes.

        Args:
            resource_ids: Resources to forecast
            horizon_minutes: Forecast horizon

        Returns:
            Dict of resource_id -> forecast_info
        """
        forecasts = {}

        if hasattr(self.resource_manager, 'get_resource_forecast'):
            for resource_id in resource_ids:
                forecast = self.resource_manager.get_resource_forecast(resource_id, horizon_minutes)
                forecasts[resource_id] = forecast

        return forecasts

    def optimize_mission_schedule(self) -> Dict[str, Any]:
        """
        Optimize mission scheduling based on resource availability and forecasts.

        Returns:
            Optimization recommendations
        """
        recommendations = {
            'can_start_immediately': [],
            'should_delay': [],
            'resource_bottlenecks': [],
            'forecast_insights': {}
        }

        with self._lock:
            # Check which pending missions can start
            for mission in self._pending_missions[:]:
                conflicts = self.check_resource_conflicts_advanced(
                    mission.instance_model.id,
                    mission.requirements.resources
                )

                if not conflicts:
                    recommendations['can_start_immediately'].append(mission.instance_model.id)
                else:
                    recommendations['should_delay'].append({
                        'mission_id': mission.instance_model.id,
                        'conflicts': len(conflicts),
                        'blocking_resources': [c['resource_id'] for c in conflicts]
                    })

            # Identify resource bottlenecks
            active_resources = set()
            for mission in self._active_missions.values():
                active_resources.update(mission.requirements.resources.keys())

            for resource_id in active_resources:
                forecast = self.get_resource_forecasts([resource_id], 30).get(resource_id)
                if forecast and forecast.get('predicted_demand', 0) > forecast.get('predicted_supply', 0):
                    recommendations['resource_bottlenecks'].append({
                        'resource_id': resource_id,
                        'demand': forecast['predicted_demand'],
                        'supply': forecast['predicted_supply'],
                        'confidence': forecast.get('confidence_level', 0.0)
                    })

            # Get forecast insights
            all_resources = set()
            for mission in self._active_missions.values():
                all_resources.update(mission.requirements.resources.keys())
            for mission in self._pending_missions:
                all_resources.update(mission.requirements.resources.keys())

            recommendations['forecast_insights'] = self.get_resource_forecasts(list(all_resources))

        return recommendations

    def get_allocation_analytics(self) -> Dict[str, Any]:
        """
        Get comprehensive allocation analytics for mission coordination.

        Returns:
            Analytics data including usage patterns and conflicts
        """
        if hasattr(self.resource_manager, 'get_allocation_analytics'):
            return self.resource_manager.get_allocation_analytics()

        # Basic analytics fallback
        return {
            'advanced_allocation_enabled': False,
            'active_missions': len(self._active_missions),
            'pending_missions': len(self._pending_missions),
            'total_missions': len(self._active_missions) + len(self._pending_missions) + len(self._completed_missions)
        }

    def _basic_resource_request(
        self,
        mission_id: str,
        resource_requirements: Dict[str, int]
    ) -> Tuple[bool, Optional[dict], List[dict]]:
        """
        Basic resource request fallback when advanced allocation is disabled.

        Args:
            mission_id: Mission requesting resources
            resource_requirements: Required resources

        Returns:
            Basic allocation result
        """
        # Check if resources are available
        conflicts = []
        for resource_id, quantity in resource_requirements.items():
            available = self.resource_manager.get_item_count(resource_id)
            if available < quantity:
                conflicts.append({
                    'conflict_id': f'basic_{resource_id}',
                    'resource_id': resource_id,
                    'required_quantity': quantity,
                    'available_quantity': available,
                    'blocking_missions': [],
                    'resolution_candidates': ['mission_delay', 'resource_gathering']
                })

        if conflicts:
            return False, None, conflicts

        # Reserve resources
        all_reserved = True
        for resource_id, quantity in resource_requirements.items():
            if not self.resource_manager.reserve_resource(resource_id, quantity):
                all_reserved = False
                break

        if all_reserved:
            return True, {"mission_id": mission_id, "allocated_resources": list(resource_requirements.keys())}, []
        else:
            return False, None, [{
                'conflict_id': 'reservation_failed',
                'resource_id': 'multiple',
                'required_quantity': 0,
                'available_quantity': 0,
                'blocking_missions': [],
                'resolution_candidates': ['mission_delay']
            }]
