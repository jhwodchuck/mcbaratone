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

from .client import Client
from .enums import MovementStatus, PathCalculationResultType, PathingCommandType, TransportEvent
from .exceptions import CommandError, RouteError, TransportError, ValidationError
from .goals import GoalFactory, GoalManager
from .lifecycle import Ticker
from .models import BetterBlockPos, BlockPos, Goal, Selection
from .processes import ProcessFacade
from .schematics import SchematicManager
from .transport import Py4JTransport, TcpTransport, Transport, WebSocketTransport

__all__ = [
    "Client",
    "Transport",
    "Py4JTransport",
    "TcpTransport",
    "WebSocketTransport",
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
    "CommandError",
    "RouteError",
    "TransportError",
    "ValidationError",
]


def create_client(transport_type: str = "tcp", **kwargs) -> Client:
    """
    Convenience function to create a client with a transport.
    
    Args:
        transport_type: Type of transport ("tcp", "websocket", or "py4j")
        **kwargs: Transport-specific parameters
            - For TCP: host (default: "localhost"), port (default: 5555)
            - For WebSocket: url (required)
            - For Py4J: gateway_params (optional)
    
    Returns:
        Client instance
    
    Raises:
        ValueError: If transport_type is invalid
        ImportError: If required dependencies are missing
    
    Example:
        ```python
        # TCP transport
        client = create_client("tcp", host="localhost", port=5555)
        
        # WebSocket transport
        client = create_client("websocket", url="ws://localhost:8080")
        ```
    """
    if transport_type == "tcp":
        host = kwargs.get("host", "localhost")
        port = kwargs.get("port", 5555)
        transport = TcpTransport(host=host, port=port)
    elif transport_type == "websocket":
        url = kwargs.get("url")
        if not url:
            raise ValueError("WebSocket transport requires 'url' parameter")
        transport = WebSocketTransport(url=url)
    elif transport_type == "py4j":
        gateway_params = kwargs.get("gateway_params")
        transport = Py4JTransport(gateway_params=gateway_params)
    else:
        raise ValueError(f"Unknown transport type: {transport_type}. Use 'tcp', 'websocket', or 'py4j'")
    
    return Client(transport)
