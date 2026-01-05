package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;

import java.net.Socket;
import java.util.List;

/**
 * Handler for the get_events command - polls buffered events from EventManager.
 */
public class GetEventsCommandHandler extends AbstractCommandHandler {

    private EventManager eventManager;

    @Override
    public String getCommandName() {
        return "get_events";
    }

    /**
     * Set the EventManager instance. This should be called during initialization.
     */
    public void setEventManager(EventManager eventManager) {
        this.eventManager = eventManager;
    }

    @Override
    protected CommandResult execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        if (eventManager == null) {
            return CommandResult.error("EventManager not initialized");
        }

        List<EventManager.Event> polledEvents = eventManager.pollEvents();
        JsonArray events = new JsonArray();

        for (EventManager.Event event : polledEvents) {
            JsonObject eventJson = new JsonObject();
            eventJson.addProperty("type", event.getType());
            eventJson.addProperty("timestamp", event.getTimestamp());
            eventJson.add("data", event.getData());
            eventJson.addProperty("priority", event.getPriority().name());
            eventJson.addProperty("source", event.getSource());
            events.add(eventJson);
        }

        JsonObject data = new JsonObject();
        data.add("events", events);
        data.addProperty("count", events.size());
        return CommandResult.success(data);
    }

    @Override
    public boolean requiresPlayer() {
        return false;
    }

    @Override
    public boolean isOfflineSafe() {
        return true;
    }
}
