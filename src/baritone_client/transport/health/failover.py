"""
Transport Failover Management
"""

import threading
import time
import logging
from typing import Dict, Set, Optional, Any

from .types import HealthPolicy, CircuitBreakerState
from .metrics import ConnectionMetrics
from .circuit import CircuitBreaker

logger = logging.getLogger(__name__)


class FailoverManager:
    """Intelligent transport failover management with quality-based selection."""

    def __init__(self, health_policy: HealthPolicy):
        self.health_policy = health_policy
        self._transport_metrics: Dict[str, ConnectionMetrics] = {}
        self._circuit_breakers: Dict[str, CircuitBreaker] = {}
        self._last_failover_time = 0.0
        self._failover_cooldown = 30.0  # Minimum seconds between failovers
        self._lock = threading.RLock()

    def register_transport(self, transport_name: str) -> None:
        """Register a transport for health monitoring."""
        with self._lock:
            self._transport_metrics[transport_name] = ConnectionMetrics()
            self._circuit_breakers[transport_name] = CircuitBreaker(self.health_policy)
            logger.info(f"Registered transport for health monitoring: {transport_name}")

    def update_metrics(self, transport_name: str, metrics: ConnectionMetrics) -> None:
        """Update metrics for a transport."""
        with self._lock:
            if transport_name in self._transport_metrics:
                self._transport_metrics[transport_name] = metrics

    def calculate_health_score(self, transport_name: str) -> float:
        """Calculate comprehensive health score for transport."""
        with self._lock:
            if transport_name not in self._transport_metrics:
                return 0.0

            metrics = self._transport_metrics[transport_name]
            circuit_breaker = self._circuit_breakers[transport_name]

            # Circuit breaker affects score
            if circuit_breaker.state == CircuitBreakerState.OPEN:
                return 0.0
            elif circuit_breaker.state == CircuitBreakerState.HALF_OPEN:
                base_score = 0.5
            else:
                base_score = 1.0

            # Latency score (inverse of p95 latency)
            latency_score = max(0.0, 1.0 - (metrics.p95_latency / max(self.health_policy.max_latency_p95, 0.1)))

            # Availability score
            availability_score = metrics.get_availability()

            # Error rate score (inverse)
            error_rate_score = max(0.0, 1.0 - metrics.get_error_rate())

            # Stability score
            stability_score = metrics.stability_score

            # Weighted combination
            weighted_score = (
                latency_score * self.health_policy.latency_weight +
                availability_score * self.health_policy.availability_weight +
                error_rate_score * self.health_policy.error_rate_weight +
                stability_score * 0.1  # Additional stability weight
            )

            return base_score * weighted_score

    def select_best_transport(self, available_transports: Set[str]) -> Optional[str]:
        """Select the best transport based on health scores."""
        if not available_transports:
            return None

        transport_scores = []
        for transport_name in available_transports:
            if transport_name in self._transport_metrics:
                score = self.calculate_health_score(transport_name)
                transport_scores.append((transport_name, score))

        if not transport_scores:
            return None

        # Sort by score descending, then by name for deterministic ordering
        transport_scores.sort(key=lambda x: (-x[1], x[0]))

        best_transport, best_score = transport_scores[0]
        logger.debug(f"Best transport: {best_transport} (score: {best_score:.3f})")
        return best_transport

    def should_failover(self, current_transport: str, available_transports: Set[str]) -> Optional[str]:
        """Determine if failover is needed and to which transport."""
        current_time = time.time()

        # Check cooldown
        if current_time - self._last_failover_time < self._failover_cooldown:
            return None

        # Get current health score
        current_score = self.calculate_health_score(current_transport)

        # Find better alternatives
        alternatives = available_transports - {current_transport}
        if not alternatives:
            return None

        best_alternative = self.select_best_transport(alternatives)
        if not best_alternative:
            return None

        alternative_score = self.calculate_health_score(best_alternative)

        # Failover if alternative is significantly better (20% improvement)
        if alternative_score > current_score * 1.2:
            self._last_failover_time = current_time
            logger.info(f"Failover recommended: {current_transport} -> {best_alternative} "
                       f"(score: {current_score:.3f} -> {alternative_score:.3f})")
            return best_alternative

        return None

    def record_transport_success(self, transport_name: str) -> None:
        """Record successful operation for transport."""
        with self._lock:
            if transport_name in self._circuit_breakers:
                self._circuit_breakers[transport_name].record_success()

    def record_transport_failure(self, transport_name: str) -> None:
        """Record failed operation for transport."""
        with self._lock:
            if transport_name in self._circuit_breakers:
                self._circuit_breakers[transport_name].record_failure()

    def get_transport_status(self, transport_name: str) -> Dict[str, Any]:
        """Get comprehensive status for a transport."""
        with self._lock:
            if transport_name not in self._transport_metrics:
                return {"error": "Transport not registered"}

            metrics = self._transport_metrics[transport_name]
            circuit_breaker = self._circuit_breakers[transport_name]

            return {
                "metrics": {
                    "p50_latency": metrics.p50_latency,
                    "p95_latency": metrics.p95_latency,
                    "p99_latency": metrics.p99_latency,
                    "avg_jitter": metrics.avg_jitter,
                    "stability_score": metrics.stability_score,
                    "error_rate": metrics.get_error_rate(),
                    "availability": metrics.get_availability(),
                    "total_requests": metrics.total_requests,
                    "consecutive_errors": metrics.consecutive_errors
                },
                "circuit_breaker": circuit_breaker.get_status(),
                "health_score": self.calculate_health_score(transport_name),
                "last_updated": metrics.last_successful_operation or metrics.last_failure_time
            }
