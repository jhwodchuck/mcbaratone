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


class Client:
    """
    Main client for interacting with Baritone.

    Provides facades for commands, processes, goals, settings, schematics, missions,
    caching, uploads, and command dispatching.
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
    
    def __init__(self, transport: Transport, event_manager: Optional[EventManager] = None) -> None:
        """
        Initialize the client with a transport.

        Args:
            transport: Transport instance (TcpTransport, WebSocketTransport, or Py4JTransport)
            event_manager: Optional EventManager for advanced event handling
        """
        self.event_manager = event_manager or EventManager()
        # Update transport with EventManager if it supports it
        if hasattr(transport, '_event_manager') and transport._event_manager is None:
            transport._event_manager = self.event_manager

        self.transport = transport
        self.command_dispatcher = CommandDispatcher(transport)
        self.command = CommandFacade(transport)
        self.process = ProcessFacade(transport)
        self.goals = GoalManager(transport)
        self.settings = SettingsFacade(transport)
        self.schematics = SchematicManager(transport)
        self.mission = MissionFacade(transport)
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

    def shutdown(self) -> None:
        """
        Shutdown the client and close transport connections.

        Should be called when done with the client to clean up resources.
        """
        self.event_manager.shutdown()
        self.transport.shutdown()
