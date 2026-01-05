"""
Heartbeat Transport Implementation
"""

import threading
import time
import logging
from typing import Dict, List, Optional, Any, Callable

from ...core.exceptions import TransportError
from ..enums import TransportEvent
from ..transport import Transport
from .types import HeartbeatConfig
from .metrics import ConnectionMetrics

logger = logging.getLogger(__name__)


class HeartbeatTransport:
    """Transport decorator that adds heartbeat protocol capabilities."""

    def __init__(self, transport: Transport, config: Optional[HeartbeatConfig] = None):
        self.transport = transport
        self.config = config or HeartbeatConfig()
        self.metrics = ConnectionMetrics()

        # Heartbeat state
        self._heartbeat_active = False
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._shutdown_event = threading.Event()
        self._heartbeat_lock = threading.RLock()

        # Callbacks
        self._health_callbacks: List[Callable[[ConnectionMetrics], None]] = []

    def start_heartbeat(self) -> None:
        """Start the heartbeat monitoring thread."""
        if self._heartbeat_active:
            return

        self._heartbeat_active = True
        self._shutdown_event.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name="HeartbeatMonitor"
        )
        self._heartbeat_thread.start()
        logger.info("Heartbeat monitoring started")

    def stop_heartbeat(self) -> None:
        """Stop the heartbeat monitoring."""
        if not self._heartbeat_active:
            return

        self._heartbeat_active = False
        self._shutdown_event.set()

        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=5.0)

        logger.info("Heartbeat monitoring stopped")

    def _heartbeat_loop(self) -> None:
        """Main heartbeat monitoring loop."""
        while not self._shutdown_event.is_set():
            try:
                self._send_heartbeat()
            except Exception as e:
                logger.debug(f"Heartbeat failed: {e}")
                self.metrics.record_heartbeat(sent=True, received=False)

            # Notify health callbacks
            try:
                for callback in self._health_callbacks:
                    callback(self.metrics)
            except Exception as e:
                logger.error(f"Health callback error: {e}")

            self._shutdown_event.wait(self.config.interval)

    def _send_heartbeat(self) -> None:
        """Send a heartbeat ping and measure response."""
        start_time = time.time()

        try:
            # Create lightweight ping payload
            ping_payload = {
                "type": "ping",
                "timestamp": start_time,
                "payload": "x" * min(self.config.payload_size, 1024)  # Limit size
            }

            # Send ping via transport
            response = self.transport.dispatch("heartbeat/ping", ping_payload, timeout=self.config.timeout)

            response_time = time.time() - start_time

            # Validate pong response
            if (response.get("type") == "pong" and
                response.get("ping_timestamp") == start_time):

                self.metrics.record_response_time(response_time)
                self.metrics.record_heartbeat(sent=True, received=True)
                logger.debug(f"Heartbeat success: {response_time*1000:.2f}ms")
            else:
                raise TransportError("Invalid pong response")

        except Exception as e:
            response_time = time.time() - start_time
            self.metrics.record_error()
            self.metrics.record_heartbeat(sent=True, received=False)
            logger.debug(f"Heartbeat error: {e}")

    def add_health_callback(self, callback: Callable[[ConnectionMetrics], None]) -> None:
        """Add a callback for health metric updates."""
        self._health_callbacks.append(callback)

    def get_metrics(self) -> ConnectionMetrics:
        """Get current connection metrics."""
        return self.metrics

    def dispatch(self, route: str, payload: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
        """Dispatch with metrics tracking."""
        start_time = time.time()

        try:
            result = self.transport.dispatch(route, payload, timeout)
            response_time = time.time() - start_time
            self.metrics.record_response_time(response_time)
            return result

        except Exception as e:
            response_time = time.time() - start_time
            self.metrics.record_error()
            raise

    def subscribe(self, event: TransportEvent, callback: Callable) -> None:
        """Delegate subscription to underlying transport."""
        self.transport.subscribe(event, callback)

    def emit(self, event: TransportEvent, payload: Dict[str, Any]) -> None:
        """Delegate event emission to underlying transport."""
        self.transport.emit(event, payload)

    def shutdown(self) -> None:
        """Shutdown heartbeat and underlying transport."""
        self.stop_heartbeat()
        self.transport.shutdown()
