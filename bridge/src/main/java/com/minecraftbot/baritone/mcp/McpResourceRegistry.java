package com.minecraftbot.baritone.mcp;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.minecraftbot.baritone.CommandDispatcher;
import com.minecraftbot.baritone.CommandResult;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Registry of MCP resources with their handlers.
 * Manages resource reading and subscription.
 */
public class McpResourceRegistry {

    private static final Logger LOGGER = LoggerFactory.getLogger("mcp-resources");
    private static final Gson GSON = new GsonBuilder().create();

    private final CommandDispatcher commandDispatcher;
    
    // Resource definitions: uri -> ResourceDefinition
    private final Map<String, ResourceDefinition> resources = new LinkedHashMap<>();
    
    // Subscriptions: uri -> set of subscribed sessions
    private final Map<String, Set<McpSession>> subscriptions = new ConcurrentHashMap<>();

    public McpResourceRegistry(CommandDispatcher commandDispatcher) {
        this.commandDispatcher = commandDispatcher;
        registerAllResources();
    }

    /**
     * Resource definition with handler.
     */
    public static class ResourceDefinition {
        public final String uri;
        public final String name;
        public final String description;
        public final String mimeType;
        public final ResourceHandler handler;

        public ResourceDefinition(String uri, String name, String description, 
                                  String mimeType, ResourceHandler handler) {
            this.uri = uri;
            this.name = name;
            this.description = description;
            this.mimeType = mimeType;
            this.handler = handler;
        }

        public JsonObject toJson() {
            JsonObject json = new JsonObject();
            json.addProperty("uri", uri);
            json.addProperty("name", name);
            json.addProperty("description", description);
            json.addProperty("mimeType", mimeType);
            return json;
        }
    }

    @FunctionalInterface
    public interface ResourceHandler {
        JsonObject read();
    }

    /**
     * List all registered resources.
     */
    public JsonObject listResources() {
        JsonObject result = new JsonObject();
        JsonArray resourcesArray = new JsonArray();
        
        for (ResourceDefinition resource : resources.values()) {
            resourcesArray.add(resource.toJson());
        }
        
        result.add("resources", resourcesArray);
        return result;
    }

    /**
     * Read a resource by URI.
     */
    public JsonObject readResource(String uri) {
        ResourceDefinition resource = resources.get(uri);
        if (resource == null) {
            throw new McpException(McpErrorCodes.RESOURCE_NOT_FOUND, "Unknown resource: " + uri);
        }

        try {
            JsonObject content = resource.handler.read();
            
            // Wrap in MCP resource response format
            JsonObject result = new JsonObject();
            JsonArray contents = new JsonArray();
            
            JsonObject contentItem = new JsonObject();
            contentItem.addProperty("uri", uri);
            contentItem.addProperty("mimeType", resource.mimeType);
            contentItem.addProperty("text", GSON.toJson(content));
            contents.add(contentItem);
            
            result.add("contents", contents);
            return result;
            
        } catch (McpException e) {
            throw e;
        } catch (Exception e) {
            LOGGER.error("Resource read failed: {}", uri, e);
            throw new McpException(McpErrorCodes.INTERNAL_ERROR, 
                "Resource read failed: " + e.getMessage(), e);
        }
    }

    /**
     * Subscribe a session to resource updates.
     */
    public void subscribe(String uri, McpSession session) {
        if (!resources.containsKey(uri)) {
            throw new McpException(McpErrorCodes.RESOURCE_NOT_FOUND, "Unknown resource: " + uri);
        }
        
        subscriptions.computeIfAbsent(uri, k -> ConcurrentHashMap.newKeySet())
            .add(session);
        LOGGER.debug("Session subscribed to resource: {}", uri);
    }

    /**
     * Unsubscribe a session from resource updates.
     */
    public void unsubscribe(String uri, McpSession session) {
        Set<McpSession> sessions = subscriptions.get(uri);
        if (sessions != null) {
            sessions.remove(session);
            LOGGER.debug("Session unsubscribed from resource: {}", uri);
        }
    }

    /**
     * Notify subscribers that a resource has been updated.
     */
    public void notifyResourceUpdated(String uri) {
        Set<McpSession> sessions = subscriptions.get(uri);
        if (sessions == null || sessions.isEmpty()) {
            return;
        }

        JsonObject params = new JsonObject();
        params.addProperty("uri", uri);

        for (McpSession session : sessions) {
            session.sendNotification("notifications/resources/updated", params);
        }
    }

    /**
     * Remove a session from all subscriptions (called on disconnect).
     */
    public void removeSession(McpSession session) {
        for (Set<McpSession> sessions : subscriptions.values()) {
            sessions.remove(session);
        }
    }

    /**
     * Register all MCP resources.
     */
    private void registerAllResources() {
        // Player state
        register("baritone://state", "Player State", 
            "Current player state including position, health, and pathing status",
            "application/json", this::readState);

        // Inventory
        register("baritone://inventory", "Inventory",
            "Current player inventory contents",
            "application/json", this::readInventory);

        // Mission status
        register("baritone://mission/status", "Mission Status",
            "Current mission controller status",
            "application/json", this::readMissionStatus);

        // Cache stats
        register("baritone://cache/stats", "Cache Stats",
            "Block cache statistics",
            "application/json", this::readCacheStats);

        // Upload status
        register("baritone://upload/status", "Upload Status",
            "Schematic upload progress",
            "application/json", this::readUploadStatus);

        // Analytics health
        register("baritone://analytics/health", "System Health",
            "System health metrics",
            "application/json", this::readHealthMetrics);

        // Analytics performance
        register("baritone://analytics/performance", "Performance Metrics",
            "Command performance analytics",
            "application/json", this::readPerformanceMetrics);

        // Analytics report
        register("baritone://analytics/report", "Analytics Report",
            "Full analytics report",
            "application/json", this::readAnalyticsReport);

        LOGGER.info("Registered {} MCP resources", resources.size());
    }

    // ==================== Resource Handlers ====================

    private JsonObject readState() {
        CommandResult result = commandDispatcher.dispatch("get_state", new JsonObject());
        if (result.isSuccess()) {
            return result.getData();
        }
        return errorResponse(result);
    }

    private JsonObject readInventory() {
        CommandResult result = commandDispatcher.dispatch("get_inventory", new JsonObject());
        if (result.isSuccess()) {
            return result.getData();
        }
        return errorResponse(result);
    }

    private JsonObject readMissionStatus() {
        JsonObject missionArgs = new JsonObject();
        missionArgs.addProperty("action", "status");
        CommandResult result = commandDispatcher.dispatch("mission", missionArgs);
        if (result.isSuccess()) {
            return result.getData();
        }
        return errorResponse(result);
    }

    private JsonObject readCacheStats() {
        // Try to get cache stats via command dispatcher
        CommandResult result = commandDispatcher.dispatch("cache_stats", new JsonObject());
        if (result.isSuccess()) {
            return result.getData();
        }
        
        // Fallback to basic stats
        JsonObject stats = new JsonObject();
        stats.addProperty("cached_blocks", 0);
        stats.addProperty("hit_rate", 0.0);
        stats.addProperty("cache_size_bytes", 0);
        stats.addProperty("eviction_count", 0);
        return stats;
    }

    private JsonObject readUploadStatus() {
        // Try to get upload status via command dispatcher
        CommandResult result = commandDispatcher.dispatch("upload_stats", new JsonObject());
        if (result.isSuccess()) {
            return result.getData();
        }
        
        // Fallback to basic status
        JsonObject status = new JsonObject();
        status.addProperty("active_uploads", 0);
        status.addProperty("completed_uploads", 0);
        status.addProperty("failed_uploads", 0);
        status.addProperty("bytes_transferred", 0);
        return status;
    }

    private JsonObject readHealthMetrics() {
        JsonObject health = new JsonObject();
        Runtime runtime = Runtime.getRuntime();
        
        // Memory metrics
        long usedMemory = runtime.totalMemory() - runtime.freeMemory();
        long maxMemory = runtime.maxMemory();
        health.addProperty("memory_used_mb", usedMemory / (1024 * 1024));
        health.addProperty("memory_max_mb", maxMemory / (1024 * 1024));
        health.addProperty("memory_free_mb", runtime.freeMemory() / (1024 * 1024));
        health.addProperty("memory_usage_percent", (double) usedMemory / maxMemory * 100);
        
        // System metrics
        health.addProperty("processors", runtime.availableProcessors());
        health.addProperty("java_version", System.getProperty("java.version"));
        
        // Determine health status
        String status = (double) usedMemory / maxMemory > 0.9 ? "warning" : "healthy";
        health.addProperty("status", status);
        health.addProperty("timestamp", System.currentTimeMillis());
        
        return health;
    }

    private JsonObject readPerformanceMetrics() {
        // Try to get metrics from MetricsCollector via command dispatcher
        CommandResult result = commandDispatcher.dispatch("get_metrics", new JsonObject());
        if (result.isSuccess()) {
            return result.getData();
        }
        
        // Fallback performance metrics
        JsonObject metrics = new JsonObject();
        metrics.addProperty("commands_executed", 0);
        metrics.addProperty("avg_latency_ms", 0.0);
        metrics.addProperty("max_latency_ms", 0.0);
        metrics.addProperty("error_count", 0);
        metrics.addProperty("cache_hits", 0);
        metrics.addProperty("cache_misses", 0);
        return metrics;
    }

    private JsonObject readAnalyticsReport() {
        JsonObject report = new JsonObject();
        
        // Aggregate all metrics
        report.add("health", readHealthMetrics());
        report.add("performance", readPerformanceMetrics());
        report.add("cache", readCacheStats());
        report.add("uploads", readUploadStatus());
        
        // Get mission status if available
        JsonObject missionArgs = new JsonObject();
        missionArgs.addProperty("action", "status");
        CommandResult missionResult = commandDispatcher.dispatch("mission", missionArgs);
        if (missionResult.isSuccess()) {
            report.add("mission", missionResult.getData());
        }
        
        // Report metadata
        report.addProperty("generated_at", System.currentTimeMillis());
        report.addProperty("mcp_version", McpCapabilities.PROTOCOL_VERSION);
        
        return report;
    }

    // ==================== Helper Methods ====================

    private void register(String uri, String name, String description, 
                         String mimeType, ResourceHandler handler) {
        resources.put(uri, new ResourceDefinition(uri, name, description, mimeType, handler));
    }

    private JsonObject errorResponse(CommandResult result) {
        JsonObject error = new JsonObject();
        error.addProperty("error", result.getErrorMessage());
        return error;
    }
}
