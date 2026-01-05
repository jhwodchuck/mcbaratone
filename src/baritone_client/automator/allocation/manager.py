"""
Resource Allocator Module
"""

import uuid
import threading
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from ...models.models import (
    ResourceSharingPolicy,
    ResourceForecast,
    AllocationRequest,
    AllocationGrant,
    ResourceReservation,
    ResourceAllocation,
    ResourceUsageAnalytics,
    ResourceAllocationState,
    AllocationConflict,
    ConflictResolutionPlan
)
from ..resource_manager import ResourceManager
from .detector import ConflictDetector
from .forecaster import ResourceForecaster
from .resolver import ConflictResolver


class ResourceAllocator:
    """
    Advanced Resource Allocator with conflict prevention and intelligent forecasting.

    Core features:
    - Priority-based allocation scheduling
    - Conflict detection and resolution
    - Resource forecasting and analytics
    - Reservation and sharing policies
    """

    def __init__(self, resource_manager: ResourceManager):
        """
        Initialize advanced resource allocator.

        Args:
            resource_manager: Underlying resource manager
        """
        self.resource_manager = resource_manager

        # Core components
        self.conflict_detector = ConflictDetector(resource_manager)
        self.forecaster = ResourceForecaster(resource_manager)
        self.conflict_resolver = ConflictResolver(resource_manager)

        # Allocation state
        self.active_allocations: Dict[str, ResourceAllocation] = {}
        self.reservations: Dict[str, ResourceReservation] = {}
        self.allocation_grants: Dict[str, AllocationGrant] = {}

        # Analytics and forecasting
        self.usage_analytics: Dict[str, ResourceUsageAnalytics] = {}
        self.forecasts: Dict[str, List[ResourceForecast]] = defaultdict(list)

        # Configuration
        self.default_sharing_policy = ResourceSharingPolicy.PRIORITY_BASED
        self.forecast_horizon_minutes = 60
        self.reservation_timeout_minutes = 30

        self._lock = threading.RLock()

    def request_allocation(
        self,
        request: AllocationRequest,
        auto_resolve_conflicts: bool = True
    ) -> Tuple[bool, Optional[AllocationGrant], List[AllocationConflict]]:
        """
        Request resource allocation with conflict detection and optional resolution.

        Args:
            request: Allocation request details
            auto_resolve_conflicts: Whether to attempt automatic conflict resolution

        Returns:
            Tuple of (success, grant, conflicts)
        """
        with self._lock:
            # Check for conflicts
            conflicts = self.conflict_detector.detect_allocation_conflicts(
                request, self.active_allocations, self.reservations
            )

            if conflicts:
                if auto_resolve_conflicts:
                    # Attempt to resolve conflicts
                    resolution_success = self._attempt_conflict_resolution(conflicts[0])
                    if resolution_success:
                        conflicts = []  # Conflicts resolved
                    else:
                        return False, None, conflicts
                else:
                    return False, None, conflicts

            # Create allocation grant
            grant = self._create_allocation_grant(request)

            # Record allocation
            allocation = ResourceAllocation(
                allocation_id=grant.allocation_id,
                mission_id=request.mission_id,
                resource_id=request.resource_id,
                quantity=grant.granted_quantity,
                allocated_at=grant.granted_at,
                expires_at=grant.expires_at,
                priority=request.priority,
                mode=request.mode,
                usage_pattern=None,  # Would be determined by usage monitoring
            )

            self.active_allocations[grant.allocation_id] = allocation
            self.allocation_grants[grant.allocation_id] = grant

            # Update analytics
            self._record_allocation_usage(request.resource_id, grant.granted_quantity, request.mission_id)

            return True, grant, []

    def create_reservation(self, request: AllocationRequest) -> Optional[ResourceReservation]:
        """
        Create a resource reservation for future use.

        Args:
            request: Reservation request details

        Returns:
            Reservation if successful, None otherwise
        """
        with self._lock:
            # Check if reservation is possible
            available = self.resource_manager.get_item_count(request.resource_id)
            conflicts = self.conflict_detector.detect_allocation_conflicts(
                request, self.active_allocations, self.reservations
            )

            if conflicts or available < request.quantity:
                return None

            reservation = ResourceReservation(
                reservation_id=str(uuid.uuid4()),
                resource_id=request.resource_id,
                quantity=request.quantity,
                reserved_by=request.mission_id,
                reserved_at=datetime.now(),
                expires_at=datetime.now() + timedelta(minutes=self.reservation_timeout_minutes),
                priority=request.priority
            )

            self.reservations[reservation.reservation_id] = reservation
            return reservation

    def release_allocation(self, allocation_id: str) -> bool:
        """
        Release an active allocation.

        Args:
            allocation_id: Allocation to release

        Returns:
            True if allocation was released
        """
        with self._lock:
            if allocation_id in self.active_allocations:
                allocation = self.active_allocations[allocation_id]

                # Record final usage for analytics
                self._record_deallocation_usage(
                    allocation.resource_id,
                    allocation.actual_usage,
                    allocation.mission_id
                )

                del self.active_allocations[allocation_id]
                if allocation_id in self.allocation_grants:
                    del self.allocation_grants[allocation_id]
                return True

        return False

    def update_allocation_usage(self, allocation_id: str, usage_delta: int):
        """
        Update usage tracking for an allocation.

        Args:
            allocation_id: Allocation to update
            usage_delta: Change in usage (positive = consumption, negative = return)
        """
        with self._lock:
            if allocation_id in self.active_allocations:
                allocation = self.active_allocations[allocation_id]
                new_usage = max(0, allocation.actual_usage + usage_delta)
                
                # Update using model_copy since model is frozen
                updated_allocation = allocation.model_copy(update={
                    "actual_usage": new_usage,
                    "last_accessed": datetime.now()
                })
                
                self.active_allocations[allocation_id] = updated_allocation

                # Update analytics
                self.forecaster.record_usage(
                    updated_allocation.resource_id,
                    usage_delta,
                    updated_allocation.mission_id
                )

    def get_resource_forecast(
        self,
        resource_id: str,
        horizon_minutes: Optional[int] = None
    ) -> Optional[ResourceForecast]:
        """
        Get current forecast for a resource.

        Args:
            resource_id: Resource to forecast
            horizon_minutes: Forecast horizon (uses default if not specified)

        Returns:
            Current forecast if available
        """
        horizon = horizon_minutes or self.forecast_horizon_minutes

        # Check cache first
        if resource_id in self.forecasts:
            cached_forecasts = self.forecasts[resource_id]
            # Return most recent forecast within horizon
            for forecast in reversed(cached_forecasts):
                if forecast.forecast_horizon == horizon:
                    # Check if forecast is still valid (not too old)
                    if (datetime.now() - forecast.timestamp).total_seconds() < 300:  # 5 minutes
                        return forecast

        # Generate new forecast
        forecast = self.forecaster.generate_forecast(resource_id, horizon)
        if forecast:
            self.forecasts[resource_id].append(forecast)
            # Keep only recent forecasts
            cutoff = datetime.now() - timedelta(hours=1)
            self.forecasts[resource_id] = [
                f for f in self.forecasts[resource_id] if f.timestamp > cutoff
            ]

        return forecast

    def get_allocation_state(self) -> ResourceAllocationState:
        """Get current allocation state snapshot."""
        with self._lock:
            return ResourceAllocationState(
                active_allocations=self.active_allocations.copy(),
                reservations=self.reservations.copy(),
                forecasts=dict(self.forecasts),
                conflicts=[],  # Computed on demand
                analytics=self.usage_analytics.copy(),
                last_sync=datetime.now()
            )

    def cleanup_expired_reservations(self):
        """Clean up expired reservations."""
        with self._lock:
            now = datetime.now()
            expired = [
                res_id for res_id, res in self.reservations.items()
                if res.expires_at < now
            ]
            for res_id in expired:
                del self.reservations[res_id]

    def _attempt_conflict_resolution(self, conflict: AllocationConflict) -> bool:
        """Attempt to resolve a conflict automatically."""
        plan = self.conflict_resolver.resolve_conflict(
            conflict, self.active_allocations
        )

        if plan:
            # If the strategy is just to delay, we haven't resolved the immediate need for allocation
            # So we should return False to indicate allocation failed (for now)
            from ...models.models import ConflictResolutionStrategy
            if plan.strategy == ConflictResolutionStrategy.MISSION_DELAY:
                return False

            # Execute resolution actions
            self._execute_resolution_actions(plan)
            return True

        return False


    def _execute_resolution_actions(self, plan: ConflictResolutionPlan):
        """Execute the actions in a resolution plan."""
        for action in plan.resolution_actions:
            action_type = action["action"]

            if action_type == "preempt_allocation":
                alloc_id = action["allocation_id"]
                self.release_allocation(alloc_id)

            elif action_type == "delay_mission":
                # Would integrate with mission coordinator to delay mission
                pass

            elif action_type == "reduce_allocation":
                # Would adjust allocation quantities
                pass

            # Additional action types would be implemented here

    def _create_allocation_grant(self, request: AllocationRequest) -> AllocationGrant:
        """Create an allocation grant for a successful request."""
        grant_quantity = request.quantity

        # Apply flexible quantity logic
        if request.flexible_quantity:
            available = self.resource_manager.get_item_count(request.resource_id)
            grant_quantity = min(request.quantity, available)

        # Calculate expiration
        expires_at = None
        if request.duration_estimate:
            expires_at = datetime.now() + timedelta(seconds=request.duration_estimate)

        return AllocationGrant(
            allocation_id=str(uuid.uuid4()),
            request=request,
            granted_quantity=grant_quantity,
            granted_at=datetime.now(),
            expires_at=expires_at,
            usage_tracking=True
        )

    def _record_allocation_usage(self, resource_id: str, quantity: int, mission_id: str):
        """Record allocation event for analytics."""
        self.forecaster.record_usage(resource_id, quantity, mission_id)
        self._update_analytics(resource_id, quantity, mission_id)

    def _record_deallocation_usage(self, resource_id: str, quantity: int, mission_id: str):
        """Record deallocation event for analytics."""
        self._update_analytics(resource_id, -quantity, mission_id)  # Negative for deallocation

    def _update_analytics(self, resource_id: str, quantity_delta: int, mission_id: str):
        """Update usage analytics for a resource."""
        now = datetime.now()

        if resource_id not in self.usage_analytics:
            self.usage_analytics[resource_id] = ResourceUsageAnalytics(
                resource_id=resource_id,
                time_window=60,  # 1 hour window
                peak_usage=0,
                average_usage=0.0,
                usage_variance=0.0,
                access_frequency=0.0,
                mission_patterns={},
                bottleneck_events=0,
                last_updated=now
            )

        analytics = self.usage_analytics[resource_id]

        # Update mission patterns (need to copy dict because model is frozen)
        new_patterns = analytics.mission_patterns.copy()
        if mission_id not in new_patterns:
            new_patterns[mission_id] = 0
        new_patterns[mission_id] += abs(quantity_delta)

        # Update usage statistics
        new_peak = max(analytics.peak_usage, abs(quantity_delta))
        
        updated_analytics = analytics.model_copy(update={
            "mission_patterns": new_patterns,
            "peak_usage": new_peak,
            "last_updated": now
        })
        
        self.usage_analytics[resource_id] = updated_analytics

