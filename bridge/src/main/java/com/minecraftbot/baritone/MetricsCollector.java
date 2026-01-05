package com.minecraftbot.baritone;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.LongAdder;
import java.util.List;
import java.util.ArrayList;
import java.util.Collections;

/**
 * Metrics collector for command dispatcher performance monitoring.
 * Tracks execution metrics, response times, and error patterns.
 */
public class MetricsCollector {
    private static final Logger logger = LoggerFactory.getLogger(MetricsCollector.class);

    // Command execution counters
    private final Map<String, LongAdder> commandCounts = new ConcurrentHashMap<>();
    private final Map<String, LongAdder> commandSuccessCounts = new ConcurrentHashMap<>();
    private final Map<String, LongAdder> commandFailureCounts = new ConcurrentHashMap<>();

    // Response time tracking (in milliseconds)
    private final Map<String, List<Long>> responseTimes = new ConcurrentHashMap<>();
    private static final int MAX_RESPONSE_TIME_SAMPLES = 1000;

    // Circuit breaker state tracking
    private final AtomicLong circuitBreakerOpens = new AtomicLong(0);
    private final AtomicLong circuitBreakerCloses = new AtomicLong(0);

    // Cache metrics
    private final AtomicLong cacheHits = new AtomicLong(0);
    private final AtomicLong cacheMisses = new AtomicLong(0);

    // Error type tracking
    private final Map<String, LongAdder> errorTypeCounts = new ConcurrentHashMap<>();

    /**
     * Record a command execution.
     */
    public void recordCommandExecution(String command, boolean success, long responseTimeMs) {
        commandCounts.computeIfAbsent(command, k -> new LongAdder()).increment();

        if (success) {
            commandSuccessCounts.computeIfAbsent(command, k -> new LongAdder()).increment();
        } else {
            commandFailureCounts.computeIfAbsent(command, k -> new LongAdder()).increment();
        }

        // Record response time
        responseTimes.computeIfAbsent(command, k -> Collections.synchronizedList(new ArrayList<>()))
                .add(responseTimeMs);

        // Maintain max samples
        List<Long> times = responseTimes.get(command);
        if (times.size() > MAX_RESPONSE_TIME_SAMPLES) {
            synchronized (times) {
                if (times.size() > MAX_RESPONSE_TIME_SAMPLES) {
                    times.remove(0);
                }
            }
        }
    }

    /**
     * Record a circuit breaker state change.
     */
    public void recordCircuitBreakerStateChange(boolean isOpen) {
        if (isOpen) {
            circuitBreakerOpens.incrementAndGet();
        } else {
            circuitBreakerCloses.incrementAndGet();
        }
    }

    /**
     * Record a cache access.
     */
    public void recordCacheAccess(boolean hit) {
        if (hit) {
            cacheHits.incrementAndGet();
        } else {
            cacheMisses.incrementAndGet();
        }
    }

    /**
     * Record an error by type.
     */
    public void recordError(String errorType) {
        errorTypeCounts.computeIfAbsent(errorType, k -> new LongAdder()).increment();
    }

    /**
     * Get command execution metrics.
     */
    public Map<String, CommandMetrics> getCommandMetrics() {
        Map<String, CommandMetrics> metrics = new ConcurrentHashMap<>();

        for (String command : commandCounts.keySet()) {
            long total = commandCounts.get(command).sum();
            long successes = commandSuccessCounts.getOrDefault(command, new LongAdder()).sum();
            long failures = commandFailureCounts.getOrDefault(command, new LongAdder()).sum();

            double successRate = total > 0 ? (double) successes / total : 0.0;

            List<Long> times = responseTimes.getOrDefault(command, Collections.emptyList());
            ResponseTimeStats timeStats = calculateResponseTimeStats(times);

            metrics.put(command, new CommandMetrics(total, successes, failures, successRate, timeStats));
        }

        return metrics;
    }

    /**
     * Get circuit breaker metrics.
     */
    public CircuitBreakerMetrics getCircuitBreakerMetrics() {
        return new CircuitBreakerMetrics(
            circuitBreakerOpens.get(),
            circuitBreakerCloses.get()
        );
    }

    /**
     * Get cache metrics.
     */
    public CacheMetrics getCacheMetrics() {
        long hits = cacheHits.get();
        long misses = cacheMisses.get();
        long total = hits + misses;
        double hitRate = total > 0 ? (double) hits / total : 0.0;

        return new CacheMetrics(hits, misses, hitRate);
    }

    /**
     * Get error type breakdown.
     */
    public Map<String, Long> getErrorTypeBreakdown() {
        Map<String, Long> breakdown = new ConcurrentHashMap<>();
        errorTypeCounts.forEach((type, counter) -> breakdown.put(type, counter.sum()));
        return breakdown;
    }

    /**
     * Calculate response time percentiles.
     */
    private ResponseTimeStats calculateResponseTimeStats(List<Long> times) {
        if (times.isEmpty()) {
            return new ResponseTimeStats(0, 0, 0, 0);
        }

        List<Long> sortedTimes;
        synchronized (times) {
            sortedTimes = new ArrayList<>(times);
        }
        Collections.sort(sortedTimes);

        int size = sortedTimes.size();
        long p50 = sortedTimes.get(size / 2);
        long p95 = sortedTimes.get((int) (size * 0.95));
        long p99 = sortedTimes.get((int) (size * 0.99));
        long avg = (long) sortedTimes.stream().mapToLong(Long::longValue).average().orElse(0);

        return new ResponseTimeStats(p50, p95, p99, avg);
    }

    /**
     * Reset all metrics (useful for testing or periodic resets).
     */
    public void reset() {
        commandCounts.clear();
        commandSuccessCounts.clear();
        commandFailureCounts.clear();
        responseTimes.clear();
        circuitBreakerOpens.set(0);
        circuitBreakerCloses.set(0);
        cacheHits.set(0);
        cacheMisses.set(0);
        errorTypeCounts.clear();
    }

    // Metrics data classes
    public static class CommandMetrics {
        public final long totalExecutions;
        public final long successfulExecutions;
        public final long failedExecutions;
        public final double successRate;
        public final ResponseTimeStats responseTimeStats;

        public CommandMetrics(long total, long success, long failure, double successRate, ResponseTimeStats timeStats) {
            this.totalExecutions = total;
            this.successfulExecutions = success;
            this.failedExecutions = failure;
            this.successRate = successRate;
            this.responseTimeStats = timeStats;
        }
    }

    public static class ResponseTimeStats {
        public final long p50; // 50th percentile
        public final long p95; // 95th percentile
        public final long p99; // 99th percentile
        public final long average;

        public ResponseTimeStats(long p50, long p95, long p99, long average) {
            this.p50 = p50;
            this.p95 = p95;
            this.p99 = p99;
            this.average = average;
        }
    }

    public static class CircuitBreakerMetrics {
        public final long opens;
        public final long closes;

        public CircuitBreakerMetrics(long opens, long closes) {
            this.opens = opens;
            this.closes = closes;
        }
    }

    public static class CacheMetrics {
        public final long hits;
        public final long misses;
        public final double hitRate;

        public CacheMetrics(long hits, long misses, double hitRate) {
            this.hits = hits;
            this.misses = misses;
            this.hitRate = hitRate;
        }
    }
}