package com.minecraftbot.baritone.mcp;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.minecraftbot.baritone.CommandDispatcher;
import com.minecraftbot.baritone.EventManager;
import org.java_websocket.WebSocket;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.reset;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class McpEventToolsTest {

    private EventManager eventManager;
    private McpToolRegistry registry;
    private WebSocket connection;
    private McpSession session;

    @BeforeEach
    void setUp() {
        eventManager = new EventManager();
        registry = new McpToolRegistry(mock(CommandDispatcher.class), eventManager);
        connection = mock(WebSocket.class);
        when(connection.isOpen()).thenReturn(true);
        session = new McpSession(connection, null);
    }

    @Test
    void genericSubscriptionStreamsOnlySelectedEventsAtTheThreshold() {
        JsonObject args = new JsonObject();
        JsonArray types = new JsonArray();
        types.add("chat");
        args.add("event_types", types);
        args.addProperty("priority_threshold", 2);

        JsonObject result = registry.invokeTool("subscribe_events", args, session);
        assertTrue(result.get("success").getAsBoolean());
        assertEquals(2, result.get("priority_threshold").getAsInt());

        eventManager.publishEvent(
            EventManager.EventType.CHAT, new JsonObject(), EventManager.Priority.NORMAL);
        verify(connection, never()).send(contains("notifications/baritone/event"));

        eventManager.publishEvent(
            EventManager.EventType.CHAT, new JsonObject(), EventManager.Priority.HIGH);
        verify(connection).send(contains("\"type\":\"chat\""));

        reset(connection);
        when(connection.isOpen()).thenReturn(true);
        registry.invokeTool("unsubscribe_events", args, session);
        eventManager.publishEvent(
            EventManager.EventType.CHAT, new JsonObject(), EventManager.Priority.CRITICAL);
        verify(connection, never()).send(contains("notifications/baritone/event"));
    }

    @Test
    void missionBroadcastUsesTheSharedEventManager() {
        registry.invokeTool("subscribe_mission_updates", new JsonObject(), session);

        JsonObject missionData = new JsonObject();
        missionData.addProperty("phase", "FOOD_AND_IRON");
        JsonObject args = new JsonObject();
        args.add("mission_data", missionData);

        JsonObject result = registry.invokeTool("broadcast_mission_state", args, session);
        assertTrue(result.get("published").getAsBoolean());
        assertFalse(eventManager.peekEvents("mission").isEmpty());
        verify(connection).send(contains("FOOD_AND_IRON"));
    }

    @Test
    void invalidEventNamesAreRejected() {
        JsonObject args = new JsonObject();
        JsonArray types = new JsonArray();
        types.add("not_a_real_event");
        args.add("event_types", types);

        McpException error = assertThrows(
            McpException.class,
            () -> registry.invokeTool("subscribe_events", args, session));
        assertEquals(McpErrorCodes.INVALID_PARAMS, error.getCode());
    }
}
