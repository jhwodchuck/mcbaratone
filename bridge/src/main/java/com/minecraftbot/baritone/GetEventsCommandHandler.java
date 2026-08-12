package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.List;
import net.minecraft.client.Minecraft;

/**
 * Handler for the get_events command - polls buffered events from EventManager.
 */
public class GetEventsCommandHandler extends AbstractCommandHandler {

    private EventManager eventManager;

    public GetEventsCommandHandler() {
        // Retained for reflective factory construction and compatibility.
    }

    public GetEventsCommandHandler(EventManager eventManager) {
        this.eventManager = eventManager;
    }

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
    protected CommandResult execute(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        if (eventManager == null) {
            return CommandResult.error("EventManager not initialized");
        }

        boolean cursorMode = params.has("after_seq");
        long afterSequence = cursorMode ? params.get("after_seq").getAsLong() : -1L;
        String type = params.has("type") ? params.get("type").getAsString() : null;
        int limit = params.has("limit") ? params.get("limit").getAsInt() : 250;
        List<EventManager.Event> polledEvents = cursorMode
            ? eventManager.getEventsAfter(afterSequence, type, limit)
            : (type == null ? eventManager.pollEvents() : eventManager.pollEvents(type));
        JsonArray events = new JsonArray();

        for (EventManager.Event event : polledEvents) {
            JsonObject eventJson = new JsonObject();
            eventJson.addProperty("event_seq", event.getSequence());
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
        data.addProperty("cursor_mode", cursorMode);
        data.addProperty("oldest_seq", eventManager.getOldestSequence());
        data.addProperty("latest_seq", eventManager.getLatestSequence());
        data.addProperty("dropped_before_seq", eventManager.getDroppedBeforeSequence());
        if (cursorMode) {
            data.addProperty("after_seq", afterSequence);
            data.addProperty(
                "gap_detected",
                afterSequence < eventManager.getDroppedBeforeSequence());
        }
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
