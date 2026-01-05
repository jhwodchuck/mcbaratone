package com.minecraftbot.baritone;

import com.google.gson.JsonObject;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * Thread-safe cache for idempotent command results with TTL-based expiration.
 * Automatically evicts expired entries and manages cache size limits.
 */
public class CommandCache {
    private static final Logger logger = LoggerFactory.getLogger(CommandCache.class);

    private static class CacheEntry {
        final CommandResult result;
        final long expirationTime;

        CacheEntry(CommandResult result, long ttlMs) {
            this.result = result;
            this.expirationTime = System.currentTimeMillis() + ttlMs;
        }

        boolean isExpired() {
            return System.currentTimeMillis() > expirationTime;
        }
    }

    private final Map<String, CacheEntry> cache = new ConcurrentHashMap<>();
    private final int maxSize;
    private final long defaultTtlMs;
    private final ScheduledExecutorService cleanupExecutor;

    // Commands that are considered idempotent and safe to cache
    private static final Set<String> IDEMPOTENT_COMMANDS = Set.of(
        "get_inventory", "get_screen", "get_events", "get_dimension",
        "get_death_location", "get_view", "get_recipes", "state"
    );

    // Default configuration
    private static final int DEFAULT_MAX_SIZE = 1000;
    private static final long DEFAULT_TTL_MS = 30000; // 30 seconds
    private static final long CLEANUP_INTERVAL_MS = 60000; // 1 minute

    public CommandCache() {
        this(DEFAULT_MAX_SIZE, DEFAULT_TTL_MS);
    }

    public CommandCache(int maxSize, long defaultTtlMs) {
        this.maxSize = maxSize;
        this.defaultTtlMs = defaultTtlMs;
        this.cleanupExecutor = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread t = new Thread(r, "CommandCache-Cleanup");
            t.setDaemon(true);
            return t;
        });

        // Schedule periodic cleanup
        cleanupExecutor.scheduleAtFixedRate(this::cleanup, CLEANUP_INTERVAL_MS, CLEANUP_INTERVAL_MS, TimeUnit.MILLISECONDS);
    }

    /**
     * Check if a command is idempotent and can be cached.
     */
    public boolean isIdempotentCommand(String command) {
        return IDEMPOTENT_COMMANDS.contains(command);
    }

    /**
     * Get a cached result for the given command and parameters.
     *
     * @param command The command name
     * @param params The command parameters
     * @return The cached CommandResult, or null if not found or expired
     */
    public CommandResult get(String command, JsonObject params) {
        if (!isIdempotentCommand(command)) {
            return null;
        }

        String key = generateKey(command, params);
        CacheEntry entry = cache.get(key);

        if (entry == null) {
            return null;
        }

        if (entry.isExpired()) {
            cache.remove(key);
            return null;
        }

        logger.debug("Cache hit for command: {}", command);
        return entry.result;
    }

    /**
     * Store a result in the cache.
     *
     * @param command The command name
     * @param params The command parameters
     * @param result The result to cache
     * @param ttlMs Time-to-live in milliseconds (0 for default)
     */
    public void put(String command, JsonObject params, CommandResult result, long ttlMs) {
        if (!isIdempotentCommand(command) || result == null) {
            return;
        }

        // Use default TTL if not specified
        if (ttlMs <= 0) {
            ttlMs = defaultTtlMs;
        }

        String key = generateKey(command, params);
        CacheEntry entry = new CacheEntry(result, ttlMs);

        // Check size limit before adding
        if (cache.size() >= maxSize && !cache.containsKey(key)) {
            evictOldest();
        }

        cache.put(key, entry);
        logger.debug("Cached result for command: {} (TTL: {}ms)", command, ttlMs);
    }

    /**
     * Store a result in the cache with default TTL.
     */
    public void put(String command, JsonObject params, CommandResult result) {
        put(command, params, result, 0);
    }

    /**
     * Invalidate cached results for a specific command.
     * Useful when state-changing operations occur.
     *
     * @param command The command to invalidate (null for all)
     */
    public void invalidate(String command) {
        if (command == null) {
            // Invalidate all
            int size = cache.size();
            cache.clear();
            logger.debug("Invalidated all cache entries ({} entries)", size);
        } else {
            // Invalidate specific command
            int removed = 0;
            for (String key : cache.keySet()) {
                if (key.startsWith(command + ":")) {
                    cache.remove(key);
                    removed++;
                }
            }
            if (removed > 0) {
                logger.debug("Invalidated {} cache entries for command: {}", removed, command);
            }
        }
    }

    /**
     * Invalidate all cached results when state changes occur.
     */
    public void invalidateAll() {
        invalidate(null);
    }

    /**
     * Get current cache size.
     */
    public int size() {
        return cache.size();
    }

    /**
     * Get cache statistics.
     */
    public String getStats() {
        long now = System.currentTimeMillis();
        long expired = cache.values().stream().mapToLong(entry -> entry.isExpired() ? 1 : 0).sum();

        return String.format("CommandCache{size=%d, maxSize=%d, expired=%d}",
            cache.size(), maxSize, expired);
    }

    /**
     * Shutdown the cache and cleanup resources.
     */
    public void shutdown() {
        cleanupExecutor.shutdown();
        try {
            if (!cleanupExecutor.awaitTermination(5, TimeUnit.SECONDS)) {
                cleanupExecutor.shutdownNow();
            }
        } catch (InterruptedException e) {
            cleanupExecutor.shutdownNow();
            Thread.currentThread().interrupt();
        }
        cache.clear();
    }

    /**
     * Generate a cache key from command and parameters.
     * Normalizes parameter order for consistent keys.
     */
    private String generateKey(String command, JsonObject params) {
        StringBuilder key = new StringBuilder(command);

        if (params != null && !params.entrySet().isEmpty()) {
            key.append(":");
            // Sort entries for consistent key generation
            params.entrySet().stream()
                .sorted(Map.Entry.comparingByKey())
                .forEach(entry -> {
                    key.append(entry.getKey()).append("=").append(entry.getValue()).append(";");
                });
        }

        return key.toString();
    }

    /**
     * Remove expired entries from the cache.
     */
    private void cleanup() {
        long now = System.currentTimeMillis();
        int removed = 0;

        for (Map.Entry<String, CacheEntry> entry : cache.entrySet()) {
            if (entry.getValue().isExpired()) {
                cache.remove(entry.getKey());
                removed++;
            }
        }

        if (removed > 0) {
            logger.debug("Cleaned up {} expired cache entries", removed);
        }
    }

    /**
     * Evict the oldest entry when cache is full.
     * Uses a simple LRU-like approach by checking expiration times.
     */
    private void evictOldest() {
        String oldestKey = null;
        long oldestTime = Long.MAX_VALUE;

        for (Map.Entry<String, CacheEntry> entry : cache.entrySet()) {
            if (entry.getValue().expirationTime < oldestTime) {
                oldestTime = entry.getValue().expirationTime;
                oldestKey = entry.getKey();
            }
        }

        if (oldestKey != null) {
            cache.remove(oldestKey);
            logger.debug("Evicted oldest cache entry: {}", oldestKey);
        }
    }
}