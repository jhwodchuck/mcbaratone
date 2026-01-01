from typing import Any, Dict, List, Optional

from .enums import TransportEvent
from .exceptions import ValidationError
from .goals import GoalManager
from .processes import ProcessFacade
from .schematics import SchematicManager
from .serialization import validate_setting
from .transport import Transport


class CommandFacade:
    """Facade for executing Baritone commands."""
    
    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    def run(self, command: str) -> Dict[str, Any]:
        """
        Execute a Baritone command string.
        
        Args:
            command: Command string (e.g., "#goto 100 64 200" or "goto 100 64 200")
        
        Returns:
            Response dictionary from the bridge
        
        Raises:
            ValidationError: If command is empty
            CommandError: If command execution fails
            TransportError: If transport fails
        """
        if not command or not command.strip():
            raise ValidationError("Command cannot be empty", field="command")
        return self.transport.dispatch("command/run", {"command": command})
    
    def explore(self, x: int = 0, z: int = 0) -> Dict[str, Any]:
        """
        Start exploration process.
        
        Args:
            x: Starting X coordinate (default: 0)
            z: Starting Z coordinate (default: 0)
        
        Returns:
            Response dictionary
        """
        return self.transport.dispatch("command/explore", {"x": x, "z": z})
    
    def follow(self, entity: str = "player") -> Dict[str, Any]:
        """
        Follow an entity.
        
        Args:
            entity: Entity name or "player" (default: "player")
        
        Returns:
            Response dictionary
        """
        return self.transport.dispatch("command/follow", {"entity": entity})
    
    def cancel(self) -> Dict[str, Any]:
        """
        Cancel current operation.
        
        Returns:
            Response dictionary
        """
        return self.transport.dispatch("command/cancel", {})
    
    def get_block(self, x: int, y: int, z: int) -> Dict[str, Any]:
        """
        Get block information at coordinates.
        
        Args:
            x: X coordinate
            y: Y coordinate
            z: Z coordinate
        
        Returns:
            Block information dictionary
        """
        return self.transport.dispatch("command/get_block", {"x": x, "y": y, "z": z})

    def craft(self, recipe_id: str, count: int = 1) -> Dict[str, Any]:
        """
        Craft an item using a bridge-supported recipe.

        Args:
            recipe_id: Identifier for the recipe on the bridge side.
            count: Number of times to craft.

        Returns:
            Response data from the bridge.
        """
        payload = {"recipe_id": recipe_id, "count": count}
        return self.transport.dispatch("craft", payload)

    def smelt(self, input_item: str, count: int = 1, fuel_item: str = "minecraft:coal") -> Dict[str, Any]:
        """
        Smelt items in a furnace (bridge stub).

        Args:
            input_item: Item id to smelt.
            count: Number of items to smelt.
            fuel_item: Fuel item id (defaults to coal).
        """
        payload = {"input_item": input_item, "count": count, "fuel_item": fuel_item}
        return self.transport.dispatch("smelt", payload)


class SettingsFacade:
    """Facade for managing Baritone settings."""
    
    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    def set(self, name: str, value: Any) -> Dict[str, Any]:
        """
        Set a Baritone setting.
        
        Args:
            name: Setting name
            value: Setting value (must be str, bool, int, or float)
        
        Returns:
            Response dictionary
        
        Raises:
            ValidationError: If value type is invalid
            CommandError: If setting fails
        """
        validated_value = validate_setting(name, value)
        return self.transport.dispatch("settings/set", {"name": name, "value": validated_value})

    def get(self, name: str) -> Dict[str, Any]:
        """
        Get a Baritone setting value.
        
        Args:
            name: Setting name
        
        Returns:
            Response dictionary containing setting value
        
        Raises:
            CommandError: If setting retrieval fails
        """
        return self.transport.dispatch("settings/get", {"name": name})

    def reset(self, name: Optional[str] = None) -> Dict[str, Any]:
        """
        Reset Baritone setting(s) to default.
        
        Args:
            name: Setting name to reset, or None to reset all settings
        
        Returns:
            Response dictionary
        """
        payload: Dict[str, Any] = {}
        if name:
            payload["name"] = name
        return self.transport.dispatch("settings/reset", payload)


class MissionFacade:
    """Facade for bridge-side mission coordination helpers."""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    def status(self) -> Dict[str, Any]:
        """Retrieve aggregated mission telemetry."""
        return self.transport.dispatch("mission/status", {})

    def checkpoint(self, phase: str, note: Optional[str] = None) -> Dict[str, Any]:
        """
        Update the bridge's mission checkpoint.

        Args:
            phase: Mission phase identifier
            note: Optional note or context
        """
        payload: Dict[str, Any] = {"phase": phase}
        if note:
            payload["note"] = note
        return self.transport.dispatch("mission/checkpoint", payload)

    def queue(self, actions: List[str], clear: bool = False) -> Dict[str, Any]:
        """Queue macros on the bridge for later execution."""
        payload: Dict[str, Any] = {"actions": actions}
        if clear:
            payload["clear"] = True
        return self.transport.dispatch("mission/queue", payload)

    def macro(
        self,
        name: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        dequeue: bool = False,
    ) -> Dict[str, Any]:
        """
        Trigger a macro on the bridge.

        Args:
            name: Macro identifier. If omitted and dequeue=True, the oldest queued macro runs.
            params: Optional parameters forwarded to the bridge.
            dequeue: Whether to consume the earliest queued macro before execution.
        """
        payload: Dict[str, Any] = {}
        if name:
            payload["name"] = name
        if params:
            payload["params"] = params
        if dequeue:
            payload["dequeue"] = True
        return self.transport.dispatch("mission/macro", payload)


class Client:
    """
    Main client for interacting with Baritone.
    
    Provides facades for commands, processes, goals, settings, and schematics.
    Supports event subscription for real-time updates (WebSocket transport recommended).
    
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
        
        # Cleanup
        client.shutdown()
        ```
    """
    
    def __init__(self, transport: Transport) -> None:
        """
        Initialize the client with a transport.
        
        Args:
            transport: Transport instance (TcpTransport, WebSocketTransport, or Py4JTransport)
        """
        self.transport = transport
        self.command = CommandFacade(transport)
        self.process = ProcessFacade(transport)
        self.goals = GoalManager(transport)
        self.settings = SettingsFacade(transport)
        self.schematics = SchematicManager(transport)
        self.mission = MissionFacade(transport)

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

    def shutdown(self) -> None:
        """
        Shutdown the client and close transport connections.
        
        Should be called when done with the client to clean up resources.
        """
        self.transport.shutdown()
