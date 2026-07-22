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
}
