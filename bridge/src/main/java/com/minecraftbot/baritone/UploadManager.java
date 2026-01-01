package com.minecraftbot.baritone;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.*;
import java.net.Socket;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicLong;

/**
 * UploadManager provides improved management of schematic uploads with proper state tracking,
 * validation, chunked upload support, and error recovery.
 */
public class UploadManager {

    private static final Logger LOGGER = LoggerFactory.getLogger(UploadManager.class);

    // Configuration constants
    private static final int UPLOAD_TIMEOUT_MS = 300000; // 5 minutes
    private static final int MAX_CONCURRENT_UPLOADS = 5;
    private static final int CLEANUP_INTERVAL_MS = 60000; // 1 minute
    private static final int MAX_UPLOAD_QUEUE_SIZE = 50;

    /**
     * Upload status enumeration
     */
    public enum UploadStatus {
        INITIALIZING,
        IN_PROGRESS,
        COMPLETED,
        FAILED,
        TIMED_OUT,
        CANCELLED
    }

    /**
     * Upload priority levels
     */
    public enum UploadPriority {
        LOW(1),
        NORMAL(2),
        HIGH(3),
        CRITICAL(4);

        private final int value;

        UploadPriority(int value) {
            this.value = value;
        }

        public int getValue() {
            return value;
        }
    }

    /**
     * Represents an upload task
     */
    public static class UploadTask {
        private final String name;
        private final File targetFile;
        private final Socket owner;
        private UploadStatus status;
        private UploadPriority priority;
        private long startTime;
        private long lastActivity;
        private long expectedSize;
        private long receivedBytes;
        private OutputStream outputStream;
        private MessageDigest digest;
        private String sha256Hash;
        private String errorMessage;

        private UploadTask(String name, File targetFile, Socket owner, UploadPriority priority) {
            this.name = name;
            this.targetFile = targetFile;
            this.owner = owner;
            this.priority = priority;
            this.status = UploadStatus.INITIALIZING;
            this.startTime = System.currentTimeMillis();
            this.lastActivity = startTime;
        }

        // Getters
        public String getName() { return name; }
        public File getTargetFile() { return targetFile; }
        public Socket getOwner() { return owner; }
        public UploadStatus getStatus() { return status; }
        public UploadPriority getPriority() { return priority; }
        public long getStartTime() { return startTime; }
        public long getLastActivity() { return lastActivity; }
        public long getExpectedSize() { return expectedSize; }
        public long getReceivedBytes() { return receivedBytes; }
        public String getSha256Hash() { return sha256Hash; }
        public String getErrorMessage() { return errorMessage; }

        // Setters
        public void setStatus(UploadStatus status) { this.status = status; }
        public void setPriority(UploadPriority priority) { this.priority = priority; }
        public void setExpectedSize(long expectedSize) { this.expectedSize = expectedSize; }
        public void setErrorMessage(String errorMessage) { this.errorMessage = errorMessage; }

        public void updateActivity() {
            this.lastActivity = System.currentTimeMillis();
        }

        public void addBytes(long bytes) {
            this.receivedBytes += bytes;
            updateActivity();
        }

        public double getProgressPercentage() {
            if (expectedSize <= 0) return 0.0;
            return (double) receivedBytes / expectedSize * 100.0;
        }

        public boolean isExpired() {
            return System.currentTimeMillis() - lastActivity > UPLOAD_TIMEOUT_MS;
        }

        public boolean isCompleted() {
            return status == UploadStatus.COMPLETED;
        }

        public boolean isFailed() {
            return status == UploadStatus.FAILED || status == UploadStatus.TIMED_OUT || status == UploadStatus.CANCELLED;
        }
    }

    // Thread-safe data structures
    private final ConcurrentHashMap<String, UploadTask> activeUploads = new ConcurrentHashMap<>();
    private final PriorityBlockingQueue<UploadTask> uploadQueue = new PriorityBlockingQueue<>(
        MAX_UPLOAD_QUEUE_SIZE,
        Comparator.comparingInt((UploadTask task) -> task.getPriority().getValue()).reversed()
    );
    private final ScheduledExecutorService cleanupExecutor = Executors.newSingleThreadScheduledExecutor(
        r -> {
            Thread t = new Thread(r, "UploadManager-Cleanup");
            t.setDaemon(true);
            return t;
        }
    );

    private final File schematicDir;
    private final AtomicLong totalUploadsCompleted = new AtomicLong(0);
    private final AtomicLong totalUploadsFailed = new AtomicLong(0);
    private final AtomicLong totalBytesUploaded = new AtomicLong(0);

    public UploadManager(File schematicDir) {
        this.schematicDir = schematicDir;
        startCleanupTask();
        LOGGER.info("UploadManager initialized with schematic directory: {}", schematicDir.getAbsolutePath());
    }

    /**
     * Initialize a new upload
     */
    public boolean startUpload(String name, long expectedSize, Socket owner, UploadPriority priority) {
        if (activeUploads.size() >= MAX_CONCURRENT_UPLOADS) {
            if (uploadQueue.size() >= MAX_UPLOAD_QUEUE_SIZE) {
                LOGGER.warn("Upload queue is full, rejecting upload: {}", name);
                return false;
            }
            // Queue the upload
            UploadTask task = new UploadTask(name, new File(schematicDir, name), owner, priority);
            task.setExpectedSize(expectedSize);
            uploadQueue.offer(task);
            LOGGER.info("Upload queued: {} (priority: {}, queue size: {})", name, priority, uploadQueue.size());
            return true;
        }

        try {
            UploadTask task = new UploadTask(name, new File(schematicDir, name), owner, priority);
            task.setExpectedSize(expectedSize);

            // Initialize file output stream
            task.outputStream = new FileOutputStream(task.targetFile);

            // Initialize SHA256 digest
            task.digest = MessageDigest.getInstance("SHA-256");

            task.setStatus(UploadStatus.IN_PROGRESS);
            activeUploads.put(name, task);

            LOGGER.info("Upload started: {} (expected size: {} bytes)", name, expectedSize);
            return true;

        } catch (IOException | NoSuchAlgorithmException e) {
            LOGGER.error("Failed to start upload: {}", name, e);
            return false;
        }
    }

    /**
     * Process an upload chunk
     */
    public boolean processChunk(String name, byte[] data) {
        UploadTask task = activeUploads.get(name);
        if (task == null) {
            LOGGER.warn("Upload task not found: {}", name);
            return false;
        }

        if (task.getStatus() != UploadStatus.IN_PROGRESS) {
            LOGGER.warn("Upload not in progress: {} (status: {})", name, task.getStatus());
            return false;
        }

        try {
            task.outputStream.write(data);
            task.digest.update(data);
            task.addBytes(data.length);

            // Check if upload is complete (if expected size was provided)
            if (task.expectedSize > 0 && task.receivedBytes >= task.expectedSize) {
                completeUpload(task);
            }

            return true;

        } catch (IOException e) {
            LOGGER.error("Failed to process chunk for upload: {}", name, e);
            failUpload(task, "Chunk processing failed: " + e.getMessage());
            return false;
        }
    }

    /**
     * Complete an upload
     */
    public boolean completeUpload(String name, String expectedSha256) {
        UploadTask task = activeUploads.get(name);
        if (task == null) {
            LOGGER.warn("Upload task not found for completion: {}", name);
            return false;
        }

        try {
            // Close the output stream
            if (task.outputStream != null) {
                task.outputStream.close();
            }

            // Compute SHA256 hash
            byte[] hashBytes = task.digest.digest();
            StringBuilder hexString = new StringBuilder();
            for (byte b : hashBytes) {
                hexString.append(String.format("%02x", b));
            }
            task.sha256Hash = hexString.toString();

            // Validate hash if provided
            if (expectedSha256 != null && !expectedSha256.equalsIgnoreCase(task.sha256Hash)) {
                failUpload(task, "SHA256 validation failed. Expected: " + expectedSha256 + ", Got: " + task.sha256Hash);
                return false;
            }

            task.setStatus(UploadStatus.COMPLETED);
            activeUploads.remove(name);

            totalUploadsCompleted.incrementAndGet();
            totalBytesUploaded.addAndGet(task.receivedBytes);

            LOGGER.info("Upload completed: {} (size: {} bytes, SHA256: {})",
                       name, task.receivedBytes, task.sha256Hash);
            return true;

        } catch (IOException e) {
            LOGGER.error("Failed to complete upload: {}", name, e);
            failUpload(task, "Completion failed: " + e.getMessage());
            return false;
        }
    }

    /**
     * Cancel an upload
     */
    public boolean cancelUpload(String name) {
        UploadTask task = activeUploads.remove(name);
        if (task == null) {
            // Check queue
            uploadQueue.removeIf(t -> t.getName().equals(name));
            LOGGER.info("Upload cancelled from queue: {}", name);
            return true;
        }

        cleanupTask(task);
        task.setStatus(UploadStatus.CANCELLED);
        LOGGER.info("Upload cancelled: {}", name);
        return true;
    }

    /**
     * Get upload progress information
     */
    public Map<String, Object> getUploadProgress(String name) {
        UploadTask task = activeUploads.get(name);
        if (task == null) {
            return Map.of("error", "Upload not found: " + name);
        }

        return Map.of(
            "name", task.getName(),
            "status", task.getStatus().toString(),
            "progress_percentage", task.getProgressPercentage(),
            "received_bytes", task.getReceivedBytes(),
            "expected_size", task.getExpectedSize(),
            "start_time", task.getStartTime(),
            "last_activity", task.getLastActivity(),
            "priority", task.getPriority().toString()
        );
    }

    /**
     * Get all active uploads
     */
    public List<Map<String, Object>> getActiveUploads() {
        List<Map<String, Object>> uploads = new ArrayList<>();
        for (UploadTask task : activeUploads.values()) {
            uploads.add(Map.of(
                "name", task.getName(),
                "status", task.getStatus().toString(),
                "progress_percentage", task.getProgressPercentage(),
                "received_bytes", task.getReceivedBytes(),
                "expected_size", task.getExpectedSize(),
                "priority", task.getPriority().toString(),
                "owner", task.getOwner() != null ? task.getOwner().getRemoteSocketAddress().toString() : "unknown"
            ));
        }
        return uploads;
    }

    /**
     * Get upload statistics
     */
    public Map<String, Object> getStatistics() {
        return Map.of(
            "active_uploads", activeUploads.size(),
            "queued_uploads", uploadQueue.size(),
            "total_completed", totalUploadsCompleted.get(),
            "total_failed", totalUploadsFailed.get(),
            "total_bytes_uploaded", totalBytesUploaded.get(),
            "max_concurrent_uploads", MAX_CONCURRENT_UPLOADS,
            "max_queue_size", MAX_UPLOAD_QUEUE_SIZE
        );
    }

    /**
     * Process queued uploads when slots become available
     */
    private void processQueue() {
        while (activeUploads.size() < MAX_CONCURRENT_UPLOADS && !uploadQueue.isEmpty()) {
            UploadTask task = uploadQueue.poll();
            if (task != null) {
                try {
                    task.outputStream = new FileOutputStream(task.targetFile);
                    task.digest = MessageDigest.getInstance("SHA-256");
                    task.setStatus(UploadStatus.IN_PROGRESS);
                    task.updateActivity();
                    activeUploads.put(task.getName(), task);
                    LOGGER.info("Upload dequeued and started: {}", task.getName());
                } catch (IOException | NoSuchAlgorithmException e) {
                    LOGGER.error("Failed to start queued upload: {}", task.getName(), e);
                    task.setStatus(UploadStatus.FAILED);
                    task.setErrorMessage("Failed to initialize: " + e.getMessage());
                }
            }
        }
    }

    /**
     * Mark an upload as failed
     */
    private void failUpload(UploadTask task, String reason) {
        task.setStatus(UploadStatus.FAILED);
        task.setErrorMessage(reason);
        cleanupTask(task);
        activeUploads.remove(task.getName());
        totalUploadsFailed.incrementAndGet();
        LOGGER.error("Upload failed: {} - {}", task.getName(), reason);

        // Process queue for available slots
        processQueue();
    }

    /**
     * Complete an upload (internal method)
     */
    private void completeUpload(UploadTask task) {
        try {
            if (task.outputStream != null) {
                task.outputStream.close();
            }

            byte[] hashBytes = task.digest.digest();
            StringBuilder hexString = new StringBuilder();
            for (byte b : hashBytes) {
                hexString.append(String.format("%02x", b));
            }
            task.sha256Hash = hexString.toString();

            task.setStatus(UploadStatus.COMPLETED);
            activeUploads.remove(task.getName());

            totalUploadsCompleted.incrementAndGet();
            totalBytesUploaded.addAndGet(task.receivedBytes);

            LOGGER.info("Upload auto-completed: {} (size: {} bytes, SHA256: {})",
                       task.getName(), task.receivedBytes, task.sha256Hash);

        } catch (IOException e) {
            failUpload(task, "Auto-completion failed: " + e.getMessage());
        }

        // Process queue for available slots
        processQueue();
    }

    /**
     * Clean up task resources
     */
    private void cleanupTask(UploadTask task) {
        try {
            if (task.outputStream != null) {
                task.outputStream.close();
            }
        } catch (IOException e) {
            LOGGER.warn("Error closing output stream for upload: {}", task.getName(), e);
        }

        // Delete partial file if it exists
        if (task.targetFile.exists() && task.getStatus() != UploadStatus.COMPLETED) {
            if (task.targetFile.delete()) {
                LOGGER.debug("Deleted partial upload file: {}", task.targetFile.getName());
            } else {
                LOGGER.warn("Failed to delete partial upload file: {}", task.targetFile.getName());
            }
        }
    }

    /**
     * Start the cleanup task for expired uploads
     */
    private void startCleanupTask() {
        cleanupExecutor.scheduleWithFixedDelay(this::cleanupExpiredUploads,
                                                 CLEANUP_INTERVAL_MS, CLEANUP_INTERVAL_MS, TimeUnit.MILLISECONDS);
    }

    /**
     * Clean up expired uploads
     */
    private void cleanupExpiredUploads() {
        List<String> expiredUploads = new ArrayList<>();

        for (Map.Entry<String, UploadTask> entry : activeUploads.entrySet()) {
            UploadTask task = entry.getValue();
            if (task.isExpired()) {
                expiredUploads.add(entry.getKey());
                task.setStatus(UploadStatus.TIMED_OUT);
                cleanupTask(task);
                totalUploadsFailed.incrementAndGet();
                LOGGER.warn("Upload timed out: {}", entry.getKey());
            }
        }

        for (String name : expiredUploads) {
            activeUploads.remove(name);
        }

        if (!expiredUploads.isEmpty()) {
            LOGGER.info("Cleaned up {} expired uploads", expiredUploads.size());
            processQueue();
        }
    }

    /**
     * Clean up uploads owned by a disconnected client
     */
    public void cleanupClientUploads(Socket clientSocket) {
        List<String> clientUploads = new ArrayList<>();

        for (Map.Entry<String, UploadTask> entry : activeUploads.entrySet()) {
            if (entry.getValue().getOwner() != null && entry.getValue().getOwner().equals(clientSocket)) {
                clientUploads.add(entry.getKey());
            }
        }

        for (String name : clientUploads) {
            UploadTask task = activeUploads.remove(name);
            if (task != null) {
                cleanupTask(task);
                totalUploadsFailed.incrementAndGet();
                LOGGER.debug("Cleaned up upload owned by disconnected client: {}", name);
            }
        }

        // Also clean up from queue
        uploadQueue.removeIf(task -> task.getOwner() != null && task.getOwner().equals(clientSocket));

        if (!clientUploads.isEmpty()) {
            processQueue();
        }
    }

    /**
     * Shutdown the upload manager
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

        // Cancel all active uploads
        for (UploadTask task : activeUploads.values()) {
            cleanupTask(task);
        }
        activeUploads.clear();
        uploadQueue.clear();

        LOGGER.info("UploadManager shutdown complete");
    }

    // Convenience methods for backward compatibility
    public boolean startUpload(String name, Socket owner) {
        return startUpload(name, -1, owner, UploadPriority.NORMAL);
    }

    public boolean completeUpload(String name) {
        return completeUpload(name, null);
    }
}