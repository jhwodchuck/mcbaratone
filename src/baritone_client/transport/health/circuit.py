"""
Circuit Breaker Implementation
"""

import time
import logging
from typing import Dict, Any

from .types import CircuitBreakerState, HealthPolicy

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Circuit breaker pattern implementation for graceful degradation."""

    def __init__(self, policy: HealthPolicy):
        self.policy = policy
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.success_count = 0
        self.next_attempt_time = 0.0

    def should_attempt(self) -> bool:
        """Check if request should be attempted based on circuit state."""
        current_time = time.time()

        if self.state == CircuitBreakerState.CLOSED:
            return True
        elif self.state == CircuitBreakerState.OPEN:
            if current_time >= self.next_attempt_time:
                self.state = CircuitBreakerState.HALF_OPEN
                self.success_count = 0
                return True
            return False
        elif self.state == CircuitBreakerState.HALF_OPEN:
            return True

        return False

    def record_success(self) -> None:
        """Record successful operation."""
        if self.state == CircuitBreakerState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.policy.circuit_success_threshold:
                self.state = CircuitBreakerState.CLOSED
                self.failure_count = 0
                logger.info("Circuit breaker closed - service recovered")

    def record_failure(self) -> None:
        """Record failed operation."""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.state == CircuitBreakerState.HALF_OPEN:
            self.state = CircuitBreakerState.OPEN
            self.next_attempt_time = time.time() + self.policy.circuit_recovery_timeout
            logger.warning("Circuit breaker opened due to failure in half-open state")

        elif (self.state == CircuitBreakerState.CLOSED and
              self.failure_count >= self.policy.circuit_failure_threshold):
            self.state = CircuitBreakerState.OPEN
            self.next_attempt_time = time.time() + self.policy.circuit_recovery_timeout
            logger.warning(f"Circuit breaker opened after {self.failure_count} failures")

    def get_status(self) -> Dict[str, Any]:
        """Get circuit breaker status."""
        return {
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "next_attempt_time": self.next_attempt_time,
            "time_until_attempt": max(0.0, self.next_attempt_time - time.time())
        }
