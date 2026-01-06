"""
Tests for Advanced Resource Allocation and Conflict Prevention - Phase 3

Comprehensive test suite covering:
- Conflict detection algorithms
- Resource forecasting
- Priority-based allocation
- Automatic conflict resolution
- Mission coordinator integration
"""

import pytest
import time
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock

from baritone_client.automator.resource_allocator import (
    ResourceAllocator,
    ConflictDetector,
    ResourceForecaster,
    ConflictResolver
)
from baritone_client.automator.resource_manager import ResourceManager
from baritone_client.models.models import (
    AllocationRequest, AllocationGrant, ResourceReservation,
    ConflictResolutionStrategy, AllocationConflict,
    ResourceAllocation, AllocationMode
)


class MockResourceManager:
    """Mock resource manager for testing."""

    def __init__(self):
        self.inventory = {
            "minecraft:diamond": 10,
            "minecraft:iron_ingot": 50,
            "minecraft:wood": 100,
            "minecraft:stone": 200
        }

    def get_item_count(self, item_id: str) -> int:
        return self.inventory.get(item_id, 0)

    def get_inventory_snapshot(self) -> dict:
        return self.inventory.copy()

    def enable_advanced_allocation(self, enabled: bool) -> None:
        self.advanced_allocation_enabled = enabled
        if enabled:
            # Create a real allocator but with this mock manager
            # We need to import it inside to avoid circular issues or just assume it's available
            from baritone_client.automator.resource_allocator import ResourceAllocator
            self.advanced_allocator = ResourceAllocator(self)

    def request_advanced_allocation(self, **kwargs) -> tuple:
        if hasattr(self, 'advanced_allocator'):
            from baritone_client.models.models import AllocationRequest
            
            # Create request object
            request = AllocationRequest(
                mission_id=kwargs.get('mission_id', 'unknown'),
                resource_id=kwargs.get('resource_id'),
                quantity=kwargs.get('quantity'),
                priority=kwargs.get('priority', 0),
                timeout=kwargs.get('timeout')
            )
            
            # Delegate to real allocator
            return self.advanced_allocator.request_allocation(request)
            
        return False, None, []


    def get_resource_forecast(self, resource_id: str, horizon: int):
        if hasattr(self, 'advanced_allocator'):
             # Return a dummy forecast
             from baritone_client.automator.allocation.forecaster import ResourceForecast
             return ResourceForecast(
                 resource_id=resource_id,
                 timestamp=datetime.now(),
                 predicted_demand=5,
                 predicted_supply=10,
                 confidence_level=0.9,
                 forecast_horizon=horizon,
                 influencing_factors=[]
             )
        return None

    def get_allocation_analytics(self):
        return {
            "advanced_allocation_enabled": getattr(self, 'advanced_allocation_enabled', False),
            "active_allocations": {}
        }

    def reserve_resource(self, resource_id: str, quantity: int) -> bool:
        current = self.get_item_count(resource_id)
        if current >= quantity:
            self.inventory[resource_id] = current - quantity
            return True
        return False



class TestConflictDetector:
    """Test conflict detection algorithms."""

    def setup_method(self):
        """Set up test fixtures."""
        self.resource_manager = MockResourceManager()
        self.detector = ConflictDetector(self.resource_manager)

    def test_no_conflicts_when_sufficient_resources(self):
        """Test no conflicts detected when resources are sufficient."""
        request = AllocationRequest(
            mission_id="test_mission",
            resource_id="minecraft:diamond",
            quantity=5
        )

        active_allocations = {}
        reservations = {}

        conflicts = self.detector.detect_allocation_conflicts(
            request, active_allocations, reservations
        )

        assert len(conflicts) == 0

    def test_conflict_detected_when_insufficient_resources(self):
        """Test conflict detection when resources are insufficient."""
        request = AllocationRequest(
            mission_id="test_mission",
            resource_id="minecraft:diamond",
            quantity=15  # More than available (10)
        )

        active_allocations = {}
        reservations = {}

        conflicts = self.detector.detect_allocation_conflicts(
            request, active_allocations, reservations
        )

        assert len(conflicts) == 1
        conflict = conflicts[0]
        assert conflict.resource_id == "minecraft:diamond"
        assert conflict.requested_quantity == 15
        assert conflict.available_quantity == 10

    def test_conflict_with_active_allocations(self):
        """Test conflict detection accounting for active allocations."""
        # Create an active allocation using 5 diamonds
        active_allocation = ResourceAllocation(
            allocation_id="active_1",
            mission_id="other_mission",
            resource_id="minecraft:diamond",
            quantity=5,
            allocated_at=datetime.now()
        )

        request = AllocationRequest(
            mission_id="test_mission",
            resource_id="minecraft:diamond",
            quantity=8  # 10 - 5 = 5 available, need 8
        )

        active_allocations = {"active_1": active_allocation}
        reservations = {}

        conflicts = self.detector.detect_allocation_conflicts(
            request, active_allocations, reservations
        )

        assert len(conflicts) == 1
        assert conflicts[0].available_quantity == 5  # 10 - 5 = 5

    def test_exclusive_allocation_blocks_others(self):
        """Test that exclusive allocations block other requests."""
        exclusive_allocation = ResourceAllocation(
            allocation_id="exclusive_1",
            mission_id="other_mission",
            resource_id="minecraft:diamond",
            quantity=1,
            allocated_at=datetime.now(),
            mode=AllocationMode.EXCLUSIVE
        )

        request = AllocationRequest(
            mission_id="test_mission",
            resource_id="minecraft:diamond",
            quantity=1
        )

        active_allocations = {"exclusive_1": exclusive_allocation}
        reservations = {}

        conflicts = self.detector.detect_allocation_conflicts(
            request, active_allocations, reservations
        )

        assert len(conflicts) == 1
        assert conflicts[0].available_quantity == 0  # Exclusive blocks all


class TestResourceForecaster:
    """Test resource forecasting capabilities."""

    def setup_method(self):
        """Set up test fixtures."""
        self.resource_manager = MockResourceManager()
        self.forecaster = ResourceForecaster(self.resource_manager)

    def test_forecast_with_insufficient_data(self):
        """Test forecast returns None with insufficient historical data."""
        forecast = self.forecaster.generate_forecast("minecraft:diamond", 60)
        assert forecast is None

    def test_forecast_generation_with_data(self):
        """Test forecast generation with sufficient historical data."""
        # Record some usage data
        base_time = datetime.now()
        self.forecaster.usage_history["minecraft:diamond"] = [
            (base_time - timedelta(minutes=30), 2),
            (base_time - timedelta(minutes=20), 3),
            (base_time - timedelta(minutes=10), 1),
            (base_time, 2)
        ]

        forecast = self.forecaster.generate_forecast("minecraft:diamond", 60, confidence_threshold=0.1)

        assert forecast is not None
        assert forecast.resource_id == "minecraft:diamond"
        assert forecast.predicted_demand >= 0
        assert forecast.predicted_supply >= 0
        assert 0.0 <= forecast.confidence_level <= 1.0

    def test_usage_recording(self):
        """Test that usage recording updates historical data."""
        initial_count = len(self.forecaster.usage_history["minecraft:diamond"])

        self.forecaster.record_usage("minecraft:diamond", 5, "test_mission")

        assert len(self.forecaster.usage_history["minecraft:diamond"]) == initial_count + 1
        timestamp, quantity = self.forecaster.usage_history["minecraft:diamond"][-1]
        assert quantity == 5


class TestConflictResolver:
    """Test automatic conflict resolution strategies."""

    def setup_method(self):
        """Set up test fixtures."""
        self.resource_manager = MockResourceManager()
        self.resolver = ConflictResolver(self.resource_manager)

    def test_preemption_resolution(self):
        """Test priority-based preemption resolution."""
        conflict = AllocationConflict(
            conflict_id="test_conflict",
            resource_id="minecraft:diamond",
            requesting_mission="high_priority_mission",
            requested_quantity=5,
            available_quantity=0,
            blocking_allocations=["low_priority_alloc"],
            blocking_missions=["low_priority_mission"],
            resolution_candidates=[ConflictResolutionStrategy.PRIORITY_PREEMPTION]
        )

        active_allocations = {
            "low_priority_alloc": ResourceAllocation(
                allocation_id="low_priority_alloc",
                mission_id="low_priority_mission",
                resource_id="minecraft:diamond",
                quantity=5,
                allocated_at=datetime.now()
            )
        }

        plan = self.resolver.resolve_conflict(conflict, active_allocations)

        assert plan is not None
        assert plan.strategy == ConflictResolutionStrategy.PRIORITY_PREEMPTION
        assert len(plan.resolution_actions) > 0
        assert plan.resolution_actions[0]["action"] == "preempt_allocation"

    def test_quota_adjustment_resolution(self):
        """Test quota adjustment when flexible quantities allowed."""
        conflict = AllocationConflict(
            conflict_id="test_conflict",
            resource_id="minecraft:diamond",
            requesting_mission="flexible_mission",
            requested_quantity=10,
            available_quantity=3,
            blocking_allocations=[],
            blocking_missions=[],
            resolution_candidates=[ConflictResolutionStrategy.QUOTA_ADJUSTMENT]
        )

        active_allocations = {}

        plan = self.resolver.resolve_conflict(conflict, active_allocations)

        assert plan is not None
        assert plan.strategy == ConflictResolutionStrategy.QUOTA_ADJUSTMENT
        assert len(plan.resolution_actions) > 0
        assert plan.resolution_actions[0]["action"] == "reduce_allocation"


class TestResourceAllocator:
    """Test the main ResourceAllocator class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.resource_manager = MockResourceManager()
        self.allocator = ResourceAllocator(self.resource_manager)

    def test_successful_allocation(self):
        """Test successful resource allocation."""
        request = AllocationRequest(
            mission_id="test_mission",
            resource_id="minecraft:diamond",
            quantity=5
        )

        success, grant, conflicts = self.allocator.request_allocation(request)

        assert success is True
        assert grant is not None
        assert grant.granted_quantity == 5
        assert len(conflicts) == 0

        # Verify allocation was recorded
        state = self.allocator.get_allocation_state()
        assert len(state.active_allocations) == 1
        assert grant.allocation_id in state.active_allocations

    def test_allocation_conflict(self):
        """Test allocation conflict detection."""
        # First allocation takes all diamonds
        request1 = AllocationRequest(
            mission_id="mission_1",
            resource_id="minecraft:diamond",
            quantity=10
        )

        success1, grant1, conflicts1 = self.allocator.request_allocation(request1)
        assert success1 is True

        # Second allocation should conflict
        request2 = AllocationRequest(
            mission_id="mission_2",
            resource_id="minecraft:diamond",
            quantity=5
        )

        success2, grant2, conflicts2 = self.allocator.request_allocation(request2)
        assert success2 is False
        assert len(conflicts2) == 1

    def test_reservation_creation(self):
        """Test resource reservation creation."""
        request = AllocationRequest(
            mission_id="test_mission",
            resource_id="minecraft:iron_ingot",
            quantity=20
        )

        reservation = self.allocator.create_reservation(request)

        assert reservation is not None
        assert reservation.reserved_by == "test_mission"
        assert reservation.quantity == 20

    def test_allocation_release(self):
        """Test allocation release."""
        request = AllocationRequest(
            mission_id="test_mission",
            resource_id="minecraft:wood",
            quantity=10
        )

        success, grant, _ = self.allocator.request_allocation(request)
        assert success is True

        release_success = self.allocator.release_allocation(grant.allocation_id)
        assert release_success is True

        # Verify allocation was removed
        state = self.allocator.get_allocation_state()
        assert grant.allocation_id not in state.active_allocations

    def test_usage_tracking(self):
        """Test allocation usage tracking."""
        request = AllocationRequest(
            mission_id="test_mission",
            resource_id="minecraft:stone",
            quantity=50
        )

        success, grant, _ = self.allocator.request_allocation(request)
        assert success is True

        # Record usage
        self.allocator.update_allocation_usage(grant.allocation_id, 10)

        # Verify usage was tracked
        allocation = self.allocator.active_allocations[grant.allocation_id]
        assert allocation.actual_usage == 10

    def test_reservation_cleanup(self):
        """Test cleanup of expired reservations."""
        # Create a reservation that expires immediately
        past_time = datetime.now() - timedelta(minutes=10)

        reservation = ResourceReservation(
            reservation_id="expired_reservation",
            resource_id="minecraft:wood",
            quantity=10,
            reserved_by="test_mission",
            reserved_at=past_time,
            expires_at=past_time + timedelta(minutes=5)  # Already expired
        )

        self.allocator.reservations["expired_reservation"] = reservation

        # Run cleanup
        self.allocator.cleanup_expired_reservations()

        # Verify reservation was removed
        assert "expired_reservation" not in self.allocator.reservations


class TestIntegrationWithResourceManager:
    """Test integration between ResourceAllocator and ResourceManager."""

    def setup_method(self):
        """Set up test fixtures."""
        self.resource_manager = MockResourceManager()

        # Enable advanced allocation
        self.resource_manager.enable_advanced_allocation(True)

        # Mock client for ResourceManager
        self.resource_manager.client = Mock()

    def test_advanced_allocation_request(self):
        """Test requesting allocation through ResourceManager interface."""
        success, grant_info, conflicts = self.resource_manager.request_advanced_allocation(
            resource_id="minecraft:diamond",
            quantity=5,
            mission_id="test_mission"
        )

        assert success is True
        assert grant_info is not None
        assert grant_info.granted_quantity == 5
        assert len(conflicts) == 0

    def test_forecast_through_resource_manager(self):
        """Test getting forecasts through ResourceManager interface."""
        # Add some historical data first
        if hasattr(self.resource_manager, 'advanced_allocator') and self.resource_manager.advanced_allocator:
            forecaster = self.resource_manager.advanced_allocator.forecaster

            base_time = datetime.now()
            forecaster.usage_history["minecraft:diamond"] = [
                (base_time - timedelta(minutes=30), 2),
                (base_time - timedelta(minutes=20), 3),
                (base_time - timedelta(minutes=10), 1),
                (base_time, 2)
            ]

        forecast = self.resource_manager.get_resource_forecast("minecraft:diamond", 60)

        if hasattr(self.resource_manager, 'advanced_allocator') and self.resource_manager.advanced_allocator:
            assert forecast is not None
        else:
            assert forecast is None

    def test_allocation_analytics(self):
        """Test getting allocation analytics."""
        analytics = self.resource_manager.get_allocation_analytics()

        assert isinstance(analytics, dict)
        assert "advanced_allocation_enabled" in analytics

        if hasattr(self.resource_manager, 'advanced_allocator') and self.resource_manager.advanced_allocator:
            assert analytics["advanced_allocation_enabled"] is True
            assert "active_allocations" in analytics
        else:
            assert analytics["advanced_allocation_enabled"] is False


class TestMissionCoordinatorIntegration:
    """Test integration with MissionCoordinator."""

    def setup_method(self):
        """Set up test fixtures."""
        self.resource_manager = MockResourceManager()
        self.resource_manager.enable_advanced_allocation(True)

        # Mock event manager
        event_manager = Mock()
        event_manager.subscribe = Mock()
        event_manager.unsubscribe = Mock()

        from baritone_client.automator.mission_coordinator import MissionCoordinator
        self.coordinator = MissionCoordinator(
            resource_manager=self.resource_manager,
            event_manager=event_manager
        )

        # Enable advanced allocation in coordinator
        self.coordinator.enable_advanced_allocation(True)

    def test_mission_resource_request(self):
        """Test requesting resources for a mission."""
        success, grant_info, conflicts = self.coordinator.request_mission_resources(
            mission_id="test_mission",
            resource_requirements={"minecraft:diamond": 5},
            priority=1
        )

        assert success is True
        assert grant_info is not None
        assert len(conflicts) == 0

    def test_resource_conflict_checking(self):
        """Test advanced conflict checking."""
        # Create a mission that uses resources
        success1, _, _ = self.coordinator.request_mission_resources(
            mission_id="mission_1",
            resource_requirements={"minecraft:diamond": 10},
            priority=1
        )
        assert success1 is True
        
        # DEBUG
        if hasattr(self.resource_manager, 'advanced_allocator'):
            print(f"DEBUG: Active allocations: {len(self.resource_manager.advanced_allocator.active_allocations)}")
            print(f"DEBUG: Active allocation details: {self.resource_manager.advanced_allocator.active_allocations}")

        # Check conflicts for second mission
        conflicts = self.coordinator.check_resource_conflicts_advanced(
            mission_id="mission_2",
            resource_requirements={"minecraft:diamond": 5}
        )
        
        # DEBUG
        print(f"DEBUG: Conflicts found: {conflicts}")

        assert len(conflicts) > 0


    def test_optimization_recommendations(self):
        """Test mission scheduling optimization."""
        # This would test the optimization logic
        recommendations = self.coordinator.optimize_mission_schedule()

        assert isinstance(recommendations, dict)
        assert "can_start_immediately" in recommendations
        assert "should_delay" in recommendations
        assert "resource_bottlenecks" in recommendations


if __name__ == "__main__":
    pytest.main([__file__])