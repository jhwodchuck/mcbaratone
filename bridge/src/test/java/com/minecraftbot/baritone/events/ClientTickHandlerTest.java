package com.minecraftbot.baritone.events;

import com.minecraftbot.baritone.BaritoneAPIBridge;
import com.minecraftbot.baritone.EventManager;
import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.Mockito.mock;

class ClientTickHandlerTest {

    @Test
    void healthLossPublishesHighPriorityDamageEvent() {
        BaritoneAPIBridge.IPlayerContext context = mock(BaritoneAPIBridge.IPlayerContext.class);
        EventManager events = new EventManager();
        ClientTickHandler handler = new ClientTickHandler(context, events);
        JsonObject metadata = new JsonObject();
        metadata.addProperty("x", 3.0);
        metadata.addProperty("y", 64.0);
        metadata.addProperty("z", -2.0);
        metadata.addProperty("dimension", "minecraft:overworld");

        handler.observeHealth(20.0f, metadata.deepCopy());
        handler.observeHealth(15.5f, metadata.deepCopy());

        List<EventManager.Event> damage = events.pollEvents("damage");
        assertEquals(1, damage.size());
        assertEquals(EventManager.Priority.HIGH, damage.get(0).getPriority());
        assertEquals(4.5f, damage.get(0).getData().get("amount").getAsFloat());
        assertEquals(15.5f, damage.get(0).getData().get("health").getAsFloat());
        assertEquals("minecraft:overworld", damage.get(0).getData().get("dimension").getAsString());
    }
}
