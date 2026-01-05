"""
Health Monitoring Types and Configurations
"""

from dataclasses import dataclass
from enum import Enum


class CircuitBreakerState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, requests rejected
    HALF_OPEN = "half_open"  # Testing recovery


class SLABreachType(Enum):
    """Types of SLA breaches."""
    LATENCY = "latency"
    AVAILABILITY = "availability"
    ERROR_RATE = "error_rate"


@dataclass
class HeartbeatConfig:
    """Configuration for heartbeat protocol."""
    interval: float = 30.0  # Seconds between heartbeats
    timeout: float = 5.0  # Heartbeat response timeout
    max_missed: int = 3  # Max consecutive missed heartbeats before failure
    payload_size: int = 64  # Size of heartbeat payload for bandwidth testing


@dataclass
class HealthPolicy:
    """Configurable health monitoring policy."""

    # Latency thresholds (seconds)
    max_latency_p95: float = 2.0
    max_latency_p99: float = 5.0

    # Error rate thresholds (percentage)
    max_error_rate: float = 0.05  # 5%

    # Availability requirements (percentage)
    min_availability: float = 0.99  # 99%

    # Circuit breaker settings
    circuit_failure_threshold: int = 10  # Failures to trigger circuit open
    circuit_recovery_timeout: float = 60.0  # Seconds before attempting recovery
    circuit_success_threshold: int = 3  # Successes needed to close circuit

    # Health score weights
    latency_weight: float = 0.4
    availability_weight: float = 0.4
    error_rate_weight: float = 0.2

    # Monitoring window (seconds)
    monitoring_window: float = 300.0  # 5 minutes
