package com.minecraftbot.baritone;

import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Simplified test suite focusing on the refactored bridge architecture.
 * Tests the core patterns without complex Minecraft API mocking.
 */
public class RefactoredBridgeTestSuite {

    @Test
    public void testCommandDispatcherArchitecture() {
        // Test that CommandDispatcher exists and has the expected interface
        CommandDispatcher dispatcher = new CommandDispatcher(null, null);
        assertNotNull(dispatcher);

        // Test missing command handling
        JsonObject request = new JsonObject();
        // Note: We can't easily mock MinecraftClient/IBaritone for full dispatch testing
        // but we can verify the architecture exists
    }

    @Test
    public void testEventManagerArchitecture() {
        EventManager eventManager = new EventManager();

        // Test basic event publishing and polling
        JsonObject eventData = new JsonObject();
        eventData.addProperty("test", "value");

        eventManager.publishEvent("test_event", eventData);

        var events = eventManager.pollEvents("test_event");
        assertEquals(1, events.size());
        assertEquals("test_event", events.get(0).getType());
        assertEquals("value", events.get(0).getData().get("test").getAsString());
    }

    @Test
    public void testEventManagerBuffering() {
        EventManager eventManager = new EventManager(3, 5000); // Small buffer

        // Fill buffer
        for (int i = 0; i < 5; i++) {
            eventManager.publishEvent("buffer_test", new JsonObject());
        }

        var events = eventManager.pollEvents();
        assertEquals(3, events.size()); // Should be limited by buffer size
    }

    @Test
    public void testEventManagerTTL() throws InterruptedException {
        EventManager eventManager = new EventManager(10, 100); // Short TTL

        eventManager.publishEvent("ttl_test", new JsonObject());

        // Wait for TTL to expire
        Thread.sleep(150);

        var events = eventManager.pollEvents();
        assertEquals(0, events.size()); // Event should be expired
    }

    @Test
    public void testCommandHandlerFactory() {
        // Clear any existing registrations
        CommandHandlerFactory.clearRegistry();

        // Test basic registration and lookup
        CommandHandlerFactory.registerHandler("test", TestCommandHandler.class);
        assertTrue(CommandHandlerFactory.hasHandler("test"));

        CommandHandler handler = CommandHandlerFactory.getHandler("test");
        assertNotNull(handler);
        assertEquals("test", handler.getCommandName());

        // Test singleton behavior
        CommandHandler handler2 = CommandHandlerFactory.getHandler("test");
        assertSame(handler, handler2);

        // Test unknown handler
        assertNull(CommandHandlerFactory.getHandler("unknown"));
    }

    @Test
    public void testCommandResult() {
        // Test success result
        JsonObject data = new JsonObject();
        data.addProperty("key", "value");
        CommandResult successResult = CommandResult.success(data);

        assertTrue(successResult.isSuccess());
        assertEquals("value", successResult.getData().get("key").getAsString());
        assertNull(successResult.getErrorMessage());

        // Test error result
        CommandResult errorResult = CommandResult.error("Test error");
        assertFalse(errorResult.isSuccess());
        assertEquals("Test error", errorResult.getErrorMessage());
    }

    @Test
    public void testEventTypes() {
        // Test that EventType enum has expected values
        assertNotNull(EventManager.EventType.CHAT);
        assertNotNull(EventManager.EventType.MISSION);
        assertNotNull(EventManager.EventType.PATHFINDING_STATE);
    }

    @Test
    public void testEventPriorities() {
        assertEquals(0, EventManager.Priority.LOW.getValue());
        assertEquals(1, EventManager.Priority.NORMAL.getValue());
        assertEquals(2, EventManager.Priority.HIGH.getValue());
        assertEquals(3, EventManager.Priority.CRITICAL.getValue());
    }

    // Simple test command handler
    public static class TestCommandHandler extends AbstractCommandHandler {
        @Override
        public String getCommandName() {
            return "test";
        }

        @Override
        protected CommandResult execute(JsonObject params, net.minecraft.client.MinecraftClient client,
                                      baritone.api.IBaritone baritone, java.net.Socket clientSocket) {
            return CommandResult.success(new JsonObject());
        }
    }
}