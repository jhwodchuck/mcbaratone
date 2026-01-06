package com.minecraftbot.baritone;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Circuit breaker implementation to prevent cascade failures.
 * Tracks failure rates and temporarily stops command execution when failure threshold is exceeded.
 */
public class CircuitBreaker {
    private static final Logger logger = LoggerFactory.getLogger(CircuitBreaker.class);

    public enum State {
        CLOSED,     // Normal operation - requests allowed
        OPEN,       // Circuit is open - requests blocked
        HALF_OPEN   // Testing recovery - single request allowed
    }

    private final String name;
    private volatile State state = State.CLOSED;
    private final int failureThreshold;      // Number of failures to trigger open
    private final int successThreshold;      // Number of successes needed in half-open to close
    private final long timeoutMs;            // Time to wait before trying half-open
    private final long windowMs;             // Time window for failure counting

    private final AtomicInteger failureCount = new AtomicInteger(0);
    private final AtomicInteger successCount = new AtomicInteger(0);
    private final AtomicLong lastFailureTime = new AtomicLong(0);
    private final AtomicLong lastStateChangeTime = new AtomicLong(System.currentTimeMillis());

    // Default configuration
    private static final int DEFAULT_FAILURE_THRESHOLD = 10;
    private static final int DEFAULT_SUCCESS_THRESHOLD = 1;
    private static final long DEFAULT_TIMEOUT_MS = 15000; // 15 seconds
    private static final long DEFAULT_WINDOW_MS = 300000; // 5 minutes

    public CircuitBreaker(String name) {
        this(name, DEFAULT_FAILURE_THRESHOLD, DEFAULT_SUCCESS_THRESHOLD, DEFAULT_TIMEOUT_MS, DEFAULT_WINDOW_MS);
    }

    public CircuitBreaker(String name, int failureThreshold, int successThreshold, long timeoutMs, long windowMs) {
        this.name = name;
        this.failureThreshold = failureThreshold;
        this.successThreshold = successThreshold;
        this.timeoutMs = timeoutMs;
        this.windowMs = windowMs;
    }

    /**
     * Check if a request should be allowed through the circuit breaker.
     *
     * @return true if request should proceed, false if circuit is open
     */
    public synchronized boolean allowRequest() {
        long now = System.currentTimeMillis();

        switch (state) {
            case CLOSED:
                return true;

            case OPEN:
                // Check if timeout has passed to transition to half-open
                if (shouldAttemptRecovery(now)) {
                    transitionToHalfOpen(now);
                    return true; // Allow one test request
                }
                return false;

            case HALF_OPEN:
                return true; // Allow request in half-open state

            default:
                return false;
        }
    }

    /**
     * Record a successful request.
     */
    public synchronized void recordSuccess() {
        long now = System.currentTimeMillis();

        if (state == State.HALF_OPEN) {
            int successes = successCount.incrementAndGet();
            if (successes >= successThreshold) {
                transitionToClosed(now);
            }
        } else if (state == State.CLOSED) {
            // Reset failure count on success in closed state
            failureCount.set(0);
        }
    }

    /**
     * Record a failed request.
     */
    public synchronized void recordFailure() {
        long now = System.currentTimeMillis();
        lastFailureTime.set(now);

        if (state == State.HALF_OPEN) {
            // Single failure in half-open reopens circuit
            transitionToOpen(now);
        } else if (state == State.CLOSED) {
            int failures = failureCount.incrementAndGet();
            if (failures >= failureThreshold) {
                transitionToOpen(now);
            }
        }
    }

    /**
     * Get current circuit breaker state.
     */
    public State getState() {
        return state;
    }

    /**
     * Get failure count in current window.
     */
    public int getFailureCount() {
        return failureCount.get();
    }

    /**
     * Check if circuit should attempt recovery based on time window.
     */
    private boolean shouldAttemptRecovery(long now) {
        return state == State.OPEN && (now - lastStateChangeTime.get()) >= timeoutMs;
    }

    private void transitionToClosed(long now) {
        state = State.CLOSED;
        lastStateChangeTime.set(now);
        failureCount.set(0);
        successCount.set(0);
        logger.info("Circuit breaker '{}' transitioned to CLOSED", name);
    }

    private void transitionToOpen(long now) {
        state = State.OPEN;
        lastStateChangeTime.set(now);
        successCount.set(0);
        logger.warn("Circuit breaker '{}' transitioned to OPEN (failures: {})", name, failureCount.get());
    }

    private void transitionToHalfOpen(long now) {
        state = State.HALF_OPEN;
        lastStateChangeTime.set(now);
        successCount.set(0);
        logger.info("Circuit breaker '{}' transitioned to HALF_OPEN", name);
    }

    /**
     * Reset circuit breaker to closed state (for testing/recovery).
     */
    public synchronized void reset() {
        transitionToClosed(System.currentTimeMillis());
    }
}
