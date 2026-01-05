package com.minecraftbot.baritone;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.function.Supplier;

/**
 * Retry handler with exponential backoff for transient failures.
 * Implements configurable retry logic with jitter to prevent thundering herd.
 */
public class RetryHandler {
    private static final Logger logger = LoggerFactory.getLogger(RetryHandler.class);

    private final int maxRetries;
    private final long baseDelayMs;
    private final double multiplier;
    private final long maxDelayMs;
    private final double jitterFactor;

    // Default configuration
    private static final int DEFAULT_MAX_RETRIES = 3;
    private static final long DEFAULT_BASE_DELAY_MS = 1000; // 1 second
    private static final double DEFAULT_MULTIPLIER = 2.0;
    private static final long DEFAULT_MAX_DELAY_MS = 30000; // 30 seconds
    private static final double DEFAULT_JITTER_FACTOR = 0.1; // 10% jitter

    public RetryHandler() {
        this(DEFAULT_MAX_RETRIES, DEFAULT_BASE_DELAY_MS, DEFAULT_MULTIPLIER, DEFAULT_MAX_DELAY_MS, DEFAULT_JITTER_FACTOR);
    }

    public RetryHandler(int maxRetries, long baseDelayMs, double multiplier, long maxDelayMs, double jitterFactor) {
        this.maxRetries = maxRetries;
        this.baseDelayMs = baseDelayMs;
        this.multiplier = multiplier;
        this.maxDelayMs = maxDelayMs;
        this.jitterFactor = jitterFactor;
    }

    /**
     * Execute a supplier with retry logic on failures.
     *
     * @param supplier The operation to retry
     * @param operationName Name for logging purposes
     * @return The result of the successful operation
     * @throws RuntimeException wrapping the last exception if all retries fail
     */
    public <T> T executeWithRetry(Supplier<T> supplier, String operationName) {
        Exception lastException = null;

        for (int attempt = 0; attempt <= maxRetries; attempt++) {
            try {
                if (attempt > 0) {
                    logger.debug("Retry attempt {} for operation: {}", attempt, operationName);
                }
                return supplier.get();
            } catch (Exception e) {
                lastException = e;

                if (attempt < maxRetries && isRetryable(e)) {
                    long delayMs = calculateDelay(attempt);
                    logger.warn("Operation '{}' failed on attempt {} ({}), retrying in {}ms: {}",
                        operationName, attempt + 1, maxRetries + 1, delayMs, e.getMessage());

                    try {
                        Thread.sleep(delayMs);
                    } catch (InterruptedException ie) {
                        Thread.currentThread().interrupt();
                        throw new RuntimeException("Retry interrupted", ie);
                    }
                } else {
                    logger.error("Operation '{}' failed after {} attempts: {}",
                        operationName, attempt + 1, e.getMessage());
                    break;
                }
            }
        }

        throw new RuntimeException("Operation failed after " + (maxRetries + 1) + " attempts: " + operationName, lastException);
    }

    /**
     * Execute a completable future supplier with retry logic on failures.
     *
     * @param supplier The operation to retry (returns CompletableFuture)
     * @param operationName Name for logging purposes
     * @return CompletableFuture that completes with the result or last exception
     */
    public <T> CompletableFuture<T> executeAsyncWithRetry(Supplier<CompletableFuture<T>> supplier, String operationName) {
        CompletableFuture<T> result = new CompletableFuture<>();

        executeWithRetryAsync(supplier, operationName, 0, result);

        return result;
    }

    private <T> void executeWithRetryAsync(Supplier<CompletableFuture<T>> supplier, String operationName,
                                          int attempt, CompletableFuture<T> result) {
        if (attempt > 0) {
            logger.debug("Async retry attempt {} for operation: {}", attempt, operationName);
        }

        try {
            CompletableFuture<T> future = supplier.get();
            future.whenComplete((value, exception) -> {
                if (exception == null) {
                    result.complete(value);
                } else {
                    if (attempt < maxRetries && isRetryable(exception)) {
                        long delayMs = calculateDelay(attempt);
                        logger.warn("Async operation '{}' failed on attempt {} ({}), retrying in {}ms: {}",
                            operationName, attempt + 1, maxRetries + 1, delayMs, exception.getMessage());

                        // Schedule retry after delay
                        CompletableFuture.delayedExecutor(delayMs, TimeUnit.MILLISECONDS)
                            .execute(() -> executeWithRetryAsync(supplier, operationName, attempt + 1, result));
                    } else {
                        logger.error("Async operation '{}' failed after {} attempts: {}",
                            operationName, attempt + 1, exception.getMessage());
                        result.completeExceptionally(exception);
                    }
                }
            });
        } catch (Exception e) {
            if (attempt < maxRetries && isRetryable(e)) {
                long delayMs = calculateDelay(attempt);
                logger.warn("Async operation '{}' setup failed on attempt {} ({}), retrying in {}ms: {}",
                    operationName, attempt + 1, maxRetries + 1, delayMs, e.getMessage());

                CompletableFuture.delayedExecutor(delayMs, TimeUnit.MILLISECONDS)
                    .execute(() -> executeWithRetryAsync(supplier, operationName, attempt + 1, result));
            } else {
                logger.error("Async operation '{}' setup failed after {} attempts: {}",
                    operationName, attempt + 1, e.getMessage());
                result.completeExceptionally(e);
            }
        }
    }

    /**
     * Calculate delay for the given attempt number using exponential backoff with jitter.
     */
    private long calculateDelay(int attempt) {
        long delay = (long) (baseDelayMs * Math.pow(multiplier, attempt));

        // Apply jitter to prevent thundering herd
        double jitter = (Math.random() - 0.5) * 2 * jitterFactor;
        delay = (long) (delay * (1 + jitter));

        // Cap at maximum delay
        return Math.min(delay, maxDelayMs);
    }

    /**
     * Determine if an exception is retryable.
     * Currently considers timeouts and certain runtime exceptions as retryable.
     */
    private boolean isRetryable(Throwable exception) {
        // Retry on timeouts
        if (exception instanceof java.util.concurrent.TimeoutException) {
            return true;
        }

        // Retry on execution exceptions that might be transient
        if (exception instanceof java.util.concurrent.ExecutionException) {
            Throwable cause = exception.getCause();
            if (cause instanceof java.util.concurrent.TimeoutException ||
                cause instanceof java.lang.InterruptedException) {
                return true;
            }
        }

        // Retry on interrupted exceptions (may indicate thread pool issues)
        if (exception instanceof InterruptedException) {
            Thread.currentThread().interrupt(); // Restore interrupt status
            return true;
        }

        // Don't retry on programming errors or illegal arguments
        if (exception instanceof IllegalArgumentException ||
            exception instanceof IllegalStateException ||
            exception instanceof NullPointerException) {
            return false;
        }

        // Default to retryable for other exceptions (may be transient)
        return true;
    }

    /**
     * Get the maximum number of retry attempts.
     */
    public int getMaxRetries() {
        return maxRetries;
    }

    /**
     * Get the base delay in milliseconds.
     */
    public long getBaseDelayMs() {
        return baseDelayMs;
    }
}