package com.minecraftbot.baritone;

import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;

import java.util.EnumSet;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

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

    @Test
    void sequenceCursorReadsAreIndependentAndNonDestructive() {
        EventManager manager = new EventManager(10, 60_000);
        manager.publishEvent(EventManager.EventType.CHAT, new JsonObject());
        manager.publishEvent(EventManager.EventType.DAMAGE, new JsonObject());

        List<EventManager.Event> firstConsumer =
            manager.getEventsAfter(0, null, 10);
        List<EventManager.Event> secondConsumer =
            manager.getEventsAfter(0, null, 10);

        assertEquals(2, firstConsumer.size());
        assertEquals(2, secondConsumer.size());
        assertEquals(1L, firstConsumer.get(0).getSequence());
        assertEquals(2L, firstConsumer.get(1).getSequence());
        assertEquals(
            firstConsumer.stream().map(EventManager.Event::getSequence).toList(),
            secondConsumer.stream().map(EventManager.Event::getSequence).toList()
        );
        assertEquals(2, manager.getBufferSize());
    }

    @Test
    void boundedBufferReportsSequenceRangeLostToOverflow() {
        EventManager manager = new EventManager(2, 60_000);
        manager.publishEvent(EventManager.EventType.CHAT, new JsonObject());
        manager.publishEvent(EventManager.EventType.DAMAGE, new JsonObject());
        manager.publishEvent(EventManager.EventType.DEATH, new JsonObject());

        assertEquals(1L, manager.getDroppedBeforeSequence());
        assertEquals(2L, manager.getOldestSequence());
        assertEquals(3L, manager.getLatestSequence());
        assertEquals(2, manager.getEventsAfter(0, null, 10).size());
    }

    @Test
    void concurrentPublishersAppendInStrictSequenceOrder() throws Exception {
        int publishers = 8;
        int eventsPerPublisher = 100;
        EventManager manager = new EventManager(1000, 60_000);
        ExecutorService pool = Executors.newFixedThreadPool(publishers);
        CountDownLatch start = new CountDownLatch(1);

        try {
            for (int publisher = 0; publisher < publishers; publisher++) {
                pool.submit(() -> {
                    start.await();
                    for (int event = 0; event < eventsPerPublisher; event++) {
                        manager.publishEvent(EventManager.EventType.CHAT, new JsonObject());
                    }
                    return null;
                });
            }
            start.countDown();
            pool.shutdown();
            assertTrue(pool.awaitTermination(10, TimeUnit.SECONDS));
        } finally {
            pool.shutdownNow();
        }

        List<EventManager.Event> events = manager.getEventsAfter(0, null, 1000);
        assertEquals(publishers * eventsPerPublisher, events.size());
        for (int index = 0; index < events.size(); index++) {
            assertEquals(index + 1L, events.get(index).getSequence());
        }
    }

    @Test
    void legacyDrainMarksSequencesUnavailableToCursorConsumers() {
        EventManager manager = new EventManager(10, 60_000);
        manager.publishEvent(EventManager.EventType.CHAT, new JsonObject());
        manager.publishEvent(EventManager.EventType.DAMAGE, new JsonObject());

        assertEquals(2, manager.pollEvents().size());
        assertEquals(2L, manager.getDroppedBeforeSequence());
        assertTrue(manager.getEventsAfter(0, null, 10).isEmpty());
    }
}
