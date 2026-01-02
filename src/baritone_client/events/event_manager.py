import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Union

from ..transport.enums import TransportEvent

logger = logging.getLogger(__name__)


@dataclass
class Event:
    type: Union[str, TransportEvent]
    data: Dict[str, Any]
    timestamp: float
    priority: int = 0  # Higher priority = processed first

    def __post_init__(self):
        if isinstance(self.type, TransportEvent):
            self.type = self.type.value


class EventFilter:
    def __init__(
        self,
        event_types: Optional[Set[Union[str, TransportEvent]]] = None,
        predicate: Optional[Callable[[Event], bool]] = None,
    ):
        self.event_types = event_types or set()
        if self.event_types:
            self.event_types = {t.value if isinstance(t, TransportEvent) else t for t in self.event_types}
        self.predicate = predicate

    def matches(self, event: Event) -> bool:
        if self.event_types and event.type not in self.event_types:
            return False
        if self.predicate and not self.predicate(event):
            return False
        return True


@dataclass
class EventSubscription:
    callback: Callable[[Event], None]
    filter_: Optional[EventFilter] = None


class EventManager:
    def __init__(self, max_buffer_size: int = 1000):
        self.max_buffer_size = max_buffer_size
        self._buffer: deque[Event] = deque(maxlen=max_buffer_size)
        self._subscriptions: List[EventSubscription] = []
        self._lock = threading.RLock()
        self._shutdown_event = threading.Event()

    def publish_event(
        self,
        event_type: Union[str, TransportEvent],
        data: Dict[str, Any],
        priority: int = 0,
        timestamp: Optional[float] = None,
    ) -> None:
        """Publish an event to the buffer and notify subscribers."""
        event = Event(
            type=event_type,
            data=data,
            timestamp=timestamp or time.time(),
            priority=priority,
        )

        with self._lock:
            # Add to buffer (deque will auto-remove oldest if over size)
            self._buffer.append(event)
            logger.debug(f"Event published: {event.type} (buffer size: {len(self._buffer)})")

        # Notify subscribers asynchronously
        self._notify_subscribers(event)

    def _notify_subscribers(self, event: Event) -> None:
        """Notify all subscribers that match the event."""
        to_notify = []
        with self._lock:
            for subscription in self._subscriptions:
                if subscription.filter_ is None or subscription.filter_.matches(event):
                    to_notify.append(subscription.callback)

        # Call callbacks outside lock to avoid deadlocks
        for callback in to_notify:
            try:
                callback(event)
            except Exception:
                logger.exception("Error in event callback")

    def subscribe(
        self,
        callback: Callable[[Event], None],
        event_types: Optional[Set[Union[str, TransportEvent]]] = None,
        predicate: Optional[Callable[[Event], bool]] = None,
    ) -> None:
        """Subscribe to events with optional filtering."""
        filter_ = None
        if event_types or predicate:
            filter_ = EventFilter(event_types=event_types, predicate=predicate)

        subscription = EventSubscription(callback=callback, filter_=filter_)
        with self._lock:
            self._subscriptions.append(subscription)

    def unsubscribe(self, callback: Callable[[Event], None]) -> None:
        """Unsubscribe a callback."""
        with self._lock:
            self._subscriptions = [s for s in self._subscriptions if s.callback != callback]

    def poll_events(
        self,
        event_types: Optional[Set[Union[str, TransportEvent]]] = None,
        predicate: Optional[Callable[[Event], bool]] = None,
        max_events: Optional[int] = None,
        remove: bool = True,
    ) -> List[Event]:
        """Poll events from the buffer with optional filtering."""
        filter_ = None
        if event_types or predicate:
            filter_ = EventFilter(event_types=event_types, predicate=predicate)

        with self._lock:
            if not filter_:
                # Get all events
                events = list(self._buffer)
                if remove:
                    self._buffer.clear()
            else:
                # Filter events
                events = [e for e in self._buffer if filter_.matches(e)]
                if remove:
                    for e in events:
                        try:
                            self._buffer.remove(e)
                        except ValueError:
                            pass  # Already removed or not in buffer

            # Sort by priority (higher first) then timestamp (older first)
            events.sort(key=lambda e: (-e.priority, e.timestamp))

            if max_events:
                events = events[:max_events]

            return events

    def get_buffer_size(self) -> int:
        """Get current buffer size."""
        with self._lock:
            return len(self._buffer)

    def clear_buffer(self) -> None:
        """Clear all events from buffer."""
        with self._lock:
            self._buffer.clear()

    def get_event_types(self) -> Set[str]:
        """Get set of all event types currently in buffer."""
        with self._lock:
            return {e.type for e in self._buffer}

    def shutdown(self) -> None:
        """Shutdown the event manager."""
        self._shutdown_event.set()
        with self._lock:
            self._subscriptions.clear()
            self._buffer.clear()