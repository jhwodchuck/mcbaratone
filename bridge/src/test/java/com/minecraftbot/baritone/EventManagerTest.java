package com.minecraftbot.baritone;

import com.google.gson.JsonObject;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Set;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.*;

public class EventManagerTest {

    private EventManager eventManager;

    @BeforeEach
    public void setUp() {
        eventManager = new EventManager();
    }

    @Test
    public void testPublishAndPollEvents() {
        JsonObject data = new JsonObject();
        data.addProperty("test", "value");

        eventManager.publishEvent("test_event", data);

        List<EventManager.Event> events = eventManager.pollEvents("test_event");

        assertEquals(1, events.size());
        assertEquals("test_event", events.get(0).getType());
        assertEquals("value", events.get(0).getData().get("test").getAsString());
    }

    @Test
    public void testEventBufferingAndSizeLimit() {
        eventManager.setMaxBufferSize(3);

        for (int i = 0; i < 5; i++) {
            JsonObject data = new JsonObject();
            data.addProperty("id", i);
            eventManager.publishEvent("test_event", data);
        }

        List<EventManager.Event> events = eventManager.pollEvents();

        // Should only have 3 events due to buffer size limit
        assertEquals(3, events.size());
    }

    @Test
    public void testEventPrioritization() {
        JsonObject lowData = new JsonObject();
        lowData.addProperty("priority", "low");
        eventManager.publishEvent("test", lowData, EventManager.Priority.LOW);

        JsonObject highData = new JsonObject();
        highData.addProperty("priority", "high");
        eventManager.publishEvent("test", highData, EventManager.Priority.HIGH);

        List<EventManager.Event> events = eventManager.peekEvents();

        assertEquals(2, events.size());
        // Events should be in FIFO order, not priority order for basic polling
        assertEquals(EventManager.Priority.LOW, events.get(0).getPriority());
        assertEquals(EventManager.Priority.HIGH, events.get(1).getPriority());
    }

    @Test
    public void testEventTTL() throws InterruptedException {
        eventManager.setTTL(100); // 100ms TTL

        JsonObject data = new JsonObject();
        data.addProperty("ttl_test", true);
        eventManager.publishEvent("ttl_event", data);

        // Wait for TTL to expire
        Thread.sleep(150);

        List<EventManager.Event> events = eventManager.pollEvents();

        // Event should be expired and cleaned up
        assertEquals(0, events.size());
    }

    @Test
    public void testEventListeners() {
        AtomicInteger eventCount = new AtomicInteger(0);

        EventManager.EventListener listener = event -> eventCount.incrementAndGet();

        eventManager.subscribe(listener);

        eventManager.publishEvent("test", new JsonObject());
        eventManager.publishEvent("test2", new JsonObject());

        assertEquals(2, eventCount.get());
    }

    @Test
    public void testTypeSpecificListeners() {
        AtomicInteger chatCount = new AtomicInteger(0);
        AtomicInteger moveCount = new AtomicInteger(0);

        EventManager.EventListener chatListener = event -> chatCount.incrementAndGet();
        EventManager.EventListener moveListener = event -> moveCount.incrementAndGet();

        eventManager.subscribe(EventManager.EventType.CHAT, chatListener);
        eventManager.subscribe(EventManager.EventType.ENTITY_MOVE, moveListener);

        eventManager.publishEvent(EventManager.EventType.CHAT, new JsonObject());
        eventManager.publishEvent(EventManager.EventType.ENTITY_MOVE, new JsonObject());
        eventManager.publishEvent(EventManager.EventType.TICK_UPDATE, new JsonObject()); // Should not trigger listeners

        assertEquals(1, chatCount.get());
        assertEquals(1, moveCount.get());
    }

    @Test
    public void testMultipleTypeSubscription() {
        AtomicInteger eventCount = new AtomicInteger(0);

        EventManager.EventListener listener = event -> eventCount.incrementAndGet();

        Set<EventManager.EventType> types = Set.of(EventManager.EventType.CHAT, EventManager.EventType.ENTITY_MOVE);
        eventManager.subscribe(types, listener);

        eventManager.publishEvent(EventManager.EventType.CHAT, new JsonObject());
        eventManager.publishEvent(EventManager.EventType.ENTITY_MOVE, new JsonObject());
        eventManager.publishEvent(EventManager.EventType.TICK_UPDATE, new JsonObject()); // Should not trigger

        assertEquals(2, eventCount.get());
    }

    @Test
    public void testUnsubscribe() {
        AtomicInteger eventCount = new AtomicInteger(0);

        EventManager.EventListener listener = event -> eventCount.incrementAndGet();

        eventManager.subscribe(listener);
        eventManager.publishEvent("test", new JsonObject());
        assertEquals(1, eventCount.get());

        eventManager.unsubscribe(listener);
        eventManager.publishEvent("test", new JsonObject());
        assertEquals(1, eventCount.get()); // Should not increment after unsubscribe
    }

    @Test
    public void testPeekEvents() {
        eventManager.publishEvent("test", new JsonObject());
        eventManager.publishEvent("test", new JsonObject());

        List<EventManager.Event> peekedEvents = eventManager.peekEvents();
        assertEquals(2, peekedEvents.size());

        // Peek should not remove events
        List<EventManager.Event> polledEvents = eventManager.pollEvents();
        assertEquals(2, polledEvents.size());
    }

    @Test
    public void testEventFiltering() {
        eventManager.publishEvent("type1", new JsonObject());
        eventManager.publishEvent("type2", new JsonObject());
        eventManager.publishEvent("type1", new JsonObject());

        List<EventManager.Event> type1Events = eventManager.pollEvents("type1");
        assertEquals(2, type1Events.size());

        List<EventManager.Event> remainingEvents = eventManager.pollEvents();
        assertEquals(1, remainingEvents.size());
        assertEquals("type2", remainingEvents.get(0).getType());
    }

    @Test
    public void testClearBuffer() {
        eventManager.publishEvent("test", new JsonObject());
        eventManager.publishEvent("test", new JsonObject());

        assertEquals(2, eventManager.getBufferSize());

        eventManager.clearBuffer();

        assertEquals(0, eventManager.getBufferSize());
    }

    @Test
    public void testConfiguration() {
        eventManager.setMaxBufferSize(50);
        eventManager.setTTL(10000);

        assertEquals(50, eventManager.getMaxBufferSize());
        assertEquals(10000, eventManager.getTTL());
    }

    @Test
    public void testInvalidConfiguration() {
        assertThrows(IllegalArgumentException.class, () -> eventManager.setMaxBufferSize(0));
        assertThrows(IllegalArgumentException.class, () -> eventManager.setTTL(-1));
    }

    @Test
    public void testStats() {
        eventManager.publishEvent("test", new JsonObject());
        eventManager.publishEvent("test", new JsonObject());

        JsonObject stats = eventManager.getStats();

        assertEquals(2, stats.get("buffer_size").getAsInt());
        assertTrue(stats.get("total_events_processed").getAsInt() >= 2);
        assertTrue(stats.has("type_listeners"));
    }

    @Test
    public void testEventTypeEnum() {
        JsonObject data = new JsonObject();
        data.addProperty("enum_test", true);

        eventManager.publishEvent(EventManager.EventType.CHAT, data);

        List<EventManager.Event> events = eventManager.pollEvents("chat");
        assertEquals(1, events.size());
        assertEquals("chat", events.get(0).getType());
    }
}