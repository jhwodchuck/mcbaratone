"""
Conflict Resolver Module
"""

import threading
from datetime import datetime
from typing import Dict, Optional, List

from ...models.models import (
    AllocationConflict,
    ConflictResolutionStrategy,
    ConflictResolutionPlan,
    ResourceAllocation
)
from ..resource_manager import ResourceManager


class ConflictResolver:
    """
    Automatic conflict resolution with intelligent negotiation strategies.

    Implements multiple resolution approaches:
    - Priority-based preemption
    - Resource substitution
    - Time slot negotiation
    - Quota adjustment
    """

    def __init__(self, resource_manager: ResourceManager):
        """
        Initialize conflict resolver.

        Args:
            resource_manager: Resource manager for allocation operations
        """
        self.resource_manager = resource_manager
        self._lock = threading.RLock()

    def resolve_conflict(
        self,
        conflict: AllocationConflict,
        active_allocations: Dict[str, ResourceAllocation],
        strategy: Optional[ConflictResolutionStrategy] = None
    ) -> Optional[ConflictResolutionPlan]:
        """
        Resolve an allocation conflict using specified or optimal strategy.

        Args:
            conflict: Conflict to resolve
            active_allocations: Currently active allocations
            strategy: Preferred resolution strategy

        Returns:
            Resolution plan if successful, None otherwise
        """
        with self._lock:
            if not strategy:
                strategy = self._select_optimal_strategy(conflict)

            # Generate resolution actions first (since plan is frozen)
            actions = self._generate_resolution_actions(strategy, conflict, active_allocations)
            
            if actions:
                # Calculate impact based on actions
                impact = self._calculate_impact(actions, conflict)
                
                plan = ConflictResolutionPlan(
                    conflict_id=conflict.conflict_id,
                    strategy=strategy,
                    affected_missions=[conflict.requesting_mission] + conflict.blocking_missions,
                    resolution_actions=actions,
                    estimated_impact=impact,
                    created_at=datetime.now()
                )
                return plan

        return None


    def _select_optimal_strategy(self, conflict: AllocationConflict) -> ConflictResolutionStrategy:
        """Select the optimal resolution strategy for a conflict."""
        candidates = conflict.resolution_candidates

        # Priority-based selection
        if ConflictResolutionStrategy.PRIORITY_PREEMPTION in candidates:
            return ConflictResolutionStrategy.PRIORITY_PREEMPTION

        if ConflictResolutionStrategy.RESOURCE_SUBSTITUTION in candidates:
            return ConflictResolutionStrategy.RESOURCE_SUBSTITUTION

        if ConflictResolutionStrategy.TIME_NEGOTIATION in candidates:
            return ConflictResolutionStrategy.TIME_NEGOTIATION

        if ConflictResolutionStrategy.QUOTA_ADJUSTMENT in candidates:
            return ConflictResolutionStrategy.QUOTA_ADJUSTMENT

        # Default fallback
        return ConflictResolutionStrategy.MISSION_DELAY

    def _generate_resolution_actions(
        self,
        strategy: ConflictResolutionStrategy,
        conflict: AllocationConflict,
        active_allocations: Dict[str, ResourceAllocation]
    ) -> List[Dict]:
        """Generate actions for the selected resolution strategy."""
        
        if strategy == ConflictResolutionStrategy.PRIORITY_PREEMPTION:
            return self._generate_preemption_actions(conflict, active_allocations)

        elif strategy == ConflictResolutionStrategy.RESOURCE_SUBSTITUTION:
            return self._generate_substitution_actions(conflict)

        elif strategy == ConflictResolutionStrategy.TIME_NEGOTIATION:
            return self._generate_time_negotiation_actions(conflict, active_allocations)

        elif strategy == ConflictResolutionStrategy.QUOTA_ADJUSTMENT:
            return self._generate_quota_adjustment_actions(conflict, active_allocations)

        elif strategy == ConflictResolutionStrategy.MISSION_DELAY:
            return self._generate_delay_actions(conflict)

        elif strategy == ConflictResolutionStrategy.ALLOCATION_SCALING:
            return self._generate_scaling_actions(conflict, active_allocations)

        return []


    def _generate_preemption_actions(
        self,
        conflict: AllocationConflict,
        active_allocations: Dict[str, ResourceAllocation]
    ) -> List[Dict]:
        """Generate priority-based preemption actions."""
        # Find lower priority allocations to preempt
        to_preempt = []
        for alloc_id in conflict.blocking_allocations:
            if alloc_id in active_allocations:
                allocation = active_allocations[alloc_id]
                # Assume requesting mission has higher priority (this would be checked elsewhere)
                to_preempt.append(allocation)

        if to_preempt:
            return [
                {
                    "action": "preempt_allocation",
                    "allocation_id": alloc.allocation_id,
                    "mission_id": alloc.mission_id,
                    "reason": "higher_priority_request"
                }
                for alloc in to_preempt
            ]

        return []


    def _generate_substitution_actions(
        self,
        conflict: AllocationConflict
    ) -> List[Dict]:
        """Generate resource substitution actions."""
        # This would require more context about available alternatives
        # Simplified implementation
        return [{
            "action": "use_alternative_resource",
            "original_resource": conflict.resource_id,
            "reason": "conflict_resolution"
        }]


    def _generate_time_negotiation_actions(
        self,
        conflict: AllocationConflict,
        active_allocations: Dict[str, ResourceAllocation]
    ) -> List[Dict]:
        """Generate time-based negotiation actions."""
        # Find allocation that will complete soonest
        earliest_completion = None
        for alloc_id in conflict.blocking_allocations:
            if alloc_id in active_allocations:
                allocation = active_allocations[alloc_id]
                if allocation.expires_at and (not earliest_completion or allocation.expires_at < earliest_completion):
                    earliest_completion = allocation.expires_at

        if earliest_completion:
            delay_minutes = (earliest_completion - datetime.now()).total_seconds() / 60
            return [{
                "action": "delay_mission",
                "mission_id": conflict.requesting_mission,
                "delay_minutes": max(1, int(delay_minutes)),
                "reason": "await_resource_availability"
            }]

        return []


    def _generate_quota_adjustment_actions(
        self,
        conflict: AllocationConflict,
        active_allocations: Dict[str, ResourceAllocation]
    ) -> List[Dict]:
        """Generate quota adjustment actions."""
        available_qty = conflict.available_quantity
        if available_qty > 0:
            return [{
                "action": "reduce_allocation",
                "mission_id": conflict.requesting_mission,
                "resource_id": conflict.resource_id,
                "original_quantity": conflict.requested_quantity,
                "adjusted_quantity": available_qty,
                "reason": "resource_sharing"
            }]

        return []


    def _generate_delay_actions(
        self,
        conflict: AllocationConflict
    ) -> List[Dict]:
        """Generate mission delay actions."""
        return [{
            "action": "delay_mission",
            "mission_id": conflict.requesting_mission,
            "delay_minutes": 5,  # Default delay
            "reason": "resource_conflict"
        }]


    def _generate_scaling_actions(
        self,
        conflict: AllocationConflict,
        active_allocations: Dict[str, ResourceAllocation]
    ) -> List[Dict]:
        """Generate allocation scaling actions."""
        # Proportional reduction of all allocations
        total_requested = sum(
            active_allocations[alloc_id].quantity
            for alloc_id in conflict.blocking_allocations
            if alloc_id in active_allocations
        ) + conflict.requested_quantity

        available = self.resource_manager.get_item_count(conflict.resource_id)
        scale_factor = available / total_requested if total_requested > 0 else 1.0

        actions = []
        for alloc_id in conflict.blocking_allocations:
            if alloc_id in active_allocations:
                allocation = active_allocations[alloc_id]
                new_quantity = int(allocation.quantity * scale_factor)
                actions.append({
                    "action": "scale_allocation",
                    "allocation_id": alloc_id,
                    "mission_id": allocation.mission_id,
                    "original_quantity": allocation.quantity,
                    "scaled_quantity": new_quantity,
                    "reason": "proportional_sharing"
                })

        actions.append({
            "action": "grant_scaled_allocation",
            "mission_id": conflict.requesting_mission,
            "resource_id": conflict.resource_id,
            "quantity": int(conflict.requested_quantity * scale_factor),
            "reason": "proportional_sharing"
        })

        return actions


    def _calculate_impact(self, actions: List[Dict], conflict: AllocationConflict) -> Dict[str, float]:
        """Calculate estimated impact of resolution actions."""
        impact = {}

        for action in actions:
            mission_id = action.get("mission_id", conflict.requesting_mission)
            if action["action"] == "delay_mission":
                impact[mission_id] = action.get("delay_minutes", 5)
            elif action["action"] in ["reduce_allocation", "scale_allocation"]:
                # Estimate impact based on quantity reduction
                original = action.get("original_quantity", 1)
                adjusted = action.get("scaled_quantity", 0)
                if original > 0:
                    impact[mission_id] = (original - adjusted) / original  # Fraction lost
            else:
                impact[mission_id] = 0.1  # Default low impact

        return impact

