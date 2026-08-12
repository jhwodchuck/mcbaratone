package com.minecraftbot.baritone;

import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;

import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class GetEventsCommandHandlerTest {

    @Test
    void bridgeRegistersAnInitializedPollingHandler() throws Exception {
        new BaritoneAPIBridge();

        CommandHandler handler = CommandHandlerFactory.getHandler("get_events");
        CommandResult result = handler
            .handle(new JsonObject(), null, null, null)
            .get(1, TimeUnit.SECONDS);

        assertTrue(result.isSuccess());
        assertEquals(0, result.getData().get("count").getAsInt());
    }

    @Test
    void pollsEventsFromInjectedBridgeManager() throws Exception {
        EventManager eventManager = new EventManager();
        JsonObject damage = new JsonObject();
        damage.addProperty("amount", 3.5f);
        eventManager.publishEvent(
            EventManager.EventType.DAMAGE,
            damage,
            EventManager.Priority.HIGH
        );
        GetEventsCommandHandler handler = new GetEventsCommandHandler(eventManager);

        CommandResult result = handler
            .handle(new JsonObject(), null, null, null)
            .get(1, TimeUnit.SECONDS);

        assertTrue(result.isSuccess());
        assertEquals(1, result.getData().get("count").getAsInt());
        JsonObject event = result.getData().getAsJsonArray("events").get(0).getAsJsonObject();
        assertEquals("damage", event.get("type").getAsString());
        assertEquals("HIGH", event.get("priority").getAsString());
        assertEquals(3.5f, event.getAsJsonObject("data").get("amount").getAsFloat());
    }

    @Test
    void cursorPollsAreNonDestructiveAndReportOverflowGap() throws Exception {
        EventManager eventManager = new EventManager(2, 60_000);
        eventManager.publishEvent(EventManager.EventType.CHAT, new JsonObject());
        eventManager.publishEvent(EventManager.EventType.DAMAGE, new JsonObject());
        eventManager.publishEvent(EventManager.EventType.DEATH, new JsonObject());
        GetEventsCommandHandler handler = new GetEventsCommandHandler(eventManager);

        JsonObject params = new JsonObject();
        params.addProperty("after_seq", 0L);
        CommandResult first = handler.handle(params, null, null, null)
            .get(1, TimeUnit.SECONDS);
        CommandResult second = handler.handle(params, null, null, null)
            .get(1, TimeUnit.SECONDS);

        assertTrue(first.isSuccess());
        assertTrue(first.getData().get("cursor_mode").getAsBoolean());
        assertTrue(first.getData().get("gap_detected").getAsBoolean());
        assertEquals(1L, first.getData().get("dropped_before_seq").getAsLong());
        assertEquals(2L, first.getData().get("oldest_seq").getAsLong());
        assertEquals(3L, first.getData().get("latest_seq").getAsLong());
        assertEquals(2, first.getData().get("count").getAsInt());
        assertEquals(
            first.getData().getAsJsonArray("events"),
            second.getData().getAsJsonArray("events")
        );
        assertEquals(2, eventManager.getBufferSize());
    }

    @Test
    void cursorPollSupportsTypeAndLimitWithoutAdvancingOtherConsumers() throws Exception {
        EventManager eventManager = new EventManager(10, 60_000);
        eventManager.publishEvent(EventManager.EventType.CHAT, new JsonObject());
        eventManager.publishEvent(EventManager.EventType.DAMAGE, new JsonObject());
        eventManager.publishEvent(EventManager.EventType.CHAT, new JsonObject());
        GetEventsCommandHandler handler = new GetEventsCommandHandler(eventManager);

        JsonObject params = new JsonObject();
        params.addProperty("after_seq", 0L);
        params.addProperty("type", "chat");
        params.addProperty("limit", 1);
        CommandResult filtered = handler.handle(params, null, null, null)
            .get(1, TimeUnit.SECONDS);

        assertEquals(1, filtered.getData().get("count").getAsInt());
        JsonObject event = filtered.getData().getAsJsonArray("events")
            .get(0).getAsJsonObject();
        assertEquals(1L, event.get("event_seq").getAsLong());
        assertEquals("chat", event.get("type").getAsString());
        assertEquals(3, eventManager.getBufferSize());
    }
}
