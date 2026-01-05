from typing import Any, Dict, Optional, Tuple, List, Set
from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime
from enum import Enum

class BlockPos(BaseModel):
    x: int
    y: int
    z: int

    model_config = ConfigDict(frozen=True)

    def to_dict(self) -> Dict[str, int]:
        return self.model_dump()

    @classmethod
    def from_tuple(cls, coords: Tuple[int, int, int]) -> "BlockPos":
        if len(coords) != 3:
            raise ValueError("BlockPos requires exactly three coordinates")
        return cls(x=int(coords[0]), y=int(coords[1]), z=int(coords[2]))


class BetterBlockPos(BlockPos):
    def offset(self, dx: int = 0, dy: int = 0, dz: int = 0) -> "BetterBlockPos":
        return BetterBlockPos(x=self.x + dx, y=self.y + dy, z=self.z + dz)


class Selection(BaseModel):
    start: BetterBlockPos
    end: BetterBlockPos

    model_config = ConfigDict(frozen=True)

    def to_dict(self) -> Dict[str, Dict[str, int]]:
        return self.model_dump()


class Goal(BaseModel):
    goal_type: str = Field(alias="type")
    payload: Dict[str, Any]

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump(by_alias=True)


# Advanced Phase B Models for Health Monitoring and Analytics

class BridgeHealth(BaseModel):
    """Bridge thread pool utilization and system load metrics."""
    thread_pool_active: int
    thread_pool_size: int
    thread_pool_utilization: float
    active_connections: int
    system_load_average: Optional[float] = None
    memory_usage_mb: Optional[int] = None
    uptime_seconds: Optional[int] = None

    model_config = ConfigDict(frozen=True)


class BridgeMetrics(BaseModel):
    """Performance metrics and error statistics."""
    total_requests: int
    successful_requests: int
    failed_requests: int
    average_response_time_ms: float
    max_response_time_ms: float
    error_breakdown: Dict[str, int]  # error_type -> count
    timestamp: datetime

    model_config = ConfigDict(frozen=True)


class CircuitBreakerStatus(BaseModel):
    """Circuit breaker state and failure statistics."""
    state: str  # "CLOSED", "OPEN", "HALF_OPEN"
    failure_count: int
    success_count: int
    last_failure_time: Optional[datetime] = None
    next_attempt_time: Optional[datetime] = None
    failure_threshold: int
    success_threshold: int
    timeout_seconds: int

    model_config = ConfigDict(frozen=True)


class CommandAnalytics(BaseModel):
    """Performance tracking for individual commands."""
    command_name: str
    total_calls: int
    successful_calls: int
    failed_calls: int
    average_execution_time_ms: float
    max_execution_time_ms: float
    min_execution_time_ms: float
    error_types: Dict[str, int]  # error_type -> count
    last_called: datetime

    model_config = ConfigDict(frozen=True)


class PerformanceReport(BaseModel):
    """Comprehensive performance report with bottleneck analysis."""
    timestamp: datetime
    overall_success_rate: float
    total_commands: int
    slow_commands: List[str]  # commands with execution time > threshold
    error_prone_commands: List[str]  # commands with failure rate > threshold
    bottleneck_suggestions: List[str]  # optimization recommendations
    health_score: float  # 0.0 to 1.0, higher is better

    model_config = ConfigDict(frozen=True)


class ClientRetryPolicy(BaseModel):
    """Configurable retry behavior for client-side error recovery."""
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    backoff_multiplier: float = 2.0
    jitter_enabled: bool = True
    retry_on_errors: List[str] = ["TransportError", "CommandError"]  # error types to retry
    circuit_breaker_integration: bool = True

    model_config = ConfigDict(frozen=True)


# Batch Operations Models

class PriorityLevel(str, Enum):
    """Priority levels for command execution."""
    CRITICAL = "critical"  # System-critical operations
    HIGH = "high"         # High-priority operations
    NORMAL = "normal"     # Default priority
    LOW = "low"          # Background operations


class BatchCommand(BaseModel):
    """Individual command within a batch operation."""
    id: str  # Unique identifier for the command
    command: str
    params: Optional[Dict[str, Any]] = None
    priority: PriorityLevel = PriorityLevel.NORMAL
    timeout: Optional[float] = None  # Command-specific timeout

    model_config = ConfigDict(frozen=True)


class BatchRequest(BaseModel):
    """Request containing multiple commands for batch processing."""
    batch_id: str  # Unique identifier for the batch
    commands: List[BatchCommand]
    transaction_mode: bool = True  # If true, all commands must succeed or all fail
    overall_timeout: Optional[float] = None  # Total timeout for entire batch

    model_config = ConfigDict(frozen=True)

    def validate_batch(self) -> None:
        """Validate batch constraints."""
        if not self.commands:
            raise ValueError("Batch must contain at least one command")

        command_ids = [cmd.id for cmd in self.commands]
        if len(command_ids) != len(set(command_ids)):
            raise ValueError("All commands in batch must have unique IDs")


class BatchCommandResult(BaseModel):
    """Result for an individual command within a batch."""
    command_id: str
    success: bool
    data: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    execution_time_ms: float
    retry_attempts: int = 0
    priority_level: Optional[str] = None

    model_config = ConfigDict(frozen=True)


class BatchResult(BaseModel):
    """Result for a complete batch operation."""
    batch_id: str
    overall_success: bool
    command_results: List[BatchCommandResult]
    total_execution_time_ms: float
    failed_commands: List[str]  # IDs of failed commands
    rollback_performed: bool = False
    error_summary: Optional[str] = None

    model_config = ConfigDict(frozen=True)

    @property
    def success_rate(self) -> float:
        """Calculate success rate of the batch."""
        if not self.command_results:
            return 0.0
        successful = sum(1 for r in self.command_results if r.success)
        return successful / len(self.command_results)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary suitable for network transmission."""
        result = self.model_dump()
        result["success_rate"] = self.success_rate
        return result


# Mission Coordination Framework Models

class MissionStatus(str, Enum):
    """Current status of a mission."""
    PENDING = "pending"      # Mission created but not started
    RUNNING = "running"      # Mission is actively executing
    PAUSED = "paused"        # Mission temporarily suspended
    COMPLETED = "completed"  # Mission finished successfully
    FAILED = "failed"        # Mission ended with failure
    CANCELLED = "cancelled"  # Mission was cancelled by user


class MissionPriority(str, Enum):
    """Priority levels for mission execution."""
    CRITICAL = "critical"  # System-critical missions
    HIGH = "high"         # High-priority missions
    NORMAL = "normal"     # Standard priority
    LOW = "low"          # Background missions
    IDLE = "idle"        # Lowest priority, only when resources available


class ResourceAllocation(BaseModel):
    """Resource allocation for a mission."""
    item_id: str
    quantity: int
    priority: int = 0  # Allocation priority (higher = harder to preempt)

    model_config = ConfigDict(frozen=True)


class MissionDependency(BaseModel):
    """Dependency relationship between missions."""
    mission_id: str
    dependency_type: str = "prerequisite"  # prerequisite, concurrent, exclusive
    required_status: Optional[MissionStatus] = None

    model_config = ConfigDict(frozen=True)


class MissionState(BaseModel):
    """Snapshot of mission execution state."""
    mission_id: str
    status: MissionStatus
    phase: str
    progress: float = 0.0  # 0.0 to 1.0
    start_time: Optional[datetime] = None
    completed_time: Optional[datetime] = None
    last_update: datetime
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)


class MissionRequirements(BaseModel):
    """Resource and dependency requirements for a mission."""
    resources: Dict[str, int] = Field(default_factory=dict)  # item_id -> quantity
    dependencies: List[MissionDependency] = Field(default_factory=list)
    exclusive_resources: Set[str] = Field(default_factory=set)  # Resources that can't be shared
    estimated_duration: Optional[float] = None  # Estimated duration in seconds

    model_config = ConfigDict(frozen=True)


class MissionInstance(BaseModel):
    """Complete mission instance with state and requirements."""
    id: str
    name: str
    description: Optional[str] = None
    priority: MissionPriority = MissionPriority.NORMAL
    requirements: MissionRequirements
    state: MissionState
    allocated_resources: List[ResourceAllocation] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(frozen=True)

    @property
    def is_active(self) -> bool:
        """Check if mission is currently active."""
        return self.state.status in [MissionStatus.RUNNING, MissionStatus.PENDING]

    @property
    def is_completed(self) -> bool:
        """Check if mission has finished."""
        return self.state.status in [MissionStatus.COMPLETED, MissionStatus.FAILED, MissionStatus.CANCELLED]


class ResourceConflict(BaseModel):
    """Represents a resource conflict between missions."""
    resource_id: str
    required_quantity: int
    available_quantity: int
    conflicting_missions: List[str]  # Mission IDs that need this resource

    model_config = ConfigDict(frozen=True)


class MissionCoordinatorState(BaseModel):
    """Global state of the mission coordinator."""
    active_missions: Dict[str, MissionInstance] = Field(default_factory=dict)
    pending_missions: List[MissionInstance] = Field(default_factory=list)
    resource_allocations: Dict[str, ResourceAllocation] = Field(default_factory=dict)
    conflicts: List[ResourceConflict] = Field(default_factory=list)
    last_sync: datetime

    model_config = ConfigDict(frozen=True)


class MissionEvent(BaseModel):
    """Event for mission state changes."""
    event_type: str  # mission_started, mission_completed, mission_failed, etc.
    mission_id: str
    timestamp: datetime
    data: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)


# Advanced Resource Allocation Models - Phase 3

class AllocationMode(str, Enum):
    """Resource allocation modes."""
    EXCLUSIVE = "exclusive"  # Full ownership required
    SHARED = "shared"       # Can be shared concurrently
    RESERVABLE = "reservable"  # Can be reserved for future use
    DYNAMIC = "dynamic"     # Allocation adjusted based on usage patterns


class ResourceSharingPolicy(str, Enum):
    """Policies for resource sharing between missions."""
    NO_SHARING = "no_sharing"           # Exclusive access only
    TIME_SLICED = "time_sliced"         # Time-based sharing
    QUOTA_BASED = "quota_based"         # Usage quota limits
    PRIORITY_BASED = "priority_based"   # Higher priority gets preference
    FAIR_SHARE = "fair_share"           # Equal sharing among missions


class ResourceForecast(BaseModel):
    """Forecasted resource usage and availability."""
    resource_id: str
    timestamp: datetime
    predicted_demand: int
    predicted_supply: int
    confidence_level: float = 0.0  # 0.0 to 1.0
    forecast_horizon: int  # Minutes into future
    influencing_factors: List[str] = Field(default_factory=list)  # Mission IDs, patterns, etc.

    model_config = ConfigDict(frozen=True)


class AllocationRequest(BaseModel):
    """Request for resource allocation."""
    mission_id: str
    resource_id: str
    quantity: int
    priority: int = 0
    mode: AllocationMode = AllocationMode.SHARED
    duration_estimate: Optional[int] = None  # Estimated duration in seconds
    flexible_quantity: bool = False  # Can allocate less than requested
    alternatives: List[str] = Field(default_factory=list)  # Alternative resources

    model_config = ConfigDict(frozen=True)


class AllocationGrant(BaseModel):
    """Granted resource allocation."""
    allocation_id: str
    request: AllocationRequest
    granted_quantity: int
    granted_at: datetime
    expires_at: Optional[datetime] = None
    usage_tracking: bool = True

    model_config = ConfigDict(frozen=True)


class ResourceReservation(BaseModel):
    """Reserved resource for future use."""
    reservation_id: str
    resource_id: str
    quantity: int
    reserved_by: str  # Mission ID
    reserved_at: datetime
    expires_at: datetime
    priority: int = 0

    model_config = ConfigDict(frozen=True)


class ConflictResolutionStrategy(str, Enum):
    """Strategies for resolving resource conflicts."""
    PRIORITY_PREEMPTION = "priority_preemption"     # Higher priority preempts lower
    TIME_NEGOTIATION = "time_negotiation"          # Negotiate time slots
    QUOTA_ADJUSTMENT = "quota_adjustment"          # Adjust usage quotas
    RESOURCE_SUBSTITUTION = "resource_substitution"  # Use alternative resources
    MISSION_DELAY = "mission_delay"                # Delay lower priority mission
    ALLOCATION_SCALING = "allocation_scaling"      # Reduce allocations proportionally


class ConflictResolutionPlan(BaseModel):
    """Plan for resolving resource conflicts."""
    conflict_id: str
    strategy: ConflictResolutionStrategy
    affected_missions: List[str]
    resolution_actions: List[Dict[str, Any]]  # Specific actions to take
    estimated_impact: Dict[str, float] = Field(default_factory=dict)  # Mission delays, etc.
    created_at: datetime

    model_config = ConfigDict(frozen=True)


class ResourceAllocation(BaseModel):
    """Enhanced resource allocation with advanced tracking."""
    allocation_id: str
    mission_id: str
    resource_id: str
    quantity: int
    allocated_at: datetime
    expires_at: Optional[datetime] = None
    priority: int = 0
    mode: AllocationMode = AllocationMode.SHARED
    usage_pattern: Optional[str] = None  # burst, steady, intermittent
    actual_usage: int = 0  # Track actual consumption
    last_accessed: Optional[datetime] = None

    model_config = ConfigDict(frozen=True)


class ResourceUsageAnalytics(BaseModel):
    """Analytics for resource usage patterns."""
    resource_id: str
    time_window: int  # Minutes
    peak_usage: int
    average_usage: float
    usage_variance: float
    access_frequency: float  # Accesses per minute
    mission_patterns: Dict[str, int]  # Mission type -> usage count
    bottleneck_events: int  # Number of times resource was bottleneck
    last_updated: datetime

    model_config = ConfigDict(frozen=True)


class ResourceAllocationState(BaseModel):
    """Global state of resource allocations."""
    active_allocations: Dict[str, ResourceAllocation] = Field(default_factory=dict)
    reservations: Dict[str, ResourceReservation] = Field(default_factory=dict)
    forecasts: Dict[str, List[ResourceForecast]] = Field(default_factory=dict)
    conflicts: List[ResourceConflict] = Field(default_factory=list)
    analytics: Dict[str, ResourceUsageAnalytics] = Field(default_factory=dict)
    last_sync: datetime

    model_config = ConfigDict(frozen=True)


class AllocationConflict(BaseModel):
    """Detailed conflict information for allocation decisions."""
    conflict_id: str
    resource_id: str
    requesting_mission: str
    requested_quantity: int
    available_quantity: int
    blocking_allocations: List[str]  # Allocation IDs blocking the request
    blocking_missions: List[str]     # Mission IDs with blocking allocations
    resolution_candidates: List[ConflictResolutionStrategy]

    model_config = ConfigDict(frozen=True)
