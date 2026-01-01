package com.minecraftbot.baritone;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.BeforeEach;
import static org.junit.jupiter.api.Assertions.*;

import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class CacheManagerTest {

    private CacheManager cache;

    @BeforeEach
    void setUp() {
        // Create cache with 5 minute TTL and max size 10
        cache = new CacheManager(5 * 60 * 1000L, 10);
    }

    @Test
    void testBasicPutAndGet() {
        cache.put("key1", "value1");
        assertEquals("value1", cache.get("key1"));
    }

    @Test
    void testMiss() {
        assertNull(cache.get("nonexistent"));
    }

    @Test
    void testRemove() {
        cache.put("key1", "value1");
        assertEquals("value1", cache.remove("key1"));
        assertNull(cache.get("key1"));
    }

    @Test
    void testClear() {
        cache.put("key1", "value1");
        cache.put("key2", "value2");
        cache.clear();
        assertNull(cache.get("key1"));
        assertNull(cache.get("key2"));
        assertEquals(0, cache.size());
    }

    @Test
    void testTTLExpiration() throws InterruptedException {
        // Create cache with 100ms TTL for testing
        CacheManager shortCache = new CacheManager(100L, 10);
        shortCache.put("key1", "value1");

        assertEquals("value1", shortCache.get("key1"));

        // Wait for expiration
        Thread.sleep(150);

        assertNull(shortCache.get("key1"));
    }

    @Test
    void testSizeLimit() {
        CacheManager smallCache = new CacheManager(0L, 3); // No TTL, max 3 entries

        smallCache.put("key1", "value1");
        smallCache.put("key2", "value2");
        smallCache.put("key3", "value3");
        smallCache.put("key4", "value4"); // Should evict key1

        assertNull(smallCache.get("key1"));
        assertEquals("value2", smallCache.get("key2"));
        assertEquals("value3", smallCache.get("key3"));
        assertEquals("value4", smallCache.get("key4"));
    }

    @Test
    void testLRU() {
        CacheManager smallCache = new CacheManager(0L, 2);

        smallCache.put("key1", "value1");
        smallCache.put("key2", "value2");

        // Access key1 to make it most recently used
        smallCache.get("key1");

        // Add key3, should evict key2 (least recently used)
        smallCache.put("key3", "value3");

        assertEquals("value1", smallCache.get("key1"));
        assertNull(smallCache.get("key2"));
        assertEquals("value3", smallCache.get("key3"));
    }

    @Test
    void testStatistics() {
        // Hit
        cache.put("key1", "value1");
        cache.get("key1");

        // Miss
        cache.get("key2");

        CacheManager.CacheStats stats = cache.getStats();
        assertEquals(1, stats.hits);
        assertEquals(1, stats.misses);
        assertEquals(0, stats.evictions);
        assertEquals(1, stats.currentSize);
        assertEquals(0.5, stats.getHitRate());
    }

    @Test
    void testWarming() {
        Map<Object, Object> initialData = new HashMap<>();
        initialData.put("key1", "value1");
        initialData.put("key2", "value2");

        cache.warm(initialData);

        assertEquals("value1", cache.get("key1"));
        assertEquals("value2", cache.get("key2"));
    }

    @Test
    void testBlockCaching() {
        cache.cacheBlock(10, 20, 30, "minecraft:stone");

        assertEquals("minecraft:stone", cache.getCachedBlock(10, 20, 30));
        assertNull(cache.getCachedBlock(11, 20, 30));
    }

    @Test
    void testEntityScanCaching() {
        List<String> entities = Arrays.asList("zombie", "skeleton");
        cache.cacheEntityScan("scan123", entities);

        List<String> cached = cache.getCachedEntityScan("scan123");
        assertNotNull(cached);
        assertEquals(2, cached.size());
        assertEquals("zombie", cached.get(0));
        assertEquals("skeleton", cached.get(1));
    }

    @Test
    void testPathCaching() {
        List<int[]> path = Arrays.asList(
            new int[]{0, 0, 0},
            new int[]{1, 0, 0},
            new int[]{2, 0, 0}
        );

        cache.cachePath(0, 0, 0, 2, 0, 0, path);

        List<int[]> cached = cache.getCachedPath(0, 0, 0, 2, 0, 0);
        assertNotNull(cached);
        assertEquals(3, cached.size());
        assertArrayEquals(new int[]{0, 0, 0}, cached.get(0));
        assertArrayEquals(new int[]{1, 0, 0}, cached.get(1));
        assertArrayEquals(new int[]{2, 0, 0}, cached.get(2));
    }

    @Test
    void testThreadSafety() throws InterruptedException {
        CacheManager threadCache = new CacheManager(0L, 100);

        // Run multiple threads accessing the cache
        Thread[] threads = new Thread[10];
        for (int i = 0; i < threads.length; i++) {
            final int threadId = i;
            threads[i] = new Thread(() -> {
                for (int j = 0; j < 100; j++) {
                    String key = "thread" + threadId + "_key" + j;
                    threadCache.put(key, "value" + j);
                    assertNotNull(threadCache.get(key));
                }
            });
            threads[i].start();
        }

        // Wait for all threads to complete
        for (Thread thread : threads) {
            thread.join();
        }

        // Verify final state
        assertTrue(threadCache.size() >= 100); // At least 100 entries from one thread due to LRU
    }
}