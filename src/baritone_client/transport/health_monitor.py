"""
Enhanced connection health monitoring system with advanced metrics and failover capabilities.

REFACTORED: Implementation moved to `src/baritone_client/transport/health/`
"""

from .health import (
    CircuitBreakerState,
    SLABreachType,
    HeartbeatConfig,
    HealthPolicy,
    ConnectionMetrics,
    QualityMetricsStreamer,
    CircuitBreaker,
    HeartbeatTransport,
    SLAMonitor,
    FailoverManager
)

__all__ = [
    "CircuitBreakerState",
    "SLABreachType",
    "HeartbeatConfig",
    "HealthPolicy",
    "ConnectionMetrics",
    "QualityMetricsStreamer",
    "CircuitBreaker",
    "HeartbeatTransport",
    "SLAMonitor",
    "FailoverManager"
]