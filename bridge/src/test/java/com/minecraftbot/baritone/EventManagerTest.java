package com.minecraftbot.baritone;

import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;

import java.util.EnumSet;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;

class EventManagerTest {

    @Test
    void subscriptionsAreIdempotentAndCanBeRemovedSelectively() {
        EventManager manager = new EventManager();
        AtomicInteger received = new AtomicInteger();
        EventManager.EventListener listener = event -> received.incrementAndGet();

        manager.subscribe(EventManager.EventType.CHAT, listener);
        manager.subscribe(EventManager.EventType.CHAT, listener);
        manager.subscribe(EventManager.EventType.MISSION, listener);

        manager.publishEvent(EventManager.EventType.CHAT, new JsonObject());
        assertEquals(1, received.get());

        manager.unsubscribe(EnumSet.of(EventManager.EventType.CHAT), listener);
        manager.publishEvent(EventManager.EventType.CHAT, new JsonObject());
        manager.publishEvent(EventManager.EventType.MISSION, new JsonObject());
        assertEquals(2, received.get());
    }

    @Test
    void globalSubscriptionsAreIdempotent() {
        EventManager manager = new EventManager();
        AtomicInteger received = new AtomicInteger();
        EventManager.EventListener listener = event -> received.incrementAndGet();

        manager.subscribe(listener);
        manager.subscribe(listener);
        manager.publishEvent(EventManager.EventType.BLOCK_BREAK, new JsonObject());

        assertEquals(1, received.get());
    }
}
