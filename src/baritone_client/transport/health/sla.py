"""
SLA Monitoring Implementation
"""

import threading
import time
import logging
from typing import Dict, List, Any, Callable

from .types import SLABreachType, HealthPolicy
from .metrics import ConnectionMetrics


logger = logging.getLogger(__name__)


class SLAMonitor:
    """Monitors SLA compliance and triggers alerts."""

    def __init__(self, policy: HealthPolicy):
        self.policy = policy
        self._breaches: List[Dict[str, Any]] = []
        self._alert_callbacks: List[Callable[[SLABreachType, Dict[str, Any]], None]] = []
        self._lock = threading.RLock()

    def check_sla_compliance(self, metrics: ConnectionMetrics) -> Dict[str, Any]:
        """Check SLA compliance and return status."""
        breaches = []
        current_time = time.time()

        # Check latency SLA
        if metrics.p95_latency > self.policy.max_latency_p95:
            breaches.append({
                "type": SLABreachType.LATENCY,
                "metric": "p95_latency",
                "threshold": self.policy.max_latency_p95,
                "actual": metrics.p95_latency,
                "timestamp": current_time
            })

        if metrics.p99_latency > self.policy.max_latency_p99:
            breaches.append({
                "type": SLABreachType.LATENCY,
                "metric": "p99_latency",
                "threshold": self.policy.max_latency_p99,
                "actual": metrics.p99_latency,
                "timestamp": current_time
            })

        # Check availability SLA
        availability = metrics.get_availability()
        if availability < self.policy.min_availability:
            breaches.append({
                "type": SLABreachType.AVAILABILITY,
                "metric": "availability",
                "threshold": self.policy.min_availability,
                "actual": availability,
                "timestamp": current_time
            })

        # Check error rate SLA
        error_rate = metrics.get_error_rate()
        if error_rate > self.policy.max_error_rate:
            breaches.append({
                "type": SLABreachType.ERROR_RATE,
                "metric": "error_rate",
                "threshold": self.policy.max_error_rate,
                "actual": error_rate,
                "timestamp": current_time
            })

        # Record breaches and trigger alerts
        with self._lock:
            for breach in breaches:
                self._breaches.append(breach)
                self._trigger_alert(breach["type"], breach)

        return {
            "compliant": len(breaches) == 0,
            "breaches": breaches,
            "checked_at": current_time
        }

    def add_alert_callback(self, callback: Callable[[SLABreachType, Dict[str, Any]], None]) -> None:
        """Add callback for SLA breach alerts."""
        with self._lock:
            self._alert_callbacks.append(callback)

    def get_recent_breaches(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent SLA breaches."""
        with self._lock:
            return list(self._breaches[-limit:])

    def _trigger_alert(self, breach_type: SLABreachType, breach_data: Dict[str, Any]) -> None:
        """Trigger alert callbacks for SLA breach."""
        logger.warning(f"SLA breach detected: {breach_type.value} - {breach_data}")

        for callback in self._alert_callbacks:
            try:
                callback(breach_type, breach_data)
            except Exception as e:
                logger.error(f"SLA alert callback error: {e}")
