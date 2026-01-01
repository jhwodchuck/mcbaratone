package com.minecraftbot.baritone;

import com.google.gson.JsonObject;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentLinkedDeque;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.Predicate;

/**
 * EventManager handles event registration, buffering, and publishing.
 * Provides thread-safe event management with configurable size limits and TTL.
 */
public class EventManager {

    private static final Logger LOGGER = LoggerFactory.getLogger("baritone-event-manager");

    // Configuration
    private volatile int maxBufferSize = 100;
    private volatile long ttlMs = 300000; // 5 minutes default

    // Event storage - thread-safe deque for FIFO with TTL cleanup
    private final ConcurrentLinkedDeque<Event> eventBuffer = new ConcurrentLinkedDeque<>();

    // Event listeners for push-based subscription
    private final Map<EventType, List<EventListener>> listeners = new ConcurrentHashMap<>();
    private final List<EventListener> globalListeners = new CopyOnWriteArrayList<>();

    // Statistics
    private final AtomicInteger totalEventsProcessed = new AtomicInteger(0);
    private final AtomicInteger eventsDropped = new AtomicInteger(0);

    /**
     * Event types supported by the system
     */
    public enum EventType {
        CHAT,
        BLOCK_INTERACT,
        ENTITY_SPAWN,
        ENTITY_MOVE,
        ENTITY_DESPAWN,
        PATHFINDING_STATE,
        MISSION,
        TICK_UPDATE,
        DIMENSION_CHANGE,
        INVENTORY_CHANGE,
        DEATH,
        RESPAWN,
        ENTITY_UPDATE,
        DAMAGE
    }

    /**
     * Event priority levels
     */
    public enum Priority {
        LOW(0),
        NORMAL(1),
        HIGH(2),
        CRITICAL(3);

        private final int value;

        Priority(int value) {
            this.value = value;
        }

        public int getValue() {
            return value;
        }
    }

    /**
     * Internal event representation
     */
    public static class Event {
        private final String type;
        private final JsonObject data;
        private final long timestamp;
        private final Priority priority;
        private final String source;

        public Event(String type, JsonObject data, Priority priority, String source) {
            this.type = type;
            this.data = data != null ? data : new JsonObject();
            this.timestamp = System.currentTimeMillis();
            this.priority = priority != null ? priority : Priority.NORMAL;
            this.source = source;
        }

        public String getType() { return type; }
        public JsonObject getData() { return data; }
        public long getTimestamp() { return timestamp; }
        public Priority getPriority() { return priority; }
        public String getSource() { return source; }

        public boolean isExpired(long ttlMs) {
            return System.currentTimeMillis() - timestamp > ttlMs;
        }

        @Override
        public String toString() {
            return String.format("Event{type='%s', priority=%s, timestamp=%d, source='%s'}",
                    type, priority, timestamp, source);
        }
    }

    /**
     * Event listener interface for push-based subscription
     */
    public interface EventListener {
        void onEvent(Event event);
    }

    /**
     * Initialize with default configuration
     */
    public EventManager() {
        // Initialize listener maps for all event types
        for (EventType type : EventType.values()) {
            listeners.put(type, new CopyOnWriteArrayList<>());
        }
    }

    /**
     * Initialize with custom configuration
     */
    public EventManager(int maxBufferSize, long ttlMs) {
        this();
        this.maxBufferSize = maxBufferSize;
        this.ttlMs = ttlMs;
    }

    // Configuration methods

    public void setMaxBufferSize(int size) {
        if (size < 1) {
            throw new IllegalArgumentException("Buffer size must be positive");
        }
        this.maxBufferSize = size;
        cleanupExpiredEvents(); // Clean up immediately if size reduced
    }

    public void setTTL(long ttlMs) {
        if (ttlMs < 0) {
            throw new IllegalArgumentException("TTL must be non-negative");
        }
        this.ttlMs = ttlMs;
        cleanupExpiredEvents();
    }

    public int getMaxBufferSize() { return maxBufferSize; }
    public long getTTL() { return ttlMs; }
    public int getBufferSize() { return eventBuffer.size(); }

    // Publishing methods

    /**
     * Publish an event with default priority
     */
    public void publishEvent(String type, JsonObject data) {
        publishEvent(type, data, Priority.NORMAL, "system");
    }

    /**
     * Publish an event with custom priority
     */
    public void publishEvent(String type, JsonObject data, Priority priority) {
        publishEvent(type, data, priority, "system");
    }

    /**
     * Publish an event with full parameters
     */
    public void publishEvent(String type, JsonObject data, Priority priority, String source) {
        if (type == null || type.trim().isEmpty()) {
            LOGGER.warn("Ignoring event with null/empty type");
            return;
        }

        Event event = new Event(type, data, priority, source);

        // Add to buffer with size management
        synchronized (eventBuffer) {
            // Clean expired events first
            cleanupExpiredEvents();

            // Add new event
            eventBuffer.addLast(event);

            // Trim buffer if too large (remove oldest)
            while (eventBuffer.size() > maxBufferSize) {
                Event removed = eventBuffer.removeFirst();
                eventsDropped.incrementAndGet();
                LOGGER.debug("Dropped expired event due to buffer size limit: {}", removed);
            }
        }

        totalEventsProcessed.incrementAndGet();

        // Notify listeners
        notifyListeners(event);

        LOGGER.debug("Published event: {}", event);
    }

    /**
     * Publish an event using EventType enum
     */
    public void publishEvent(EventType eventType, JsonObject data) {
        publishEvent(eventType, data, Priority.NORMAL, "system");
    }

    /**
     * Publish an event using EventType enum with priority
     */
    public void publishEvent(EventType eventType, JsonObject data, Priority priority) {
        publishEvent(eventType, data, priority, "system");
    }

    /**
     * Publish an event using EventType enum with full parameters
     */
    public void publishEvent(EventType eventType, JsonObject data, Priority priority, String source) {
        publishEvent(eventType.name().toLowerCase(), data, priority, source);
    }

    // Subscription methods

    /**
     * Subscribe to all events
     */
    public void subscribe(EventListener listener) {
        if (listener != null) {
            globalListeners.add(listener);
        }
    }

    /**
     * Subscribe to specific event type
     */
    public void subscribe(EventType eventType, EventListener listener) {
        if (listener != null && eventType != null) {
            listeners.get(eventType).add(listener);
        }
    }

    /**
     * Subscribe to multiple event types
     */
    public void subscribe(Set<EventType> eventTypes, EventListener listener) {
        if (listener != null && eventTypes != null) {
            for (EventType type : eventTypes) {
                subscribe(type, listener);
            }
        }
    }

    /**
     * Unsubscribe from all events
     */
    public void unsubscribe(EventListener listener) {
        if (listener != null) {
            globalListeners.remove(listener);
            for (List<EventListener> typeListeners : listeners.values()) {
                typeListeners.remove(listener);
            }
        }
    }

    // Polling methods

    /**
     * Poll all buffered events, removing expired ones
     */
    public List<Event> pollEvents() {
        return pollEvents((Predicate<Event>) null);
    }

    /**
     * Poll events of specific type
     */
    public List<Event> pollEvents(String type) {
        return pollEvents(event -> type == null || event.getType().equals(type));
    }

    /**
     * Poll events matching predicate
     */
    public List<Event> pollEvents(Predicate<Event> filter) {
        List<Event> result = new ArrayList<>();

        synchronized (eventBuffer) {
            cleanupExpiredEvents();

            Iterator<Event> iterator = eventBuffer.iterator();
            while (iterator.hasNext()) {
                Event event = iterator.next();
                if (filter == null || filter.test(event)) {
                    result.add(event);
                    iterator.remove(); // Remove from buffer when polled
                }
            }
        }

        return result;
    }

    /**
     * Peek at events without removing them
     */
    public List<Event> peekEvents() {
        return peekEvents((Predicate<Event>) null);
    }

    /**
     * Peek at events of specific type without removing
     */
    public List<Event> peekEvents(String type) {
        return peekEvents(event -> type == null || event.getType().equals(type));
    }

    /**
     * Peek at events matching predicate without removing
     */
    public List<Event> peekEvents(Predicate<Event> filter) {
        List<Event> result = new ArrayList<>();

        synchronized (eventBuffer) {
            cleanupExpiredEvents();

            for (Event event : eventBuffer) {
                if (filter == null || filter.test(event)) {
                    result.add(event);
                }
            }
        }

        return result;
    }

    // Utility methods

    /**
     * Clear all buffered events
     */
    public void clearBuffer() {
        synchronized (eventBuffer) {
            int cleared = eventBuffer.size();
            eventBuffer.clear();
            LOGGER.debug("Cleared {} events from buffer", cleared);
        }
    }

    /**
     * Get statistics
     */
    public JsonObject getStats() {
        JsonObject stats = new JsonObject();
        stats.addProperty("buffer_size", eventBuffer.size());
        stats.addProperty("max_buffer_size", maxBufferSize);
        stats.addProperty("ttl_ms", ttlMs);
        stats.addProperty("total_events_processed", totalEventsProcessed.get());
        stats.addProperty("events_dropped", eventsDropped.get());
        stats.addProperty("global_listeners", globalListeners.size());

        JsonObject typeListeners = new JsonObject();
        for (Map.Entry<EventType, List<EventListener>> entry : listeners.entrySet()) {
            typeListeners.addProperty(entry.getKey().name().toLowerCase(), entry.getValue().size());
        }
        stats.add("type_listeners", typeListeners);

        return stats;
    }

    // Private helper methods

    private void cleanupExpiredEvents() {
        synchronized (eventBuffer) {
            eventBuffer.removeIf(event -> event.isExpired(ttlMs));
        }
    }

    private void notifyListeners(Event event) {
        // Notify global listeners
        for (EventListener listener : globalListeners) {
            try {
                listener.onEvent(event);
            } catch (Exception e) {
                LOGGER.warn("Error notifying global event listener", e);
            }
        }

        // Notify type-specific listeners
        try {
            EventType eventType = EventType.valueOf(event.getType().toUpperCase());
            List<EventListener> typeListeners = listeners.get(eventType);
            if (typeListeners != null) {
                for (EventListener listener : typeListeners) {
                    try {
                        listener.onEvent(event);
                    } catch (Exception e) {
                        LOGGER.warn("Error notifying event listener for type: {}", eventType, e);
                    }
                }
            }
        } catch (IllegalArgumentException e) {
            // Event type not in enum, skip type-specific listeners
        }
    }
}