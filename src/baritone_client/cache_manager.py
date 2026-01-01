"""CacheManager facade for intelligent caching of expensive operations."""

import logging
from typing import Any, Dict, Optional

from .transport import Transport

logger = logging.getLogger(__name__)


class CacheStats:
    """Cache statistics data class."""

    def __init__(self, hits: int, misses: int, evictions: int, current_size: int):
        self.hits = hits
        self.misses = misses
        self.evictions = evictions
        self.current_size = current_size

    def get_hit_rate(self) -> float:
        """Calculate hit rate as percentage."""
        total = self.hits + self.misses
        return (self.hits / total * 100) if total > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "current_size": self.current_size,
            "hit_rate": self.get_hit_rate()
        }


class CacheManager:
    """Facade for bridge-side caching operations."""

    def __init__(self, transport: Transport):
        self.transport = transport

    def get(self, key: str) -> Optional[Any]:
        """
        Retrieve a value from the cache.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found
        """
        response = self.transport.dispatch("cache/get", {"key": key})
        return response.get("value")

    def put(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> bool:
        """
        Store a value in the cache.

        Args:
            key: Cache key
            value: Value to cache
            ttl_seconds: Optional time-to-live in seconds

        Returns:
            True if successful
        """
        payload = {"key": key, "value": value}
        if ttl_seconds is not None:
            payload["ttl_seconds"] = ttl_seconds
        response = self.transport.dispatch("cache/put", payload)
        return response.get("success", False)

    def remove(self, key: str) -> bool:
        """
        Remove a value from the cache.

        Args:
            key: Cache key

        Returns:
            True if removed
        """
        response = self.transport.dispatch("cache/remove", {"key": key})
        return response.get("removed", False)

    def clear(self) -> bool:
        """
        Clear all entries from the cache.

        Returns:
            True if successful
        """
        response = self.transport.dispatch("cache/clear", {})
        return response.get("success", False)

    def get_stats(self) -> CacheStats:
        """
        Get cache statistics.

        Returns:
            CacheStats object
        """
        response = self.transport.dispatch("cache/stats", {})
        stats_data = response.get("stats", {})
        return CacheStats(
            hits=stats_data.get("hits", 0),
            misses=stats_data.get("misses", 0),
            evictions=stats_data.get("evictions", 0),
            current_size=stats_data.get("current_size", 0)
        )

    def cache_block(self, x: int, y: int, z: int, block_state: str) -> bool:
        """
        Cache block query results.

        Args:
            x, y, z: Block coordinates
            block_state: Block state string

        Returns:
            True if cached
        """
        key = f"block:{x},{y},{z}"
        return self.put(key, block_state)

    def get_cached_block(self, x: int, y: int, z: int) -> Optional[str]:
        """
        Retrieve cached block state.

        Args:
            x, y, z: Block coordinates

        Returns:
            Cached block state or None
        """
        key = f"block:{x},{y},{z}"
        value = self.get(key)
        return str(value) if value is not None else None

    def cache_entity_scan(self, scan_id: str, entities: list) -> bool:
        """
        Cache entity scan results.

        Args:
            scan_id: Unique scan identifier
            entities: List of entities

        Returns:
            True if cached
        """
        key = f"entities:{scan_id}"
        return self.put(key, entities)

    def get_cached_entity_scan(self, scan_id: str) -> Optional[list]:
        """
        Retrieve cached entity scan results.

        Args:
            scan_id: Unique scan identifier

        Returns:
            Cached entity list or None
        """
        key = f"entities:{scan_id}"
        return self.get(key)