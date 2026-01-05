from typing import Any, Dict, List, Optional

from ..utils.cache_manager import CacheManager
from ..transport.command_dispatcher import CommandDispatcher
from .facades.commands import CommandFacade
from ..transport.enums import TransportEvent
from ..events.event_manager import EventManager
from .facades.goals import GoalManager
from .facades.missions import MissionFacade
from .facades.processes import ProcessFacade
from .facades.schematics import SchematicManager
from .facades.settings import SettingsFacade
from ..transport.transport import Transport
from ..utils.upload_manager import UploadManager
from .advanced import CommandAnalyticsTracker, ClientRetryPolicyHandler, BridgeMonitor
from ..automator.resource_manager import ResourceManager


class Client:
    """
    Main client for interacting with Baritone.

    Provides facades for commands, processes, goals, settings, schematics, missions,
    caching, uploads, and command dispatching.
    Supports event subscription for real-time updates (WebSocket transport recommended).

    Advanced Phase B features (optional):
    - Health monitoring API for bridge diagnostics
    - Client-side analytics for performance tracking
    - Enhanced error recovery with configurable retry policies

    Example:
        ```python
        from baritone_client import Client, TcpTransport

        transport = TcpTransport(host="localhost", port=5555)
        client = Client(transport)

        # Execute a command
        client.command.run("#goto 100 64 200")

        # Use goals
        from baritone_client import GoalFactory
        goal = GoalFactory.goal_block(100, 64, 200)
        client.goals.apply(goal)

        # Start a process
        client.process.mine.start("diamond_ore", quantity=10)

        # Advanced features (Phase B)
        health = client.get_bridge_health()
        analytics = client.get_command_analytics("mine")
        report = client.get_performance_report()

        # Cleanup
        client.shutdown()
        ```
    """
    
    def __init__(
        self,
        transport: Transport,
        event_manager: Optional[EventManager] = None,
        resource_manager: Optional[ResourceManager] = None
    ) -> None:
        """
        Initialize the client with a transport.

        Args:
            transport: Transport instance (TcpTransport, WebSocketTransport, or Py4JTransport)
            event_manager: Optional EventManager for advanced event handling
            resource_manager: Optional ResourceManager for mission coordination (Phase 2)
        """
        # Phase B Advanced Features (initialize first)
        self._analytics = CommandAnalyticsTracker()
        self._bridge_monitor = BridgeMonitor(transport)
        self._retry_policy = None  # Can be set via set_retry_policy()

        self.event_manager = event_manager or EventManager()
        # Update transport with EventManager if it supports it
        if hasattr(transport, '_event_manager') and transport._event_manager is None:
            transport._event_manager = self.event_manager

        self.transport = transport
        self.command_dispatcher = CommandDispatcher(
            transport,
            retry_policy=self._retry_policy,
            analytics_tracker=self._analytics
        )
        self.command = CommandFacade(transport)
        self.process = ProcessFacade(transport)
        self.goals = GoalManager(transport)
        self.settings = SettingsFacade(transport)
        self.schematics = SchematicManager(transport)
        self.mission = MissionFacade(
            transport=transport,
            resource_manager=resource_manager,
            event_manager=self.event_manager,
            enable_coordination=resource_manager is not None
        )
        self.cache = CacheManager(transport)
        self.upload = UploadManager(transport)

    def on(self, event: TransportEvent, callback) -> None:
        """
        Subscribe to transport events.

        Args:
            event: Event type to subscribe to
            callback: Callback function that receives event payload

        Note:
            TCP transport has limited event support. Use WebSocketTransport for full event support.
        """
        self.transport.subscribe(event, callback)

    def subscribe_events(self, callback, event_types=None, predicate=None):
        """
        Subscribe to events using the EventManager with advanced filtering.

        Args:
            callback: Callback function that receives Event object
            event_types: Optional set of event types to filter by
            predicate: Optional predicate function for additional filtering
        """
        self.event_manager.subscribe(callback, event_types=event_types, predicate=predicate)

    def poll_events(self, event_types=None, predicate=None, max_events=None, remove=True):
        """
        Poll events from the EventManager buffer.

        Args:
            event_types: Optional set of event types to filter by
            predicate: Optional predicate function
            max_events: Maximum number of events to return
            remove: Whether to remove events from buffer

        Returns:
            List of Event objects
        """
        return self.event_manager.poll_events(
            event_types=event_types, predicate=predicate, max_events=max_events, remove=remove
        )

    def get_event_buffer_size(self):
        """Get current event buffer size."""
        return self.event_manager.get_buffer_size()

    # Phase B Advanced Features

    def set_retry_policy(self, policy) -> None:
        """
        Set a client-side retry policy for enhanced error recovery.

        Args:
            policy: Configurable retry policy with backoff strategies and error handling

        Example:
            ```python
            from baritone_client import ClientRetryPolicy

            policy = ClientRetryPolicy(
                max_attempts=5,
                base_delay_seconds=2.0,
                retry_on_errors=["TransportError", "CommandError"]
            )
            client.set_retry_policy(policy)
            ```
        """
        self._retry_policy = ClientRetryPolicyHandler(policy)
        # Update command dispatcher with new retry policy
        if hasattr(self.command_dispatcher, 'retry_policy'):
            self.command_dispatcher.retry_policy = self._retry_policy

    def get_bridge_health(self):
        """
        Retrieve bridge thread pool utilization and system load metrics.

        Returns:
            BridgeHealth object with current system metrics

        Raises:
            TransportError: If unable to communicate with bridge
            CommandError: If bridge returns an error

        Example:
            ```python
            health = client.get_bridge_health()
            print(f"Thread pool utilization: {health.thread_pool_utilization:.1%}")
            ```
        """
        return self._bridge_monitor.get_bridge_health()

    def get_bridge_metrics(self):
        """
        Access performance metrics including response times and error breakdowns.

        Returns:
            BridgeMetrics object with performance statistics

        Raises:
            TransportError: If unable to communicate with bridge
            CommandError: If bridge returns an error

        Example:
            ```python
            metrics = client.get_bridge_metrics()
            print(f"Average response time: {metrics.average_response_time_ms:.1f}ms")
            ```
        """
        return self._bridge_monitor.get_bridge_metrics()

    def get_circuit_breaker_status(self):
        """
        Query circuit breaker state and failure statistics.

        Returns:
            CircuitBreakerStatus object with current circuit breaker state

        Raises:
            TransportError: If unable to communicate with bridge
            CommandError: If bridge returns an error

        Example:
            ```python
            status = client.get_circuit_breaker_status()
            print(f"Circuit breaker state: {status.state}")
            ```
        """
        return self._bridge_monitor.get_circuit_breaker_status()

    def get_command_analytics(self, command_name: str):
        """
        Retrieve performance insights for a specific command.

        Args:
            command_name: Name of the command to analyze

        Returns:
            CommandAnalytics object or None if command not found

        Example:
            ```python
            analytics = client.get_command_analytics("mine")
            if analytics:
                print(f"Success rate: {analytics.successful_calls/analytics.total_calls:.1%}")
            ```
        """
        return self._analytics.get_command_analytics(command_name)

    def get_performance_report(self, slow_threshold_ms: float = 1000.0, error_threshold_rate: float = 0.1):
        """
        Generate comprehensive performance reports with bottleneck detection.

        Args:
            slow_threshold_ms: Threshold for considering commands slow (default: 1000ms)
            error_threshold_rate: Threshold for considering commands error-prone (default: 10%)

        Returns:
            PerformanceReport object with analysis and optimization suggestions

        Example:
            ```python
            report = client.get_performance_report()
            print(f"Overall health score: {report.health_score:.2f}")
            for suggestion in report.bottleneck_suggestions:
                print(f"Suggestion: {suggestion}")
            ```
        """
        return self._analytics.get_performance_report(slow_threshold_ms, error_threshold_rate)

    def shutdown(self) -> None:
        """
        Shutdown the client and close transport connections.

        Should be called when done with the client to clean up resources.
        """
        self.event_manager.shutdown()
        self.transport.shutdown()
