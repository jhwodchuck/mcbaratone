import json
import logging
import os
import threading
import time
from typing import Dict, List, Optional, Set, Union

from .event_manager import Event
from ..transport.enums import TransportEvent

logger = logging.getLogger(__name__)


class EventStorage:
    """Durable event storage for missed events during disconnections."""

    def __init__(
        self,
        storage_path: str = "event_storage.json",
        max_entries: int = 10000,
        max_age_hours: float = 24.0,
        enable_compression: bool = True
    ):
        self.storage_path = storage_path
        self.max_entries = max_entries
        self.max_age_hours = max_age_hours
        self.enable_compression = enable_compression
        self._lock = threading.RLock()
        self._events: List[Event] = []
        self._load_storage()

    def _load_storage(self) -> None:
        """Load events from persistent storage."""
        if not os.path.exists(self.storage_path):
            return

        try:
            with open(self.storage_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if not isinstance(data, list):
                logger.warning("Invalid storage format, initializing empty")
                return

            current_time = time.time()
            max_age_seconds = self.max_age_hours * 3600

            for event_data in data:
                try:
                    # Check if event is too old
                    if current_time - event_data.get('timestamp', 0) > max_age_seconds:
                        continue

                    event = Event(
                        type=event_data['type'],
                        data=event_data['data'],
                        timestamp=event_data['timestamp'],
                        priority=event_data.get('priority', 0),
                        ttl=event_data.get('ttl')
                    )
                    self._events.append(event)
                except (KeyError, TypeError) as e:
                    logger.warning(f"Invalid event data in storage: {e}")

            # Sort by priority and timestamp
            self._events.sort(key=lambda e: (-e.priority, e.timestamp))

            logger.info(f"Loaded {len(self._events)} events from storage")

        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to load event storage: {e}")
            # Initialize empty storage
            self._events = []

    def _save_storage(self) -> None:
        """Save events to persistent storage."""
        try:
            # Convert events to serializable format
            data = []
            current_time = time.time()
            max_age_seconds = self.max_age_hours * 3600

            for event in self._events:
                # Skip expired events
                if event.is_expired():
                    continue

                # Skip events that are too old for storage
                if current_time - event.timestamp > max_age_seconds:
                    continue

                event_dict = {
                    'type': event.type,
                    'data': event.data,
                    'timestamp': event.timestamp,
                    'priority': event.priority,
                    'ttl': event.ttl
                }
                data.append(event_dict)

            # Ensure directory exists
            os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)

            with open(self.storage_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)

            logger.debug(f"Saved {len(data)} events to storage")

        except IOError as e:
            logger.error(f"Failed to save event storage: {e}")

    def store_event(self, event: Event) -> None:
        """Store an event for later retrieval."""
        with self._lock:
            # Add to in-memory storage
            self._events.append(event)

            # Sort by priority and timestamp
            self._events.sort(key=lambda e: (-e.priority, e.timestamp))

            # Remove oldest events if over limit
            if len(self._events) > self.max_entries:
                self._events = self._events[:self.max_entries]

            # Clean expired events
            self._events = [e for e in self._events if not e.is_expired()]

            # Save to disk
            self._save_storage()

    def get_events(
        self,
        event_types: Optional[Set[Union[str, TransportEvent]]] = None,
        min_priority: int = 0,
        max_events: Optional[int] = None,
        since_timestamp: Optional[float] = None
    ) -> List[Event]:
        """Retrieve stored events with optional filtering."""
        with self._lock:
            filtered_events = []

            # Convert event types to strings
            event_type_strings = None
            if event_types:
                event_type_strings = {t.value if isinstance(t, TransportEvent) else t for t in event_types}

            for event in self._events:
                # Filter by event type
                if event_type_strings and event.type not in event_type_strings:
                    continue

                # Filter by priority
                if event.priority < min_priority:
                    continue

                # Filter by timestamp
                if since_timestamp and event.timestamp <= since_timestamp:
                    continue

                # Skip expired events
                if event.is_expired():
                    continue

                filtered_events.append(event)

                # Limit results if requested
                if max_events and len(filtered_events) >= max_events:
                    break

            return filtered_events

    def remove_events(self, event_types: Optional[Set[Union[str, TransportEvent]]] = None) -> int:
        """Remove events from storage, optionally filtering by type."""
        with self._lock:
            if not event_types:
                removed_count = len(self._events)
                self._events.clear()
            else:
                event_type_strings = {t.value if isinstance(t, TransportEvent) else t for t in event_types}
                original_count = len(self._events)
                self._events = [e for e in self._events if e.type not in event_type_strings]
                removed_count = original_count - len(self._events)

            # Save updated storage
            self._save_storage()

            return removed_count

    def get_stats(self) -> Dict[str, int]:
        """Get storage statistics."""
        with self._lock:
            event_types = {}
            priorities = {}
            expired_count = 0

            for event in self._events:
                event_types[event.type] = event_types.get(event.type, 0) + 1
                priorities[event.priority] = priorities.get(event.priority, 0) + 1
                if event.is_expired():
                    expired_count += 1

            return {
                'total_events': len(self._events),
                'event_types': event_types,
                'priorities': priorities,
                'expired_events': expired_count
            }

    def cleanup(self) -> int:
        """Remove expired events and save storage."""
        with self._lock:
            original_count = len(self._events)
            self._events = [e for e in self._events if not e.is_expired()]
            removed_count = original_count - len(self._events)

            if removed_count > 0:
                self._save_storage()
                logger.info(f"Cleaned up {removed_count} expired events")

            return removed_count