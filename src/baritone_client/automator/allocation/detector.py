"""
Conflict Detector Module
"""

import uuid
import threading
from typing import Dict, List
from datetime import datetime, timedelta

from baritone_client.models.models import (
    AllocationMode,
    AllocationRequest,
    ResourceReservation,
    ConflictResolutionStrategy,
    ResourceAllocation,
    AllocationConflict
)
from baritone_client.automator.resource_manager import ResourceManager



class ConflictDetector:
    """
    Advanced conflict detection algorithms for resource allocation.

    Implements multiple detection strategies:
    - Temporal conflict analysis
    - Priority-based conflict assessment
    - Resource sharing policy validation
    - Dependency chain conflict resolution
    """

    def __init__(self, resource_manager: ResourceManager):
        """
        Initialize conflict detector.

        Args:
            resource_manager: Resource manager for inventory access
        """
        self.resource_manager = resource_manager
        self._lock = threading.RLock()

    def detect_allocation_conflicts(
        self,
        request: AllocationRequest,
        active_allocations: Dict[str, ResourceAllocation],
        reservations: Dict[str, ResourceReservation]
    ) -> List[AllocationConflict]:
        """
        Detect all conflicts for an allocation request.

        Args:
            request: Allocation request to check
            active_allocations: Currently active allocations
            reservations: Active reservations

        Returns:
            List of detected conflicts
        """
        conflicts = []

        with self._lock:
            # Check against active allocations
            allocation_conflicts = self._check_active_allocation_conflicts(
                request, active_allocations
            )
            conflicts.extend(allocation_conflicts)

            # Check against reservations
            reservation_conflicts = self._check_reservation_conflicts(
                request, reservations
            )
            conflicts.extend(reservation_conflicts)

            # Check temporal conflicts
            temporal_conflicts = self._check_temporal_conflicts(request, active_allocations)
            conflicts.extend(temporal_conflicts)

        return conflicts

    def _check_active_allocation_conflicts(
        self,
        request: AllocationRequest,
        active_allocations: Dict[str, ResourceAllocation]
    ) -> List[AllocationConflict]:
        """Check conflicts with currently active allocations."""
        conflicts = []
        available_quantity = self.resource_manager.get_item_count(request.resource_id)

        # Subtract quantities from active allocations
        for allocation in active_allocations.values():
            if allocation.resource_id == request.resource_id:
                if allocation.mode == AllocationMode.EXCLUSIVE:
                    # Exclusive allocation blocks all others
                    available_quantity = 0
                else:
                    # Shared allocation reduces available quantity
                    available_quantity -= allocation.quantity

        if available_quantity < request.quantity:
            # Find blocking allocations
            blocking_allocations = [
                alloc.allocation_id
                for alloc in active_allocations.values()
                if alloc.resource_id == request.resource_id
            ]

            blocking_missions = [
                alloc.mission_id
                for alloc in active_allocations.values()
                if alloc.resource_id == request.resource_id
            ]

            conflict = AllocationConflict(

                conflict_id=str(uuid.uuid4()),
                resource_id=request.resource_id,
                requesting_mission=request.mission_id,
                requested_quantity=request.quantity,
                available_quantity=max(0, available_quantity),
                blocking_allocations=blocking_allocations,
                blocking_missions=blocking_missions,
                resolution_candidates=self._get_resolution_candidates(request, blocking_allocations)
            )
            conflicts.append(conflict)

        return conflicts

    def _check_reservation_conflicts(
        self,
        request: AllocationRequest,
        reservations: Dict[str, ResourceReservation]
    ) -> List[AllocationConflict]:
        """Check conflicts with active reservations."""
        conflicts = []
        available_quantity = self.resource_manager.get_item_count(request.resource_id)

        # Subtract quantities from reservations
        for reservation in reservations.values():
            if reservation.resource_id == request.resource_id and reservation.expires_at > datetime.now():
                available_quantity -= reservation.quantity


        if available_quantity < request.quantity:
            blocking_reservations = [
                res.reservation_id
                for res in reservations.values()
                if res.resource_id == request.resource_id and res.expires_at > datetime.now()
            ]

            # Only report conflict if there are actual blocking reservations
            # Otherwise this is just a base inventory shortage covered by active allocation check
            if not blocking_reservations:
                return []

            blocking_missions = [
                res.reserved_by
                for res in reservations.values()
                if res.resource_id == request.resource_id and res.expires_at > datetime.now()
            ]

            conflict = AllocationConflict(
                conflict_id=str(uuid.uuid4()),
                resource_id=request.resource_id,
                requesting_mission=request.mission_id,
                requested_quantity=request.quantity,
                available_quantity=max(0, available_quantity),
                blocking_allocations=[],  # Reservations aren't allocations
                blocking_missions=blocking_missions,
                resolution_candidates=[ConflictResolutionStrategy.MISSION_DELAY]
            )
            conflicts.append(conflict)

        return conflicts


    def _check_temporal_conflicts(
        self,
        request: AllocationRequest,
        active_allocations: Dict[str, ResourceAllocation]
    ) -> List[AllocationConflict]:
        """Check for temporal conflicts based on estimated durations."""
        conflicts = []

        if not request.duration_estimate:
            return conflicts

        # Find allocations that would overlap temporally
        request_end = datetime.now() + timedelta(seconds=request.duration_estimate)

        for allocation in active_allocations.values():
            if allocation.resource_id == request.resource_id and allocation.expires_at:
                if allocation.expires_at > datetime.now() and allocation.expires_at < request_end:
                    # Temporal overlap detected
                    conflict = AllocationConflict(
                        conflict_id=str(uuid.uuid4()),
                        resource_id=request.resource_id,
                        requesting_mission=request.mission_id,
                        requested_quantity=request.quantity,
                        available_quantity=0,  # Temporal conflict
                        blocking_allocations=[allocation.allocation_id],
                        blocking_missions=[allocation.mission_id],
                        resolution_candidates=[
                            ConflictResolutionStrategy.TIME_NEGOTIATION,
                            ConflictResolutionStrategy.MISSION_DELAY
                        ]
                    )
                    conflicts.append(conflict)

        return conflicts

    def _get_resolution_candidates(
        self,
        request: AllocationRequest,
        blocking_allocations: List[str]
    ) -> List[ConflictResolutionStrategy]:
        """Get candidate resolution strategies for a conflict."""
        candidates = []

        if request.priority > 0:
            candidates.append(ConflictResolutionStrategy.PRIORITY_PREEMPTION)

        if request.alternatives:
            candidates.append(ConflictResolutionStrategy.RESOURCE_SUBSTITUTION)

        if request.flexible_quantity:
            candidates.append(ConflictResolutionStrategy.QUOTA_ADJUSTMENT)

        candidates.append(ConflictResolutionStrategy.MISSION_DELAY)
        candidates.append(ConflictResolutionStrategy.ALLOCATION_SCALING)

        return candidates
