package com.minecraftbot.baritone;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;

/**
 * CacheManager provides intelligent caching with TTL support, LRU eviction, size limits,
 * and statistics. Designed to cache expensive operations like block queries, entity scans,
 * and path calculations to reduce server load and improve response times.
 */
public class CacheManager {

    private final long ttlMillis;
    private final int maxSize;
    private final Map<Object, CacheEntry> cache;
    private final AtomicLong hits = new AtomicLong(0);
    private final AtomicLong misses = new AtomicLong(0);
    private final AtomicLong evictions = new AtomicLong(0);

    /**
     * Cache entry holding value and creation timestamp
     */
    private static class CacheEntry {
        final Object value;
        final long timestamp;

        CacheEntry(Object value) {
            this.value = value;
            this.timestamp = System.currentTimeMillis();
        }

        boolean isExpired(long ttlMillis) {
            return System.currentTimeMillis() - timestamp > ttlMillis;
        }
    }

    /**
     * Creates a new CacheManager with specified TTL and maximum size
     * @param ttlMillis Time-to-live in milliseconds (0 for no expiration)
     * @param maxSize Maximum number of entries (0 for unlimited)
     */
    public CacheManager(long ttlMillis, int maxSize) {
        this.ttlMillis = ttlMillis;
        this.maxSize = maxSize;
        this.cache = new LinkedHashMap<Object, CacheEntry>(16, 0.75f, true) {
            @Override
            protected boolean removeEldestEntry(Map.Entry<Object, CacheEntry> eldest) {
                if (maxSize > 0 && size() > maxSize) {
                    evictions.incrementAndGet();
                    return true;
                }
                return false;
            }
        };
    }

    /**
     * Retrieves a value from the cache
     * @param key The cache key
     * @return The cached value or null if not found or expired
     */
    public synchronized Object get(Object key) {
        CacheEntry entry = cache.get(key);
        if (entry == null) {
            misses.incrementAndGet();
            return null;
        }

        if (ttlMillis > 0 && entry.isExpired(ttlMillis)) {
            cache.remove(key);
            misses.incrementAndGet();
            return null;
        }

        hits.incrementAndGet();
        return entry.value;
    }

    /**
     * Stores a value in the cache
     * @param key The cache key
     * @param value The value to cache
     */
    public synchronized void put(Object key, Object value) {
        cache.put(key, new CacheEntry(value));
    }

    /**
     * Removes a value from the cache
     * @param key The cache key
     * @return The removed value or null if not found
     */
    public synchronized Object remove(Object key) {
        CacheEntry entry = cache.remove(key);
        return entry != null ? entry.value : null;
    }

    /**
     * Clears all entries from the cache
     */
    public synchronized void clear() {
        cache.clear();
    }

    /**
     * Gets the current cache size
     * @return Number of entries in cache
     */
    public synchronized int size() {
        // Clean expired entries before returning size
        cleanExpiredEntries();
        return cache.size();
    }

    /**
     * Warms the cache with initial data
     * @param initialData Map of key-value pairs to preload
     */
    public synchronized void warm(Map<Object, Object> initialData) {
        for (Map.Entry<Object, Object> entry : initialData.entrySet()) {
            put(entry.getKey(), entry.getValue());
        }
    }

    /**
     * Gets cache statistics
     * @return CacheStats object with current metrics
     */
    public CacheStats getStats() {
        return new CacheStats(hits.get(), misses.get(), evictions.get(), size());
    }

    /**
     * Cleans expired entries from the cache
     */
    private synchronized void cleanExpiredEntries() {
        if (ttlMillis <= 0) return;

        cache.entrySet().removeIf(entry -> {
            if (entry.getValue().isExpired(ttlMillis)) {
                evictions.incrementAndGet();
                return true;
            }
            return false;
        });
    }

    // Integration methods for bridge operations

    /**
     * Caches block query results
     * @param x Block X coordinate
     * @param y Block Y coordinate
     * @param z Block Z coordinate
     * @param blockState The block state to cache
     */
    public void cacheBlock(int x, int y, int z, String blockState) {
        String key = "block:" + x + "," + y + "," + z;
        put(key, blockState);
    }

    /**
     * Retrieves cached block state
     * @param x Block X coordinate
     * @param y Block Y coordinate
     * @param z Block Z coordinate
     * @return Cached block state or null
     */
    public String getCachedBlock(int x, int y, int z) {
        String key = "block:" + x + "," + y + "," + z;
        return (String) get(key);
    }

    /**
     * Caches entity scan results
     * @param scanId Unique scan identifier
     * @param entities List of entities found
     */
    public void cacheEntityScan(String scanId, java.util.List<String> entities) {
        String key = "entities:" + scanId;
        put(key, entities);
    }

    /**
     * Retrieves cached entity scan results
     * @param scanId Unique scan identifier
     * @return Cached entity list or null
     */
    public java.util.List<String> getCachedEntityScan(String scanId) {
        String key = "entities:" + scanId;
        return (java.util.List<String>) get(key);
    }

    /**
     * Caches path calculation results
     * @param startX Starting X coordinate
     * @param startY Starting Y coordinate
     * @param startZ Starting Z coordinate
     * @param endX Ending X coordinate
     * @param endY Ending Y coordinate
     * @param endZ Ending Z coordinate
     * @param path The calculated path
     */
    public void cachePath(int startX, int startY, int startZ, int endX, int endY, int endZ, java.util.List<int[]> path) {
        String key = "path:" + startX + "," + startY + "," + startZ + "->" + endX + "," + endY + "," + endZ;
        put(key, path);
    }

    /**
     * Retrieves cached path
     * @param startX Starting X coordinate
     * @param startY Starting Y coordinate
     * @param startZ Starting Z coordinate
     * @param endX Ending X coordinate
     * @param endY Ending Y coordinate
     * @param endZ Ending Z coordinate
     * @return Cached path or null
     */
    public java.util.List<int[]> getCachedPath(int startX, int startY, int startZ, int endX, int endY, int endZ) {
        String key = "path:" + startX + "," + startY + "," + startZ + "->" + endX + "," + endY + "," + endZ;
        return (java.util.List<int[]>) get(key);
    }

    /**
     * Cache statistics data class
     */
    public static class CacheStats {
        public final long hits;
        public final long misses;
        public final long evictions;
        public final int currentSize;

        CacheStats(long hits, long misses, long evictions, int currentSize) {
            this.hits = hits;
            this.misses = misses;
            this.evictions = evictions;
            this.currentSize = currentSize;
        }

        public double getHitRate() {
            long total = hits + misses;
            return total > 0 ? (double) hits / total : 0.0;
        }

        @Override
        public String toString() {
            return String.format("CacheStats{hits=%d, misses=%d, evictions=%d, currentSize=%d, hitRate=%.2f}",
                    hits, misses, evictions, currentSize, getHitRate());
        }
    }
}