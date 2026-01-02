from typing import Any, Callable, Dict, Optional, Set, Union

from ..transport.enums import TransportEvent
from .event_manager import EventManager


class EventRegistry:
    """Legacy compatibility layer for EventManager."""

    def __init__(self, event_manager: Optional[EventManager] = None) -> None:
        self.event_manager = event_manager or EventManager()

    def subscribe(self, event: TransportEvent, callback: Callable[[Dict[str, Any]], None]) -> None:
        self.event_manager.subscribe(
            callback=lambda e: callback(e.data),
            event_types={event}
        )

    def dispatch(self, event: TransportEvent, payload: Dict[str, Any]) -> None:
        self.event_manager.publish_event(event, payload)
