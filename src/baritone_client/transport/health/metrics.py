"""
Connection Metrics Module
"""

import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable
import logging

logger = logging.getLogger(__name__)


@dataclass
class ConnectionMetrics:
    """Real-time connection quality metrics."""

    # Response time tracking
    response_times: deque = field(default_factory=lambda: deque(maxlen=1000))
    p50_latency: float = 0.0
    p95_latency: float = 0.0
    p99_latency: float = 0.0

    # Jitter calculation
    jitter_samples: deque = field(default_factory=lambda: deque(maxlen=100))
    avg_jitter: float = 0.0

    # Stability scoring
    stability_score: float = 1.0  # 0.0 to 1.0
    stability_window: int = 100

    # Throughput metrics
    bytes_sent: int = 0
    bytes_received: int = 0
    packets_sent: int = 0
    packets_received: int = 0

    # Error tracking
    total_requests: int = 0
    error_count: int = 0
    consecutive_errors: int = 0

    # Heartbeat metrics
    heartbeat_sent: int = 0
    heartbeat_received: int = 0
    missed_heartbeats: int = 0

    # Timestamps
    last_successful_operation: float = 0.0
    last_failure_time: float = 0.0
    uptime_start: float = field(default_factory=time.time)

    def record_response_time(self, response_time: float) -> None:
        """Record a response time measurement."""
        self.response_times.append(response_time)
        self.total_requests += 1
        self.last_successful_operation = time.time()
        self.consecutive_errors = 0

        # Update percentiles
        if len(self.response_times) >= 10:
            sorted_times = sorted(self.response_times)
            n = len(sorted_times)
            self.p50_latency = sorted_times[n // 2]
            self.p95_latency = sorted_times[int(n * 0.95)]
            self.p99_latency = sorted_times[int(n * 0.99)]

        # Update jitter
        if len(self.response_times) >= 2:
            diffs = []
            times_list = list(self.response_times)
            for i in range(1, len(times_list)):
                diffs.append(abs(times_list[i] - times_list[i-1]))
            if diffs:
                self.jitter_samples.append(statistics.mean(diffs))
                if len(self.jitter_samples) >= 5:
                    self.avg_jitter = statistics.mean(self.jitter_samples)

        # Update stability score
        self._calculate_stability_score()

    def record_error(self) -> None:
        """Record an error occurrence."""
        self.error_count += 1
        self.consecutive_errors += 1
        self.last_failure_time = time.time()

    def record_heartbeat(self, sent: bool = True, received: bool = True) -> None:
        """Record heartbeat activity."""
        if sent:
            self.heartbeat_sent += 1
        if received:
            self.heartbeat_received += 1
        else:
            self.missed_heartbeats += 1

    def get_error_rate(self) -> float:
        """Calculate current error rate."""
        if self.total_requests == 0:
            return 0.0
        return self.error_count / self.total_requests

    def get_availability(self) -> float:
        """Calculate availability percentage over monitoring window."""
        if self.total_requests == 0:
            return 1.0
        return 1.0 - self.get_error_rate()

    def _calculate_stability_score(self) -> None:
        """Calculate connection stability score based on variance."""
        if len(self.response_times) < 10:
            self.stability_score = 0.5
            return

        try:
            mean = statistics.mean(self.response_times)
            if mean == 0:
                self.stability_score = 1.0
                return

            variance = statistics.variance(self.response_times)
            cv = (variance ** 0.5) / mean  # Coefficient of variation

            # Convert CV to stability score (lower CV = higher stability)
            self.stability_score = max(0.0, min(1.0, 1.0 - cv))
        except statistics.StatisticsError:
            self.stability_score = 0.5


class QualityMetricsStreamer:
    """Streams real-time quality metrics and maintains historical data."""

    def __init__(self, retention_period: float = 3600.0):  # 1 hour default
        self.retention_period = retention_period
        self._metrics_history: deque = deque()
        self._lock = threading.RLock()
        self._shutdown_event = threading.Event()
        self._streaming_thread: Optional[threading.Thread] = None
        self._active = False

    def start_streaming(self, metrics_source: Callable[[], ConnectionMetrics]) -> None:
        """Start streaming metrics from source."""
        if self._active:
            return

        self._active = True
        self._shutdown_event.clear()
        self._streaming_thread = threading.Thread(
            target=self._streaming_loop,
            args=(metrics_source,),
            daemon=True,
            name="MetricsStreamer"
        )
        self._streaming_thread.start()
        logger.info("Metrics streaming started")

    def stop_streaming(self) -> None:
        """Stop metrics streaming."""
        if not self._active:
            return

        self._active = False
        self._shutdown_event.set()

        if self._streaming_thread and self._streaming_thread.is_alive():
            self._streaming_thread.join(timeout=5.0)

        logger.info("Metrics streaming stopped")

    def _streaming_loop(self, metrics_source: Callable[[], ConnectionMetrics]) -> None:
        """Main streaming loop."""
        while not self._shutdown_event.is_set():
            try:
                metrics = metrics_source()

                # Create snapshot
                snapshot = {
                    "timestamp": time.time(),
                    "p50_latency": metrics.p50_latency,
                    "p95_latency": metrics.p95_latency,
                    "p99_latency": metrics.p99_latency,
                    "avg_jitter": metrics.avg_jitter,
                    "stability_score": metrics.stability_score,
                    "error_rate": metrics.get_error_rate(),
                    "availability": metrics.get_availability(),
                    "total_requests": metrics.total_requests,
                    "error_count": metrics.error_count,
                    "bytes_sent": metrics.bytes_sent,
                    "bytes_received": metrics.bytes_received,
                    "heartbeat_success_rate": (
                        metrics.heartbeat_received / max(metrics.heartbeat_sent, 1)
                    )
                }

                with self._lock:
                    self._metrics_history.append(snapshot)

                    # Clean old data
                    cutoff_time = time.time() - self.retention_period
                    while self._metrics_history and self._metrics_history[0]["timestamp"] < cutoff_time:
                        self._metrics_history.popleft()

            except Exception as e:
                logger.error(f"Metrics streaming error: {e}")

            self._shutdown_event.wait(1.0)  # Stream every second

    def get_current_metrics(self) -> Optional[Dict[str, Any]]:
        """Get most recent metrics snapshot."""
        with self._lock:
            return self._metrics_history[-1] if self._metrics_history else None

    def get_historical_metrics(self, start_time: Optional[float] = None,
                              end_time: Optional[float] = None) -> List[Dict[str, Any]]:
        """Get historical metrics within time range."""
        with self._lock:
            if not self._metrics_history:
                return []

            start_time = start_time or (time.time() - self.retention_period)
            end_time = end_time or time.time()

            return [
                m for m in self._metrics_history
                if start_time <= m["timestamp"] <= end_time
            ]
