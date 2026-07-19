"""Python facade for Baritone control.

This package provides a Python client for controlling Baritone, a Minecraft pathfinding mod.
It supports multiple transport protocols (TCP, WebSocket, Py4J) and provides a clean API
for executing commands, managing processes, setting goals, and uploading schematics.

Example:
    ```python
    from baritone_client import Client, TcpTransport, GoalFactory
    
    # Connect to Baritone bridge
    transport = TcpTransport(host="localhost", port=5555)
    client = Client(transport)
    
    # Set a goal
    goal = GoalFactory.goal_block(100, 64, 200)
    client.goals.apply(goal)
    
    # Start mining
    client.process.mine.start("diamond_ore", quantity=10)
    
    # Cleanup
    client.shutdown()
    ```
"""

from .core.client import Client
from .core.facades.missions import MissionFacade
from .transport.command_dispatcher import CommandDispatcher, CommandResult
from .transport.enums import MovementStatus, PathCalculationResultType, PathingCommandType, TransportEvent
from .core.exceptions import CircuitBreakerOpenError, CommandError, RetryExhaustedError, RouteError, TransportError, ValidationError
from .core.facades.goals import GoalFactory, GoalManager
from .events.lifecycle import Ticker
from .models.models import (
    BetterBlockPos, BlockPos, Goal, Selection,
    BridgeHealth, BridgeMetrics, CircuitBreakerStatus,
    CommandAnalytics, PerformanceReport, ClientRetryPolicy,
    PriorityLevel, BatchCommand, BatchRequest, BatchCommandResult, BatchResult
)
from .core.facades.processes import ProcessFacade
from .core.facades.schematics import SchematicManager
from .scripts.endgame import EndGameMission, MissionPhase, MissionState
from .transport.transport import Py4JTransport, TcpTransport, Transport, WebSocketTransport
from .transport.transport_manager import TransportManager
from .utils.cache_manager import CacheManager, CacheStats
from .utils.upload_manager import UploadManager, UploadPriority, UploadProgress, UploadStatus
from .chat_control import FollowController, FollowCommandConfig, parse_chat_command
from .world_identity import WorldIdentity

__all__ = [
    "Client",
    "Transport",
    "Py4JTransport",
    "TcpTransport",
    "WebSocketTransport",
    "TransportManager",
    "CommandDispatcher",
    "CommandResult",
    "CacheManager",
    "CacheStats",
    "UploadManager",
    "UploadPriority",
    "UploadProgress",
    "UploadStatus",
    "GoalFactory",
    "GoalManager",
    "ProcessFacade",
    "SchematicManager",
    "BlockPos",
    "BetterBlockPos",
    "Selection",
    "Goal",
    "MovementStatus",
    "PathCalculationResultType",
    "PathingCommandType",
    "TransportEvent",
    "Ticker",
    "CircuitBreakerOpenError",
    "CommandError",
    "RetryExhaustedError",
    "RouteError",
    "TransportError",
    "ValidationError",
    "MissionFacade",
    "EndGameMission",
    "MissionState",
    "MissionPhase",
    # Phase B Advanced Features
    "BridgeHealth",
    "BridgeMetrics",
    "CircuitBreakerStatus",
    "CommandAnalytics",
    "PerformanceReport",
    "ClientRetryPolicy",
    # Batch Operations
    "PriorityLevel",
    "BatchCommand",
    "BatchRequest",
    "BatchCommandResult",
    "BatchResult",
    "FollowController",
    "FollowCommandConfig",
    "parse_chat_command",
    "WorldIdentity",
]


def create_client(transport_type: str = "tcp", failover: bool = False, **kwargs) -> Client:
    """
    Convenience function to create a client with transport failover support.

    Args:
        transport_type: Primary transport type ("tcp", "websocket", "py4j", or "auto")
            - "auto": Use TransportManager with automatic failover (WebSocket → TCP → Py4J)
        failover: Enable failover for single transport types (ignored when transport_type="auto")
        **kwargs: Transport-specific parameters
            - For TCP: host (default: "localhost"), port (default: 5555), timeout (optional)
            - For WebSocket: url (required), timeout (optional)
            - For Py4J: gateway_params (optional)
            - For auto/failover: websocket_url (optional), tcp_host/tcp_port (optional),
              py4j_gateway_params (optional), health_check_interval (optional),
              failover_cooldown (optional), max_retry_attempts (optional)

    Returns:
        Client instance

    Raises:
        ValueError: If transport_type is invalid or required parameters missing
        ImportError: If required dependencies are missing

    Example:
        ```python
        # TCP transport with failover
        client = create_client("tcp", failover=True, host="localhost", port=5555)

        # WebSocket transport
        client = create_client("websocket", url="ws://localhost:8080")

        # Automatic failover (WebSocket → TCP → Py4J)
        client = create_client("auto",
                              websocket_url="ws://localhost:8080",
                              tcp_host="localhost",
                              tcp_port=5555)
        ```
    """
    if transport_type == "auto":
        # Use TransportManager for automatic failover
        websocket_url = kwargs.get("websocket_url")
        tcp_host = kwargs.get("tcp_host", "localhost")
        tcp_port = kwargs.get("tcp_port", 5555)
        py4j_gateway_params = kwargs.get("py4j_gateway_params")
        health_check_interval = kwargs.get("health_check_interval", 30.0)
        failover_cooldown = kwargs.get("failover_cooldown", 5.0)
        max_retry_attempts = kwargs.get("max_retry_attempts", 3)

        transport_manager = TransportManager(
            websocket_url=websocket_url,
            tcp_host=tcp_host,
            tcp_port=tcp_port,
            py4j_gateway_params=py4j_gateway_params,
            health_check_interval=health_check_interval,
            failover_cooldown=failover_cooldown,
            max_retry_attempts=max_retry_attempts
        )
        return Client(transport_manager)

    elif transport_type in ("tcp", "websocket", "py4j"):
        if failover:
            # Use TransportManager for single transport with failover
            websocket_url = kwargs.get("websocket_url") if transport_type == "websocket" else None
            tcp_host = kwargs.get("tcp_host", "localhost") if transport_type == "tcp" else "localhost"
            tcp_port = kwargs.get("tcp_port", 5555) if transport_type == "tcp" else 5555
            py4j_gateway_params = kwargs.get("py4j_gateway_params") if transport_type == "py4j" else None

            transport_manager = TransportManager(
                websocket_url=websocket_url,
                tcp_host=tcp_host,
                tcp_port=tcp_port,
                py4j_gateway_params=py4j_gateway_params
            )
            return Client(transport_manager)
        else:
            # Use direct transport (backward compatibility)
            if transport_type == "tcp":
                host = kwargs.get("host", "localhost")
                port = kwargs.get("port", 5555)
                timeout = kwargs.get("timeout", 15.0)
                transport = TcpTransport(host=host, port=port, timeout=timeout)
            elif transport_type == "websocket":
                url = kwargs.get("url")
                if not url:
                    raise ValueError("WebSocket transport requires 'url' parameter")
                timeout = kwargs.get("timeout", 15.0)
                transport = WebSocketTransport(url=url, timeout=timeout)
            elif transport_type == "py4j":
                gateway_params = kwargs.get("gateway_params")
                transport = Py4JTransport(gateway_params=gateway_params)

            return Client(transport)

    else:
        raise ValueError(f"Unknown transport type: {transport_type}. Use 'tcp', 'websocket', 'py4j', or 'auto'")
