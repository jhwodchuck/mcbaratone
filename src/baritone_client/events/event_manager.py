import json
import logging
import threading
import time
import zlib
from collections import deque, defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
from datetime import datetime, timedelta

try:
    import lz4.frame
except ImportError:
    lz4 = None

from ..transport.enums import TransportEvent

logger = logging.getLogger(__name__)


@dataclass
class Event:
    type: Union[str, TransportEvent]
    data: Dict[str, Any]
    timestamp: float
    priority: int = 0  # Higher priority = processed first
    ttl: Optional[float] = None  # Time-to-live in seconds

    def __post_init__(self):
        if isinstance(self.type, TransportEvent):
            self.type = self.type.value

    def is_expired(self) -> bool:
        """Check if the event has expired based on TTL."""
        if self.ttl is None:
            return False
        return time.time() - self.timestamp > self.ttl


@dataclass
class BoundingBox:
    """3D bounding box for coordinate-based filtering."""
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    min_z: float
    max_z: float

    def contains(self, x: float, y: float, z: float) -> bool:
        return (self.min_x <= x <= self.max_x and
                self.min_y <= y <= self.max_y and
                self.min_z <= z <= self.max_z)


@dataclass
class TimeWindow:
    """Time window for filtering events by timestamp."""
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    duration_seconds: Optional[float] = None

    def contains(self, timestamp: float) -> bool:
        current_time = time.time()
        if self.duration_seconds:
            # Relative time window from now
            start = current_time - self.duration_seconds
            end = current_time
        else:
            start = self.start_time or 0
            end = self.end_time or float('inf')

        return start <= timestamp <= end


#: Types with non-standard coordinate fields. Anything absent falls back to
#: top-level x/y/z: the spatial index and the filter path must agree on which
#: events are spatial, or the indexed fast path drops events the unindexed one
#: would have matched.
DEFAULT_COORDINATE_FIELDS: Dict[str, Tuple[str, str, str]] = {
    "player_move": ("x", "y", "z"),
    "block_update": ("x", "y", "z"),
    "entity_move": ("x", "y", "z"),
    "position": ("x", "y", "z"),
}


def resolve_coordinate_fields(event_type: str, mapping: Optional[Dict[str, Tuple[str, str, str]]] = None) -> Tuple[str, str, str]:
    """Return the (x, y, z) field paths to read for an event type."""
    table = DEFAULT_COORDINATE_FIELDS if mapping is None else mapping
    return table.get(event_type, ("x", "y", "z"))


def get_nested_value(data: Dict[str, Any], path_parts: List[str]) -> Any:
    """Get nested value from data using dot-notation path parts.

    Shared by the filters and by EventManager's coordinate extraction, which
    previously called a copy that existed only on the filter classes.
    """
    current = data
    for part in path_parts:
        if isinstance(current, dict):
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        else:
            raise KeyError(f"Cannot access {part} in {current}")
    return current


@dataclass
class PayloadFilter:
    """Filter events based on payload field values."""
    field_path: str  # Dot-separated path like "player.health" or "position.x"
    operator: str    # "eq", "ne", "gt", "lt", "ge", "le", "in", "contains", "regex"
    value: Any

    def matches(self, payload: Dict[str, Any]) -> bool:
        """Check if payload matches the filter criteria."""
        try:
            field_value = get_nested_value(payload, self.field_path.split('.'))
            return self._apply_operator(field_value, self.operator, self.value)
        except (KeyError, TypeError, AttributeError):
            return False

    def _apply_operator(self, field_value: Any, operator: str, expected_value: Any) -> bool:
        """Apply comparison operator."""
        if operator == "eq":
            return field_value == expected_value
        elif operator == "ne":
            return field_value != expected_value
        elif operator == "gt":
            return field_value > expected_value
        elif operator == "lt":
            return field_value < expected_value
        elif operator == "ge":
            return field_value >= expected_value
        elif operator == "le":
            return field_value <= expected_value
        elif operator == "in":
            return field_value in expected_value if isinstance(expected_value, (list, set, tuple)) else False
        elif operator == "contains":
            return expected_value in field_value if isinstance(field_value, (str, list, dict)) else False
        elif operator == "regex":
            import re
            return bool(re.search(expected_value, str(field_value)))
        return False


@dataclass
class FrequencyThrottle:
    """Throttle high-frequency events."""
    max_events_per_second: float
    burst_limit: int = 1

    def __post_init__(self):
        self._event_timestamps: deque = deque(maxlen=self.burst_limit * 2)
        self._last_check = time.time()

    def should_allow(self, event_type: str) -> bool:
        """Check if event should be allowed based on frequency limits."""
        current_time = time.time()

        # Clean old timestamps
        cutoff_time = current_time - 1.0
        while self._event_timestamps and self._event_timestamps[0] < cutoff_time:
            self._event_timestamps.popleft()

        # Check burst limit
        if len(self._event_timestamps) >= self.burst_limit:
            return False

        # Check rate limit
        if self._event_timestamps:
            time_span = current_time - self._event_timestamps[0]
            if time_span > 0:
                current_rate = len(self._event_timestamps) / time_span
                if current_rate >= self.max_events_per_second:
                    return False

        self._event_timestamps.append(current_time)
        return True


class EventFilter:
    """Enhanced event filter with advanced filtering capabilities."""

    def __init__(
        self,
        event_types: Optional[Set[Union[str, TransportEvent]]] = None,
        predicate: Optional[Callable[[Event], bool]] = None,
        # Advanced filters
        coordinate_bounds: Optional[BoundingBox] = None,
        time_window: Optional[TimeWindow] = None,
        payload_filters: Optional[List[PayloadFilter]] = None,
        frequency_throttle: Optional[FrequencyThrottle] = None,
        # Coordinate field mapping (customizable for different event types)
        coordinate_fields: Optional[Dict[str, Tuple[str, str, str]]] = None,  # event_type -> (x_field, y_field, z_field)
    ):
        # Legacy filters
        self.event_types = event_types or set()
        if self.event_types:
            self.event_types = {t.value if isinstance(t, TransportEvent) else t for t in self.event_types}
        self.predicate = predicate

        # Advanced filters
        self.coordinate_bounds = coordinate_bounds
        self.time_window = time_window
        self.payload_filters = payload_filters or []
        self.frequency_throttle = frequency_throttle

        # Default coordinate field mappings for common Minecraft events
        self.coordinate_fields = coordinate_fields or {
            "player_move": ("x", "y", "z"),
            "block_update": ("x", "y", "z"),
            "entity_move": ("x", "y", "z"),
            "position": ("x", "y", "z"),
        }

    def matches(self, event: Event) -> bool:
        """Check if event matches all filter criteria."""
        # Legacy checks
        if self.event_types and event.type not in self.event_types:
            return False
        if self.predicate and not self.predicate(event):
            return False

        # Advanced checks
        if not self._matches_coordinate_bounds(event):
            return False
        if not self._matches_time_window(event):
            return False
        if not self._matches_payload_filters(event):
            return False
        if not self._matches_frequency_throttle(event):
            return False

        return True

    def _matches_coordinate_bounds(self, event: Event) -> bool:
        """Check coordinate bounds if configured."""
        if not self.coordinate_bounds:
            return True

        coord_fields = resolve_coordinate_fields(event.type, self.coordinate_fields)

        try:
            x_field, y_field, z_field = coord_fields
            x = get_nested_value(event.data, x_field.split('.'))
            y = get_nested_value(event.data, y_field.split('.'))
            z = get_nested_value(event.data, z_field.split('.'))
            return self.coordinate_bounds.contains(x, y, z)
        except (KeyError, TypeError, ValueError):
            return False

    def _matches_time_window(self, event: Event) -> bool:
        """Check time window if configured."""
        if not self.time_window:
            return True
        return self.time_window.contains(event.timestamp)

    def _matches_payload_filters(self, event: Event) -> bool:
        """Check all payload filters."""
        for payload_filter in self.payload_filters:
            if not payload_filter.matches(event.data):
                return False
        return True

    def _matches_frequency_throttle(self, event: Event) -> bool:
        """Check frequency throttling."""
        if not self.frequency_throttle:
            return True
        return self.frequency_throttle.should_allow(event.type)


@dataclass
class PriorityRule:
    """Dynamic priority adjustment rule."""
    event_types: Set[str]
    condition: Callable[[Event], bool]
    priority_boost: int
    description: str = ""

    def applies_to(self, event: Event) -> bool:
        """Check if this rule applies to the event."""
        return event.type in self.event_types and self.condition(event)


class PriorityManager:
    """Manages dynamic priority adjustments for events."""

    def __init__(self):
        self._rules: List[PriorityRule] = []
        self._event_frequencies: Dict[str, deque] = defaultdict(lambda: deque(maxlen=100))
        self._importance_scores: Dict[str, float] = defaultdict(float)

    def add_rule(self, rule: PriorityRule) -> None:
        """Add a priority adjustment rule."""
        self._rules.append(rule)

    def calculate_dynamic_priority(self, event: Event) -> int:
        """Calculate dynamic priority based on rules and event characteristics."""
        base_priority = event.priority

        # Update frequency tracking
        self._update_frequency(event.type, event.timestamp)

        # Apply rule-based adjustments
        for rule in self._rules:
            if rule.applies_to(event):
                base_priority += rule.priority_boost

        # Apply frequency-based adjustments
        frequency_penalty = self._calculate_frequency_penalty(event.type)
        importance_boost = self._calculate_importance_boost(event.type)

        return max(0, base_priority + frequency_penalty + importance_boost)

    def _update_frequency(self, event_type: str, timestamp: float) -> None:
        """Update event frequency tracking."""
        self._event_frequencies[event_type].append(timestamp)

    def _calculate_frequency_penalty(self, event_type: str) -> int:
        """Calculate priority penalty for high-frequency events."""
        timestamps = self._event_frequencies[event_type]
        if len(timestamps) < 10:
            return 0

        # Calculate events per second over last 10 events
        time_span = timestamps[-1] - timestamps[0]
        if time_span <= 0:
            return 0

        freq = len(timestamps) / time_span

        # Penalize very high frequency events
        if freq > 50:  # More than 50 events/second
            return -2
        elif freq > 20:
            return -1
        return 0

    def _calculate_importance_boost(self, event_type: str) -> int:
        """Calculate importance-based priority boost."""
        # Simple importance scoring based on event type patterns
        importance_patterns = {
            "error": 3,
            "critical": 3,
            "warning": 2,
            "goal_reached": 2,
            "inventory_low": 2,
            "player_hurt": 2,
            "block_broken": 1,
            "block_placed": 1,
        }

        for pattern, boost in importance_patterns.items():
            if pattern in event_type.lower():
                self._importance_scores[event_type] = max(self._importance_scores[event_type], boost)
                return boost

        return 0


@dataclass
class NamedSubscription:
    """A named subscription with specific filter criteria and metadata."""
    name: str
    callback: Callable[[Event], None]
    filter_: Optional[EventFilter] = None
    client_id: Optional[str] = None
    created_at: float = None
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = time.time()
        if self.metadata is None:
            self.metadata = {}


@dataclass
class EventSubscription:
    callback: Callable[[Event], None]
    filter_: Optional[EventFilter] = None


class SpatialIndex:
    """Spatial index for efficient coordinate-based lookups."""

    def __init__(self, grid_size: float = 16.0):
        self.grid_size = grid_size
        self._grid: Dict[Tuple[int, int, int], List[Event]] = defaultdict(list)

    def insert(self, event: Event, x: float, y: float, z: float) -> None:
        """Insert event into spatial index."""
        grid_x, grid_y, grid_z = self._get_grid_coords(x, y, z)
        self._grid[(grid_x, grid_y, grid_z)].append(event)

    def query_bbox(self, bbox: BoundingBox) -> List[Event]:
        """Query events within bounding box."""
        events = []
        # Calculate grid cells that intersect with the bounding box
        min_grid_x = int(bbox.min_x // self.grid_size)
        max_grid_x = int(bbox.max_x // self.grid_size)
        min_grid_y = int(bbox.min_y // self.grid_size)
        max_grid_y = int(bbox.max_y // self.grid_size)
        min_grid_z = int(bbox.min_z // self.grid_size)
        max_grid_z = int(bbox.max_z // self.grid_size)

        # Query all intersecting grid cells
        for gx in range(min_grid_x, max_grid_x + 1):
            for gy in range(min_grid_y, max_grid_y + 1):
                for gz in range(min_grid_z, max_grid_z + 1):
                    cell_events = self._grid.get((gx, gy, gz), [])
                    # Filter events that are actually within the bounding box
                    for event in cell_events:
                        if self._event_in_bbox(event, bbox):
                            events.append(event)

        return events

    def _get_grid_coords(self, x: float, y: float, z: float) -> Tuple[int, int, int]:
        """Convert world coordinates to grid coordinates."""
        return (int(x // self.grid_size), int(y // self.grid_size), int(z // self.grid_size))

    def _event_in_bbox(self, event: Event, bbox: BoundingBox) -> bool:
        """Check if event coordinates are within bounding box."""
        # This would need coordinate extraction logic based on event type
        # For now, return True (would be implemented based on coordinate_fields mapping)
        return True


class FilterCache:
    """Cache for expensive filter evaluations."""

    def __init__(self, max_cache_size: int = 1000, ttl_seconds: float = 60.0):
        self.max_cache_size = max_cache_size
        self.ttl_seconds = ttl_seconds
        self._cache: Dict[str, Tuple[bool, float]] = {}

    def get(self, cache_key: str) -> Optional[bool]:
        """Get cached filter result."""
        if cache_key in self._cache:
            result, timestamp = self._cache[cache_key]
            if time.time() - timestamp < self.ttl_seconds:
                return result
            else:
                del self._cache[cache_key]
        return None

    def put(self, cache_key: str, result: bool) -> None:
        """Cache filter result."""
        if len(self._cache) >= self.max_cache_size:
            # Simple LRU eviction - remove oldest entries
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k][1])
            del self._cache[oldest_key]

        self._cache[cache_key] = (result, time.time())

    def generate_cache_key(self, filter_: EventFilter, event: Event) -> str:
        """Generate cache key for filter + event combination."""
        # The payload must be part of the key: type+timestamp alone collide for
        # events published within one clock tick, letting one event's cached
        # verdict decide a different event with an unrelated payload.
        try:
            payload_key = json.dumps(event.data, sort_keys=True, default=str)
        except (TypeError, ValueError):
            payload_key = repr(event.data)
        key_parts = [event.type, str(event.timestamp), payload_key]

        if filter_.coordinate_bounds:
            bbox = filter_.coordinate_bounds
            key_parts.append(f"bbox_{bbox.min_x}_{bbox.max_x}_{bbox.min_y}_{bbox.max_y}_{bbox.min_z}_{bbox.max_z}")

        if filter_.time_window:
            tw = filter_.time_window
            key_parts.append(f"tw_{tw.start_time}_{tw.end_time}_{tw.duration_seconds}")

        # Add payload filter keys
        for pf in filter_.payload_filters:
            key_parts.append(f"pf_{pf.field_path}_{pf.operator}_{pf.value}")

        return "_".join(key_parts)


class EventAggregator:
    """Aggregates high-frequency events to reduce noise."""

    def __init__(self, event_type: str, aggregation_window: float = 1.0, max_count: int = 10):
        self.event_type = event_type
        self.aggregation_window = aggregation_window
        self.max_count = max_count
        self._events: List[Event] = []
        self._last_aggregation = time.time()

    def add_event(self, event: Event) -> Optional[Event]:
        """Add an event to the aggregator. Returns aggregated event if threshold reached."""
        self._events.append(event)

        # Check if we should aggregate
        if len(self._events) >= self.max_count or \
           (time.time() - self._last_aggregation) >= self.aggregation_window:

            aggregated = self._aggregate()
            self._events.clear()
            self._last_aggregation = time.time()
            return aggregated
        return None

    def _aggregate(self) -> Event:
        """Create an aggregated event from collected events."""
        if not self._events:
            raise ValueError("No events to aggregate")

        # Create aggregated data
        aggregated_data = {
            "count": len(self._events),
            "events": [e.data for e in self._events],
            "first_timestamp": self._events[0].timestamp,
            "last_timestamp": self._events[-1].timestamp,
        }

        return Event(
            type=f"{self.event_type}_aggregated",
            data=aggregated_data,
            timestamp=time.time(),
            priority=max(e.priority for e in self._events),
        )

    def flush(self) -> Optional[Event]:
        """Force aggregation of remaining events."""
        if self._events:
            aggregated = self._aggregate()
            self._events.clear()
            self._last_aggregation = time.time()
            return aggregated
        return None


class EventManager:
    # Compression constants
    COMPRESSION_SIZE_THRESHOLD = 1024  # 1KB
    HIGH_FREQUENCY_THRESHOLD = 10  # events per second
    FREQUENCY_WINDOW = 5.0  # seconds

    def __init__(
        self,
        max_buffer_size: int = 1000,
        default_ttl: Optional[float] = None,
        enable_compression: bool = True,
        enable_spatial_index: bool = True,
        enable_filter_cache: bool = True,
        spatial_grid_size: float = 16.0,
        filter_cache_size: int = 1000,
        filter_cache_ttl: float = 60.0
    ):
        self.max_buffer_size = max_buffer_size
        self.default_ttl = default_ttl
        self.enable_compression = enable_compression
        self.enable_spatial_index = enable_spatial_index
        self.enable_filter_cache = enable_filter_cache
        self._buffer: deque[Event] = deque(maxlen=max_buffer_size)
        self._subscriptions: List[EventSubscription] = []
        self._named_subscriptions: Dict[str, NamedSubscription] = {}
        self._aggregators: Dict[str, EventAggregator] = {}
        self._lock = threading.RLock()
        self._shutdown_event = threading.Event()

        # Advanced features
        self._priority_manager = PriorityManager()
        self._spatial_index = SpatialIndex(spatial_grid_size) if enable_spatial_index else None
        self._filter_cache = FilterCache(filter_cache_size, filter_cache_ttl) if enable_filter_cache else None

        # Compression tracking
        self._event_frequencies: Dict[str, List[float]] = defaultdict(list)  # event_type -> timestamps
        self._compression_metrics: Dict[str, Dict[str, float]] = defaultdict(dict)  # compression_type -> metrics

        # Bandwidth tracking for prioritization
        self._bandwidth_stats: Dict[str, Dict[str, float]] = defaultdict(dict)  # client_id -> stats

    def _update_event_frequency(self, event_type: str) -> None:
        """Update frequency tracking for an event type."""
        current_time = time.time()
        timestamps = self._event_frequencies[event_type]

        # Add current timestamp
        timestamps.append(current_time)

        # Remove timestamps older than frequency window
        cutoff_time = current_time - self.FREQUENCY_WINDOW
        self._event_frequencies[event_type] = [t for t in timestamps if t > cutoff_time]

    def _is_high_frequency_event(self, event_type: str) -> bool:
        """Check if event type is considered high frequency."""
        timestamps = self._event_frequencies[event_type]
        if len(timestamps) < 2:
            return False

        # Calculate events per second over the window
        time_span = timestamps[-1] - timestamps[0]
        if time_span == 0:
            return False

        freq = len(timestamps) / time_span
        return freq >= self.HIGH_FREQUENCY_THRESHOLD

    def _select_compression_algorithm(self, data: Dict[str, Any], event_type: str) -> str:
        """Select the best compression algorithm based on data characteristics."""
        if not self.enable_compression:
            return "none"

        json_str = json.dumps(data)
        data_size = len(json_str.encode('utf-8'))

        # Check size threshold
        if data_size < self.COMPRESSION_SIZE_THRESHOLD:
            return "none"

        # For high-frequency events, prefer LZ4 for speed
        if self._is_high_frequency_event(event_type):
            return "lz4"

        # For large payloads, use GZIP for better compression
        return "gzip"

    def _update_compression_metrics(self, compression_type: str, original_size: int, compressed_size: int) -> None:
        """Update compression effectiveness metrics."""
        if original_size == 0:
            return

        ratio = compressed_size / original_size
        metrics = self._compression_metrics[compression_type]
        metrics['total_compressions'] = metrics.get('total_compressions', 0) + 1
        metrics['avg_ratio'] = ((metrics.get('avg_ratio', 0) * (metrics['total_compressions'] - 1)) + ratio) / metrics['total_compressions']

    def publish_event(
        self,
        event_type: Union[str, TransportEvent],
        data: Dict[str, Any],
        priority: int = 0,
        timestamp: Optional[float] = None,
        ttl: Optional[float] = None,
    ) -> None:
        """Publish an event to the buffer and notify subscribers."""
        event_type_str = event_type.value if isinstance(event_type, TransportEvent) else event_type
        event_timestamp = timestamp or time.time()

        # Update frequency tracking
        self._update_event_frequency(event_type_str)

        # Compress data if enabled
        compressed_data = self._compress_data(data, event_type_str) if self.enable_compression else data

        # Create initial event
        event = Event(
            type=event_type,
            data=compressed_data,
            timestamp=event_timestamp,
            priority=priority,
            ttl=ttl or self.default_ttl,
        )

        # Calculate dynamic priority
        event.priority = self._priority_manager.calculate_dynamic_priority(event)

        # Add to spatial index if enabled and coordinates available
        if self._spatial_index and self._has_coordinates(event):
            try:
                x, y, z = self._extract_coordinates(event)
                self._spatial_index.insert(event, x, y, z)
            except (KeyError, TypeError, ValueError):
                pass  # Skip spatial indexing if coordinates not available

        # Check for aggregation
        aggregated_event = None
        if event_type_str in self._aggregators:
            aggregated_event = self._aggregators[event_type_str].add_event(event)

        with self._lock:
            # Clean expired events
            self._clean_expired_events()

            # Add to buffer (deque will auto-remove oldest if over size)
            self._buffer.append(event)
            logger.debug(f"Event published: {event.type} (buffer size: {len(self._buffer)})")

        # Notify subscribers asynchronously
        self._notify_subscribers(event)

        # Also notify about aggregated event if created
        if aggregated_event:
            self._notify_subscribers(aggregated_event)

    def _has_coordinates(self, event: Event) -> bool:
        """Check if the event actually carries readable coordinates."""
        try:
            self._extract_coordinates(event)
            return True
        except (KeyError, TypeError, ValueError):
            return False

    def _extract_coordinates(self, event: Event) -> Tuple[float, float, float]:
        """Extract coordinates from event data."""
        x_field, y_field, z_field = resolve_coordinate_fields(event.type)
        x = get_nested_value(event.data, x_field.split('.'))
        y = get_nested_value(event.data, y_field.split('.'))
        z = get_nested_value(event.data, z_field.split('.'))

        return float(x), float(y), float(z)

    def _compress_data(self, data: Dict[str, Any], event_type: str) -> Dict[str, Any]:
        """Compress event data using intelligent algorithm selection."""
        if not self.enable_compression:
            return data

        # Select compression algorithm
        algorithm = self._select_compression_algorithm(data, event_type)
        if algorithm == "none":
            return data

        json_str = json.dumps(data)
        original_size = len(json_str.encode('utf-8'))

        try:
            if algorithm == "lz4":
                if lz4 is None:
                    # Fallback to gzip if LZ4 not available
                    algorithm = "gzip"
                else:
                    compressed = lz4.frame.compress(json_str.encode('utf-8'))
                    compressed_size = len(compressed)
                    self._update_compression_metrics("lz4", original_size, compressed_size)
                    return {"_compressed": True, "_algorithm": "lz4", "data": compressed.hex()}

            if algorithm == "gzip":
                compressed = zlib.compress(json_str.encode('utf-8'))
                compressed_size = len(compressed)
                self._update_compression_metrics("gzip", original_size, compressed_size)
                return {"_compressed": True, "_algorithm": "gzip", "data": compressed.hex()}

        except Exception as e:
            logger.warning(f"Compression failed for {algorithm}: {e}")
            return data

        return data

    def _decompress_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Decompress event data if compressed."""
        if isinstance(data.get("_compressed"), bool) and data["_compressed"]:
            try:
                compressed_bytes = bytes.fromhex(data["data"])
                algorithm = data.get("_algorithm", "gzip")  # Default to gzip for backward compatibility

                if algorithm == "lz4":
                    if lz4 is None:
                        raise ImportError("LZ4 library not available")
                    decompressed = lz4.frame.decompress(compressed_bytes).decode('utf-8')
                elif algorithm == "gzip":
                    decompressed = zlib.decompress(compressed_bytes).decode('utf-8')
                else:
                    # Fallback for unknown algorithms
                    decompressed = zlib.decompress(compressed_bytes).decode('utf-8')

                return json.loads(decompressed)
            except Exception as e:
                logger.warning(f"Decompression failed for {algorithm}: {e}")
                return data
        return data

    def _clean_expired_events(self) -> None:
        """Remove expired events from buffer."""
        current_time = time.time()
        # Create new deque with non-expired events
        self._buffer = deque(
            (e for e in self._buffer if not e.is_expired()),
            maxlen=self.max_buffer_size
        )

    def add_aggregator(self, event_type: str, aggregation_window: float = 1.0, max_count: int = 10) -> None:
        """Add event aggregation for a specific event type."""
        event_type_str = event_type.value if isinstance(event_type, TransportEvent) else event_type
        self._aggregators[event_type_str] = EventAggregator(event_type_str, aggregation_window, max_count)

    def remove_aggregator(self, event_type: str) -> None:
        """Remove event aggregation for a specific event type."""
        event_type_str = event_type.value if isinstance(event_type, TransportEvent) else event_type
        if event_type_str in self._aggregators:
            # Flush any remaining aggregated events
            aggregated = self._aggregators[event_type_str].flush()
            if aggregated:
                self._notify_subscribers(aggregated)
            del self._aggregators[event_type_str]

    def _notify_subscribers(self, event: Event) -> None:
        """Notify all subscribers that match the event."""
        to_notify = []
        with self._lock:
            for subscription in self._subscriptions:
                if subscription.filter_ is None or subscription.filter_.matches(event):
                    to_notify.append(subscription.callback)
            # Without this, subscribe_named() registered a dead callback.
            for named in self._named_subscriptions.values():
                if named.filter_ is None or named.filter_.matches(event):
                    to_notify.append(named.callback)

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
        """Subscribe to events with optional filtering (legacy method)."""
        filter_ = None
        if event_types or predicate:
            filter_ = EventFilter(event_types=event_types, predicate=predicate)

        subscription = EventSubscription(callback=callback, filter_=filter_)
        with self._lock:
            self._subscriptions.append(subscription)

    def subscribe_named(
        self,
        name: str,
        callback: Callable[[Event], None],
        filter_: Optional[EventFilter] = None,
        client_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Create a named subscription with advanced filtering."""
        if name in self._named_subscriptions:
            raise ValueError(f"Subscription with name '{name}' already exists")

        named_subscription = NamedSubscription(
            name=name,
            callback=callback,
            filter_=filter_,
            client_id=client_id,
            metadata=metadata or {},
        )

        with self._lock:
            self._named_subscriptions[name] = named_subscription

    def unsubscribe_named(self, name: str) -> bool:
        """Remove a named subscription."""
        with self._lock:
            return self._named_subscriptions.pop(name, None) is not None

    def get_named_subscription(self, name: str) -> Optional[NamedSubscription]:
        """Get a named subscription by name."""
        return self._named_subscriptions.get(name)

    def list_named_subscriptions(self, client_id: Optional[str] = None) -> List[NamedSubscription]:
        """List all named subscriptions, optionally filtered by client_id."""
        subscriptions = list(self._named_subscriptions.values())
        if client_id:
            subscriptions = [s for s in subscriptions if s.client_id == client_id]
        return subscriptions

    def add_priority_rule(self, rule: PriorityRule) -> None:
        """Add a dynamic priority adjustment rule."""
        self._priority_manager.add_rule(rule)

    def remove_priority_rule(self, description: str) -> bool:
        """Remove priority rules matching the description."""
        # Note: This is a simple implementation - could be enhanced
        removed = False
        rules_to_remove = []
        for i, rule in enumerate(self._priority_manager._rules):
            if rule.description == description:
                rules_to_remove.append(i)
                removed = True

        for i in reversed(rules_to_remove):
            self._priority_manager._rules.pop(i)

        return removed

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

        return self.poll_events_with_filter(filter_, max_events, remove)

    def poll_events_with_filter(
        self,
        filter_: Optional[EventFilter] = None,
        max_events: Optional[int] = None,
        remove: bool = True,
    ) -> List[Event]:
        """Poll events using advanced EventFilter."""
        with self._lock:
            # Clean expired events first
            self._clean_expired_events()

            # Use spatial index for coordinate-based queries if available
            if filter_ and filter_.coordinate_bounds and self._spatial_index:
                candidate_events = self._spatial_index.query_bbox(filter_.coordinate_bounds)
                # Filter candidates further
                events = [e for e in candidate_events if filter_.matches(e)]
            elif not filter_:
                # Get all events
                events = list(self._buffer)
            else:
                # Check filter cache first
                cached_results = []
                if self._filter_cache:
                    for event in self._buffer:
                        cache_key = self._filter_cache.generate_cache_key(filter_, event)
                        cached = self._filter_cache.get(cache_key)
                        if cached is not None:
                            if cached:
                                cached_results.append(event)
                        else:
                            # Not cached, evaluate and cache
                            matches = filter_.matches(event)
                            self._filter_cache.put(cache_key, matches)
                            if matches:
                                cached_results.append(event)
                    events = cached_results
                else:
                    # No cache, evaluate all
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

            # Decompress event data
            for event in events:
                event.data = self._decompress_data(event.data)

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
            # Flush all aggregators
            for aggregator in self._aggregators.values():
                aggregated = aggregator.flush()
                if aggregated:
                    self._notify_subscribers(aggregated)

            # Clear all subscriptions and advanced features
            self._subscriptions.clear()
            self._named_subscriptions.clear()
            self._aggregators.clear()
            self._buffer.clear()

            # Clear performance optimization structures
            if self._spatial_index:
                self._spatial_index._grid.clear()
            if self._filter_cache:
                self._filter_cache._cache.clear()