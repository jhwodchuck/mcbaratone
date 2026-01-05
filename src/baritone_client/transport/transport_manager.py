"""Transport manager with failover logic and health monitoring."""

import logging
import threading
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Union

from ..events.event_manager import EventManager
from .enums import TransportEvent
from .health_monitor import (
    CircuitBreaker, ConnectionMetrics, FailoverManager, HealthPolicy,
    HeartbeatConfig, HeartbeatTransport, QualityMetricsStreamer, SLAMonitor
)
from .transport import Py4JTransport, TcpTransport, Transport, WebSocketTransport

logger = logging.getLogger(__name__)


class TransportState(Enum):
    """Transport connection states."""
    CONNECTED = "connected"
    CONNECTING = "connecting"
    DISCONNECTED = "disconnected"
    FAILED = "failed"


class TransportPriority(Enum):
    """Transport priority order for failover."""
    WEBSOCKET = 1
    TCP = 2
    PY4J = 3


class TransportHealth:
    """Health status of a transport."""

    def __init__(self, transport_name: str):
        self.transport_name = transport_name
        self.state = TransportState.DISCONNECTED
        self.last_successful_operation = 0.0
        self.last_failure_time = 0.0
        self.failure_count = 0
        self.response_time_avg = 0.0
        self.total_operations = 0

    def record_success(self, response_time: float) -> None:
        """Record a successful operation."""
        self.state = TransportState.CONNECTED
        self.last_successful_operation = time.time()
        self.total_operations += 1
        # Update rolling average response time
        self.response_time_avg = (self.response_time_avg * (self.total_operations - 1) + response_time) / self.total_operations

    def record_failure(self) -> None:
        """Record a failed operation."""
        self.state = TransportState.FAILED
        self.last_failure_time = time.time()
        self.failure_count += 1
        self.total_operations += 1

    def is_healthy(self, health_check_interval: float = 30.0) -> bool:
        """Check if transport is considered healthy."""
        current_time = time.time()

        # If we haven't tried recently, assume healthy
        if self.total_operations == 0:
            return True

        # If last operation was successful and recent, consider healthy
        if (self.state == TransportState.CONNECTED and
            current_time - self.last_successful_operation < health_check_interval):
            return True

        # If failures are recent but not too many, might still be ok
        if (self.failure_count > 0 and
            current_time - self.last_failure_time > health_check_interval):
            return True

        return False

    def get_health_score(self) -> float:
        """Get a health score between 0.0 (unhealthy) and 1.0 (healthy)."""
        if self.total_operations == 0:
            return 0.5  # Neutral for unused transports

        success_rate = 1.0 - (self.failure_count / self.total_operations)
        recency_factor = min(1.0, (time.time() - self.last_successful_operation) / 60.0) if self.last_successful_operation > 0 else 0.0

        # Weight: 60% success rate, 40% recency
        return (success_rate * 0.6) + ((1.0 - recency_factor) * 0.4)


class TransportManager:
    """Manages multiple transport instances with automatic failover and advanced health monitoring.

    Supports WebSocket → TCP → Py4J failover hierarchy with configurable retry logic.
    Features advanced health monitoring with heartbeat, quality metrics, circuit breakers,
    and SLA monitoring for production-grade connection management.
    """

    def __init__(self,
                 websocket_url: Optional[str] = None,
                 tcp_host: str = "localhost",
                 tcp_port: int = 5555,
                 py4j_gateway_params: Optional[Dict[str, Any]] = None,
                 event_manager: Optional[EventManager] = None,
                 health_check_interval: float = 30.0,
                 failover_cooldown: float = 5.0,
                 max_retry_attempts: int = 3,
                 enable_advanced_monitoring: bool = True,
                 heartbeat_config: Optional[HeartbeatConfig] = None,
                 health_policy: Optional[HealthPolicy] = None,
                 enable_metrics_streaming: bool = True,
                 metrics_retention_hours: float = 1.0,
                 enable_sla_monitoring: bool = True):
        """
        Initialize TransportManager with transport configurations.

        Args:
            websocket_url: WebSocket URL (e.g., "ws://localhost:8080"). If None, WebSocket is disabled.
            tcp_host: TCP host for fallback transport
            tcp_port: TCP port for fallback transport
            py4j_gateway_params: Py4J gateway parameters for final fallback
            event_manager: Optional EventManager for event handling
            health_check_interval: Seconds between health checks
            failover_cooldown: Minimum seconds between failover attempts
            max_retry_attempts: Maximum retry attempts per transport before failover
            enable_advanced_monitoring: Enable advanced health monitoring features
            heartbeat_config: Heartbeat protocol configuration
            health_policy: Health monitoring policy with thresholds
            enable_metrics_streaming: Enable real-time metrics streaming
            metrics_retention_hours: Hours to retain historical metrics
            enable_sla_monitoring: Enable SLA monitoring and alerting
        """
        self.websocket_url = websocket_url
        self.tcp_host = tcp_host
        self.tcp_port = tcp_port
        self.py4j_gateway_params = py4j_gateway_params or {}
        self.event_manager = event_manager
        self.health_check_interval = health_check_interval
        self.failover_cooldown = failover_cooldown
        self.max_retry_attempts = max_retry_attempts
        self.enable_advanced_monitoring = enable_advanced_monitoring

        # Advanced monitoring components
        self.heartbeat_config = heartbeat_config or HeartbeatConfig()
        self.health_policy = health_policy or HealthPolicy()
        self.enable_metrics_streaming = enable_metrics_streaming
        self.metrics_retention_hours = metrics_retention_hours
        self.enable_sla_monitoring = enable_sla_monitoring

        self._lock = threading.RLock()
        self._shutdown_event = threading.Event()

        # Transport instances and health tracking
        self._transports: Dict[str, Transport] = {}
        self._transport_health: Dict[str, TransportHealth] = {}  # Backward compatibility
        self._current_transport: Optional[Transport] = None
        self._current_transport_name: Optional[str] = None

        # Advanced monitoring components
        self._heartbeat_transports: Dict[str, HeartbeatTransport] = {}
        self._connection_metrics: Dict[str, ConnectionMetrics] = {}
        self._failover_manager: Optional[FailoverManager] = None
        self._metrics_streamer: Optional[QualityMetricsStreamer] = None
        self._sla_monitor: Optional[SLAMonitor] = None

        # Failover state (backward compatibility)
        self._last_failover_time = 0.0
        self._failover_in_progress = False
        self._health_monitor_thread: Optional[threading.Thread] = None

        # Initialize transports
        self._initialize_transports()

        # Initialize advanced monitoring if enabled
        if self.enable_advanced_monitoring:
            self._initialize_advanced_monitoring()

        # Start health monitoring
        self._start_health_monitor()

    def _initialize_advanced_monitoring(self) -> None:
        """Initialize advanced health monitoring components."""
        # Initialize failover manager
        self._failover_manager = FailoverManager(self.health_policy)

        # Initialize metrics streamer if enabled
        if self.enable_metrics_streaming:
            self._metrics_streamer = QualityMetricsStreamer(
                retention_period=self.metrics_retention_hours * 3600
            )

        # Initialize SLA monitor if enabled
        if self.enable_sla_monitoring:
            self._sla_monitor = SLAMonitor(self.health_policy)

        # Wrap transports with heartbeat functionality
        for transport_name, transport in self._transports.items():
            heartbeat_transport = HeartbeatTransport(transport, self.heartbeat_config)

            # Register with failover manager
            self._failover_manager.register_transport(transport_name)

            # Connect metrics updates
            if self._metrics_streamer:
                heartbeat_transport.add_health_callback(
                    lambda metrics, name=transport_name: self._update_metrics(name, metrics)
                )

            # Connect SLA monitoring
            if self._sla_monitor:
                heartbeat_transport.add_health_callback(
                    lambda metrics, name=transport_name: self._check_sla_compliance(name, metrics)
                )

            self._heartbeat_transports[transport_name] = heartbeat_transport
            self._connection_metrics[transport_name] = heartbeat_transport.get_metrics()

    def _initialize_transports(self) -> None:
        """Initialize available transport instances."""
        # WebSocket transport (highest priority)
        if self.websocket_url:
            try:
                self._transports["websocket"] = WebSocketTransport(
                    url=self.websocket_url,
                    event_manager=self.event_manager
                )
                self._transport_health["websocket"] = TransportHealth("websocket")
                logger.info(f"Initialized WebSocket transport: {self.websocket_url}")
            except Exception as e:
                logger.warning(f"Failed to initialize WebSocket transport: {e}")

        # TCP transport (medium priority)
        try:
            self._transports["tcp"] = TcpTransport(
                host=self.tcp_host,
                port=self.tcp_port,
                event_manager=self.event_manager
            )
            self._transport_health["tcp"] = TransportHealth("tcp")
            logger.info(f"Initialized TCP transport: {self.tcp_host}:{self.tcp_port}")
        except Exception as e:
            logger.warning(f"Failed to initialize TCP transport: {e}")

        # Py4J transport (lowest priority)
        try:
            self._transports["py4j"] = Py4JTransport(
                gateway_params=self.py4j_gateway_params,
                event_manager=self.event_manager
            )
            self._transport_health["py4j"] = TransportHealth("py4j")
            logger.info("Initialized Py4J transport")
        except Exception as e:
            logger.warning(f"Failed to initialize Py4J transport: {e}")

        # Select initial transport
        self._select_best_transport()

    def _start_health_monitor(self) -> None:
        """Start background health monitoring thread."""
        self._health_monitor_thread = threading.Thread(
            target=self._health_monitor_loop,
            daemon=True,
            name="TransportHealthMonitor"
        )
        self._health_monitor_thread.start()

    def _health_monitor_loop(self) -> None:
        """Background loop for health monitoring and failover."""
        while not self._shutdown_event.is_set():
            try:
                self._perform_health_checks()
                self._check_failover_needed()
            except Exception as e:
                logger.error(f"Health monitor error: {e}")
            finally:
                self._shutdown_event.wait(self.health_check_interval)

    def _update_metrics(self, transport_name: str, metrics: ConnectionMetrics) -> None:
        """Update metrics for a transport and trigger monitoring."""
        if self._failover_manager:
            self._failover_manager.update_metrics(transport_name, metrics)

        # Update legacy health for backward compatibility
        if transport_name in self._transport_health:
            health = self._transport_health[transport_name]
            if metrics.last_successful_operation > 0:
                # Legacy health uses simple average
                health.response_time_avg = metrics.p50_latency or metrics.response_times[-1] if metrics.response_times else 0.0
                health.last_successful_operation = metrics.last_successful_operation
                health.state = TransportState.CONNECTED
            elif metrics.consecutive_errors > 0:
                health.record_failure()

    def _check_sla_compliance(self, transport_name: str, metrics: ConnectionMetrics) -> None:
        """Check SLA compliance for a transport."""
        if self._sla_monitor:
            self._sla_monitor.check_sla_compliance(metrics)

    def _perform_health_checks(self) -> None:
        """Perform health checks on all available transports."""
        current_time = time.time()

        for transport_name, transport in self._transports.items():
            if transport_name == self._current_transport_name:
                continue  # Skip health check on active transport

            health = self._transport_health[transport_name]

            # Use heartbeat transport for advanced monitoring if available
            check_transport = self._heartbeat_transports.get(transport_name, transport)

            # Simple health check: try to dispatch a lightweight command
            try:
                start_time = time.time()
                # Use a simple status check - adjust route as needed
                check_transport.dispatch("process/status", {}, timeout=2.0)
                response_time = time.time() - start_time
                health.record_success(response_time)
                logger.debug(f"Health check passed for {transport_name}")
            except Exception as e:
                health.record_failure()
                logger.debug(f"Health check failed for {transport_name}: {e}")

    def _check_failover_needed(self) -> None:
        """Check if failover to a better transport is needed."""
        if self._failover_in_progress:
            return

        current_time = time.time()
        if current_time - self._last_failover_time < self.failover_cooldown:
            return

        # Use advanced failover manager if available
        if self._failover_manager and self._current_transport_name:
            available_transports = set(self._transports.keys())
            failover_target = self._failover_manager.should_failover(
                self._current_transport_name, available_transports
            )
            if failover_target:
                logger.info(f"Advanced failover: {self._current_transport_name} -> {failover_target}")
                self._perform_failover(failover_target)
                return

        # Fallback to legacy failover logic
        best_transport_name = self._get_best_transport_name()
        if best_transport_name and best_transport_name != self._current_transport_name:
            logger.info(f"Legacy failover: {self._current_transport_name} to {best_transport_name}")
            self._perform_failover(best_transport_name)

    def _get_best_transport_name(self) -> Optional[str]:
        """Get the name of the best available transport based on health and priority."""
        available_transports = [
            (name, health.get_health_score())
            for name, health in self._transport_health.items()
            if name in self._transports and health.is_healthy()
        ]

        if not available_transports:
            return None

        # Sort by health score (descending), then by priority (ascending)
        available_transports.sort(key=lambda x: (-x[1], TransportPriority[x[0].upper()].value))

        return available_transports[0][0]

    def _select_best_transport(self) -> None:
        """Select the best available transport as current."""
        best_transport_name = self._get_best_transport_name()

        if best_transport_name:
            with self._lock:
                self._current_transport = self._transports[best_transport_name]
                self._current_transport_name = best_transport_name
            logger.info(f"Selected transport: {best_transport_name}")
        else:
            logger.warning("No healthy transports available")
            with self._lock:
                self._current_transport = None
                self._current_transport_name = None

    def _perform_failover(self, target_transport_name: str) -> None:
        """Perform failover to the specified transport."""
        if target_transport_name not in self._transports:
            logger.error(f"Cannot failover to unknown transport: {target_transport_name}")
            return

        self._failover_in_progress = True
        self._last_failover_time = time.time()

        try:
            # Test the target transport
            target_transport = self._transports[target_transport_name]
            target_transport.dispatch("process/status", {}, timeout=5.0)

            # Switch to target transport
            with self._lock:
                old_transport = self._current_transport_name
                self._current_transport = target_transport
                self._current_transport_name = target_transport_name

            logger.info(f"Successfully failed over from {old_transport} to {target_transport_name}")

        except Exception as e:
            logger.error(f"Failover to {target_transport_name} failed: {e}")
            # Mark target transport as unhealthy
            if target_transport_name in self._transport_health:
                self._transport_health[target_transport_name].record_failure()
        finally:
            self._failover_in_progress = False

    def dispatch(self, route: str, payload: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
        """Dispatch request through current transport with automatic retry/failover."""
        if not self._current_transport:
            raise Exception("No transport available")

        max_attempts = self.max_retry_attempts
        last_exception = None

        for attempt in range(max_attempts):
            try:
                # Use heartbeat transport for advanced monitoring if available
                transport = self._heartbeat_transports.get(self._current_transport_name, self._current_transport)

                start_time = time.time()
                result = transport.dispatch(route, payload, timeout)
                response_time = time.time() - start_time

                # Record success in advanced monitoring
                if self.enable_advanced_monitoring and self._current_transport_name:
                    if self._failover_manager:
                        self._failover_manager.record_transport_success(self._current_transport_name)
                    # Metrics are automatically updated via heartbeat callbacks

                # Record success in legacy monitoring for backward compatibility
                if self._current_transport_name and self._current_transport_name in self._transport_health:
                    self._transport_health[self._current_transport_name].record_success(response_time)

                return result

            except Exception as e:
                last_exception = e
                logger.warning(f"Transport dispatch attempt {attempt + 1} failed: {e}")

                # Record failure in advanced monitoring
                if self.enable_advanced_monitoring and self._current_transport_name:
                    if self._failover_manager:
                        self._failover_manager.record_transport_failure(self._current_transport_name)

                # Record failure in legacy monitoring
                if self._current_transport_name and self._current_transport_name in self._transport_health:
                    self._transport_health[self._current_transport_name].record_failure()

                # Try failover if not the last attempt
                if attempt < max_attempts - 1:
                    logger.info("Attempting failover after dispatch failure")
                    self._select_best_transport()

                    if not self._current_transport:
                        break

                    # Brief pause before retry
                    time.sleep(0.5)

        # All attempts failed
        raise last_exception or Exception("All transport dispatch attempts failed")

    def subscribe(self, event: TransportEvent, callback) -> None:
        """Subscribe to events on current transport."""
        if self._current_transport:
            self._current_transport.subscribe(event, callback)

    def emit(self, event: TransportEvent, payload: Dict[str, Any]) -> None:
        """Emit event through current transport."""
        if self._current_transport:
            self._current_transport.emit(event, payload)

    def get_current_transport_name(self) -> Optional[str]:
        """Get the name of the currently active transport."""
        return self._current_transport_name

    def get_transport_health(self) -> Dict[str, Dict[str, Any]]:
        """Get health status of all transports."""
        health_status = {}
        for name, health in self._transport_health.items():
            health_status[name] = {
                "state": health.state.value,
                "health_score": health.get_health_score(),
                "total_operations": health.total_operations,
                "failure_count": health.failure_count,
                "average_response_time": health.response_time_avg,
                "is_current": name == self._current_transport_name
            }
        return health_status

    def force_failover(self, target_transport: str) -> bool:
        """Force failover to a specific transport."""
        if target_transport not in self._transports:
            logger.error(f"Unknown transport: {target_transport}")
            return False

        logger.info(f"Forcing failover to {target_transport}")
        self._perform_failover(target_transport)
        return self._current_transport_name == target_transport

    def shutdown(self) -> None:
        """Shutdown all transports and monitoring."""
        self._shutdown_event.set()

        # Shutdown advanced monitoring components
        if self._metrics_streamer:
            self._metrics_streamer.stop_streaming()

        # Shutdown heartbeat transports
        for heartbeat_transport in self._heartbeat_transports.values():
            try:
                heartbeat_transport.stop_heartbeat()
                heartbeat_transport.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down heartbeat transport: {e}")

        # Shutdown base transports
        for transport in self._transports.values():
            try:
                transport.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down transport: {e}")

        # Wait for health monitor to finish
        if self._health_monitor_thread and self._health_monitor_thread.is_alive():
            self._health_monitor_thread.join(timeout=5.0)

    # Enhanced health monitoring API methods

    def get_connection_metrics(self, transport_name: Optional[str] = None) -> Union[ConnectionMetrics, Dict[str, ConnectionMetrics]]:
        """Get detailed connection metrics for transports.

        Args:
            transport_name: Specific transport name, or None for all

        Returns:
            ConnectionMetrics for specific transport, or dict of all metrics
        """
        if not self.enable_advanced_monitoring:
            raise RuntimeError("Advanced monitoring not enabled")

        if transport_name:
            return self._connection_metrics.get(transport_name, ConnectionMetrics())
        else:
            return self._connection_metrics.copy()

    def get_quality_metrics_history(self, transport_name: Optional[str] = None,
                                   start_time: Optional[float] = None,
                                   end_time: Optional[float] = None) -> Union[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
        """Get historical quality metrics.

        Args:
            transport_name: Specific transport name, or None for all
            start_time: Start timestamp for filtering
            end_time: End timestamp for filtering

        Returns:
            List of historical metrics snapshots
        """
        if not self._metrics_streamer:
            return [] if transport_name else {}

        # Note: Current implementation streams all transports together
        # In a more advanced version, we could separate by transport
        history = self._metrics_streamer.get_historical_metrics(start_time, end_time)

        if transport_name:
            # Filter by transport if needed (placeholder for future enhancement)
            return history
        else:
            return {"all_transports": history}  # Simplified for now

    def get_sla_status(self) -> Dict[str, Any]:
        """Get current SLA compliance status."""
        if not self._sla_monitor:
            return {"enabled": False}

        return {
            "enabled": True,
            "recent_breaches": self._sla_monitor.get_recent_breaches(),
            "compliance_status": "checking"  # Would be updated by monitoring
        }

    def add_sla_alert_callback(self, callback: Callable) -> None:
        """Add callback for SLA breach alerts."""
        if self._sla_monitor:
            self._sla_monitor.add_alert_callback(callback)
        else:
            logger.warning("SLA monitoring not enabled")

    def get_failover_status(self) -> Dict[str, Any]:
        """Get current failover manager status."""
        if not self._failover_manager:
            return {"enabled": False}

        status = {}
        for transport_name in self._transports.keys():
            status[transport_name] = self._failover_manager.get_transport_status(transport_name)

        return {
            "enabled": True,
            "transports": status,
            "last_failover": self._last_failover_time,
            "failover_cooldown_remaining": max(0.0, self.failover_cooldown - (time.time() - self._last_failover_time))
        }

    def force_health_check(self, transport_name: Optional[str] = None) -> Dict[str, Any]:
        """Force immediate health check on transports.

        Args:
            transport_name: Specific transport to check, or None for all

        Returns:
            Health check results
        """
        results = {}

        transports_to_check = [transport_name] if transport_name else list(self._transports.keys())

        for name in transports_to_check:
            if name not in self._transports:
                results[name] = {"error": "Transport not found"}
                continue

            transport = self._heartbeat_transports.get(name, self._transports[name])

            try:
                start_time = time.time()
                transport.dispatch("process/status", {}, timeout=5.0)
                response_time = time.time() - start_time
                results[name] = {
                    "healthy": True,
                    "response_time": response_time,
                    "timestamp": time.time()
                }
            except Exception as e:
                results[name] = {
                    "healthy": False,
                    "error": str(e),
                    "timestamp": time.time()
                }

        return results

    def get_advanced_health_report(self) -> Dict[str, Any]:
        """Get comprehensive advanced health report."""
        if not self.enable_advanced_monitoring:
            return {"enabled": False, "error": "Advanced monitoring not enabled"}

        report = {
            "enabled": True,
            "timestamp": time.time(),
            "current_transport": self._current_transport_name,
            "transports": {},
            "failover_status": self.get_failover_status(),
            "sla_status": self.get_sla_status(),
            "metrics_streaming": {
                "enabled": self.enable_metrics_streaming,
                "active": self._metrics_streamer._active if self._metrics_streamer else False,
                "retention_hours": self.metrics_retention_hours
            }
        }

        # Add detailed status for each transport
        for transport_name in self._transports.keys():
            transport_report = {
                "exists": True,
                "current": transport_name == self._current_transport_name,
                "legacy_health": self.get_transport_health().get(transport_name, {}),
            }

            if transport_name in self._connection_metrics:
                metrics = self._connection_metrics[transport_name]
                transport_report["advanced_metrics"] = {
                    "p50_latency": metrics.p50_latency,
                    "p95_latency": metrics.p95_latency,
                    "p99_latency": metrics.p99_latency,
                    "avg_jitter": metrics.avg_jitter,
                    "stability_score": metrics.stability_score,
                    "error_rate": metrics.get_error_rate(),
                    "availability": metrics.get_availability(),
                    "total_requests": metrics.total_requests,
                    "heartbeat_success_rate": (
                        metrics.heartbeat_received / max(metrics.heartbeat_sent, 1)
                    )
                }

            if self._failover_manager:
                transport_report["failover_health"] = self._failover_manager.get_transport_status(transport_name)

            report["transports"][transport_name] = transport_report

        return report