package com.minecraftbot.baritone.mcp;

import com.google.gson.JsonObject;
import com.minecraftbot.baritone.EventManager;
import org.java_websocket.WebSocket;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Per-connection MCP session state.
 * Manages client info, subscriptions, and event listening.
 */
public class McpSession implements EventManager.EventListener {

    private static final Logger LOGGER = LoggerFactory.getLogger("mcp-session");

    private final WebSocket connection;
    private final McpServer server;
    private final long createdAt;
    
    // Session state
    private final AtomicBoolean initialized = new AtomicBoolean(false);
    private JsonObject clientInfo;
    private String clientVersion;
    
    // Event subscriptions
    private final Set<EventManager.EventType> subscribedEvents = ConcurrentHashMap.newKeySet();
    private int priorityThreshold = 0;
    
    // Active operations for cancellation
    private final ConcurrentHashMap<String, CancellableTask> activeTasks = new ConcurrentHashMap<>();

    public McpSession(WebSocket connection, McpServer server) {
        this.connection = connection;
        this.server = server;
        this.createdAt = System.currentTimeMillis();
    }

    public WebSocket getConnection() {
        return connection;
    }

    public boolean isInitialized() {
        return initialized.get();
    }

    public void setInitialized(JsonObject clientInfo) {
        this.clientInfo = clientInfo;
        if (clientInfo != null && clientInfo.has("name")) {
            this.clientVersion = clientInfo.get("name").getAsString();
        }
        this.initialized.set(true);
        LOGGER.info("MCP session initialized for client: {}", clientVersion);
    }

    public JsonObject getClientInfo() {
        return clientInfo;
    }

    public long getCreatedAt() {
        return createdAt;
    }

    // Event subscription management
    
    public void subscribeToEvents(Set<EventManager.EventType> eventTypes, int priorityThreshold) {
        this.subscribedEvents.addAll(eventTypes);
        this.priorityThreshold = priorityThreshold;
        LOGGER.debug("Session subscribed to events: {}", eventTypes);
    }

    public void unsubscribeFromEvents(Set<EventManager.EventType> eventTypes) {
        this.subscribedEvents.removeAll(eventTypes);
        LOGGER.debug("Session unsubscribed from events: {}", eventTypes);
    }

    public Set<EventManager.EventType> getSubscribedEvents() {
        return subscribedEvents;
    }

    public boolean isSubscribedTo(EventManager.EventType eventType) {
        return subscribedEvents.contains(eventType);
    }

    // EventListener implementation
    
    @Override
    public void onEvent(EventManager.Event event) {
        // Check if we're subscribed to this event type
        try {
            EventManager.EventType eventType = EventManager.EventType.valueOf(event.getType().toUpperCase());
            if (!isSubscribedTo(eventType)) {
                return;
            }
        } catch (IllegalArgumentException e) {
            // Unknown event type, skip
            return;
        }

        // Check priority threshold
        if (event.getPriority().getValue() < priorityThreshold) {
            return;
        }

        // Send notification to client
        sendEventNotification(event);
    }

    private void sendEventNotification(EventManager.Event event) {
        if (!connection.isOpen()) {
            return;
        }

        try {
            JsonObject notification = new JsonObject();
            notification.addProperty("jsonrpc", "2.0");
            notification.addProperty("method", "notifications/baritone/event");
            
            JsonObject params = new JsonObject();
            params.addProperty("type", event.getType());
            params.add("data", event.getData());
            params.addProperty("timestamp", event.getTimestamp());
            params.addProperty("priority", event.getPriority().name());
            notification.add("params", params);

            connection.send(notification.toString());
        } catch (Exception e) {
            LOGGER.warn("Failed to send event notification: {}", e.getMessage());
        }
    }

    // Task cancellation support
    
    public void registerTask(String requestId, CancellableTask task) {
        activeTasks.put(requestId, task);
    }

    public void unregisterTask(String requestId) {
        activeTasks.remove(requestId);
    }

    public boolean cancelTask(String requestId) {
        CancellableTask task = activeTasks.remove(requestId);
        if (task != null) {
            task.cancel();
            return true;
        }
        return false;
    }

    public void cancelAllTasks() {
        for (CancellableTask task : activeTasks.values()) {
            task.cancel();
        }
        activeTasks.clear();
    }

    /**
     * Send a notification to this session's client.
     */
    public void sendNotification(String method, JsonObject params) {
        if (!connection.isOpen()) {
            return;
        }

        try {
            JsonObject notification = new JsonObject();
            notification.addProperty("jsonrpc", "2.0");
            notification.addProperty("method", method);
            if (params != null) {
                notification.add("params", params);
            }
            connection.send(notification.toString());
        } catch (Exception e) {
            LOGGER.warn("Failed to send notification: {}", e.getMessage());
        }
    }

    /**
     * Clean up session resources.
     */
    public void close() {
        cancelAllTasks();
        subscribedEvents.clear();
        LOGGER.info("MCP session closed after {}ms", System.currentTimeMillis() - createdAt);
    }

    /**
     * Interface for cancellable long-running tasks.
     */
    public interface CancellableTask {
        void cancel();
    }
}
