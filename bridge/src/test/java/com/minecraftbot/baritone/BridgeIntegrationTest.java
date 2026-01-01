package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;

import java.net.Socket;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/**
 * Integration tests to verify backward compatibility and overall bridge functionality
 */
public class BridgeIntegrationTest {

    @Mock
    private MissionController missionController;
    @Mock
    private LegacyCommandHandler legacyHandler;
    @Mock
    private MinecraftClient minecraftClient;
    @Mock
    private IBaritone baritone;
    @Mock
    private Socket clientSocket;

    private CommandDispatcher dispatcher;

    @BeforeEach
    public void setUp() {
        MockitoAnnotations.openMocks(this);
        dispatcher = new CommandDispatcher(missionController, legacyHandler);
    }

    @Test
    public void testCommandDispatchingPipeline() {
        // Test the full command dispatching pipeline

        JsonObject gotoRequest = new JsonObject();
        gotoRequest.addProperty("id", "123");
        gotoRequest.addProperty("command", "goto");
        JsonObject gotoParams = new JsonObject();
        gotoParams.addProperty("x", 100);
        gotoParams.addProperty("y", 64);
        gotoParams.addProperty("z", 200);
        gotoRequest.add("params", gotoParams);

        JsonObject mineRequest = new JsonObject();
        mineRequest.addProperty("id", "124");
        mineRequest.addProperty("command", "mine");
        JsonObject mineParams = new JsonObject();
        mineParams.addProperty("block_type", "minecraft:stone");
        mineParams.addProperty("count", 10);
        mineRequest.add("params", mineParams);

        JsonObject stateRequest = new JsonObject();
        stateRequest.addProperty("id", "125");
        stateRequest.addProperty("command", "get_state");

        // Mock mission controller to not handle these commands
        when(missionController.tryHandle(any(), any(), any(), any(), any(), any())).thenReturn(false);

        // Test that these commands are handled by their respective handlers
        CommandResult gotoResult = dispatcher.dispatchCommand(gotoRequest, clientSocket, minecraftClient, baritone);
        CommandResult mineResult = dispatcher.dispatchCommand(mineRequest, clientSocket, minecraftClient, baritone);
        CommandResult stateResult = dispatcher.dispatchCommand(stateRequest, clientSocket, minecraftClient, baritone);

        // Verify that commands are processed (may succeed or fail based on mocks, but not unhandled)
        assertNotNull(gotoResult);
        assertNotNull(mineResult);
        assertNotNull(stateResult);

        // Verify mission controller was checked for each
        verify(missionController, times(3)).tryHandle(any(), any(), any(), any(), any(), any());
    }

    @Test
    public void testEventManagerIntegration() {
        EventManager eventManager = new EventManager();

        // Test event publishing and polling integration
        JsonObject eventData = new JsonObject();
        eventData.addProperty("test", "integration");

        eventManager.publishEvent("integration_test", eventData);

        // Test immediate polling
        var events = eventManager.pollEvents("integration_test");
        assertEquals(1, events.size());
        assertEquals("integration_test", events.get(0).getType());

        // Test event listener integration
        final boolean[] listenerCalled = {false};
        EventManager.EventListener listener = event -> {
            if ("listener_test".equals(event.getType())) {
                listenerCalled[0] = true;
            }
        };

        eventManager.subscribe(listener);
        eventManager.publishEvent("listener_test", new JsonObject());

        assertTrue(listenerCalled[0]);
    }

    @Test
    public void testCommandHandlerFactoryIntegration() {
        // Test that factory properly registers and retrieves handlers
        CommandHandlerFactory.clearRegistry();

        // Re-register core handlers
        CommandHandlerFactory.registerHandler("goto", GotoCommandHandler.class);
        CommandHandlerFactory.registerHandler("mine", MineCommandHandler.class);
        CommandHandlerFactory.registerHandler("get_inventory", InventoryCommandHandler.class);
        CommandHandlerFactory.registerHandler("get_state", StateCommandHandler.class);
        CommandHandlerFactory.registerHandler("build", BuildCommandHandler.class);

        // Test that all expected handlers are available
        assertTrue(CommandHandlerFactory.hasHandler("goto"));
        assertTrue(CommandHandlerFactory.hasHandler("mine"));
        assertTrue(CommandHandlerFactory.hasHandler("get_inventory"));
        assertTrue(CommandHandlerFactory.hasHandler("get_state"));
        assertTrue(CommandHandlerFactory.hasHandler("build"));

        // Test that instances can be created
        assertNotNull(CommandHandlerFactory.getHandler("goto"));
        assertNotNull(CommandHandlerFactory.getHandler("mine"));

        // Test singleton behavior
        CommandHandler goto1 = CommandHandlerFactory.getHandler("goto");
        CommandHandler goto2 = CommandHandlerFactory.getHandler("goto");
        assertSame(goto1, goto2);
    }

    @Test
    public void testLegacyFallbackIntegration() {
        JsonObject request = new JsonObject();
        request.addProperty("command", "legacy_command");
        request.addProperty("id", "456");

        JsonObject legacyResponse = new JsonObject();
        legacyResponse.addProperty("legacy_handled", true);

        when(missionController.tryHandle(any(), any(), any(), any(), any(), any())).thenReturn(false);
        when(legacyHandler.handleLegacyCommand(eq("legacy_command"), any(), any(), any(), any()))
            .thenReturn(CommandResult.success(legacyResponse));

        CommandResult result = dispatcher.dispatchCommand(request, clientSocket, minecraftClient, baritone);

        assertTrue(result.isSuccess());
        assertEquals(legacyResponse, result.getData());
        verify(legacyHandler).handleLegacyCommand(eq("legacy_command"), any(), any(), any(), any());
    }

    @Test
    public void testRateLimitingIntegration() {
        JsonObject request = new JsonObject();
        request.addProperty("command", "goto");

        // Create a new socket for rate limiting test
        Socket testSocket = mock(Socket.class);

        // Send many requests quickly
        for (int i = 0; i < 110; i++) {
            dispatcher.dispatchCommand(request, testSocket, minecraftClient, baritone);
        }

        // Next request should be rate limited
        CommandResult result = dispatcher.dispatchCommand(request, testSocket, minecraftClient, baritone);

        assertFalse(result.isSuccess());
        assertTrue(result.getError().contains("Rate limit exceeded"));

        // Clear rate limit and verify it works again
        dispatcher.clearRateLimit(testSocket);
        result = dispatcher.dispatchCommand(request, testSocket, minecraftClient, baritone);
        assertFalse(result.getError() != null && result.getError().contains("Rate limit exceeded"));
    }

    @Test
    public void testErrorHandlingIntegration() {
        JsonObject request = new JsonObject();
        request.addProperty("command", "goto");

        // Test exception in mission controller
        when(missionController.tryHandle(any(), any(), any(), any(), any(), any()))
            .thenThrow(new RuntimeException("Mission controller error"));

        CommandResult result = dispatcher.dispatchCommand(request, clientSocket, minecraftClient, baritone);

        assertFalse(result.isSuccess());
        assertTrue(result.getError().contains("Command execution failed"));
    }

    @Test
    public void testEventBufferingAndCleanup() throws InterruptedException {
        EventManager eventManager = new EventManager(5, 100); // Small buffer, short TTL

        // Fill buffer
        for (int i = 0; i < 7; i++) {
            eventManager.publishEvent("buffer_test", new JsonObject());
        }

        // Buffer should be trimmed to max size
        assertEquals(5, eventManager.getBufferSize());

        // Wait for TTL to expire
        Thread.sleep(150);

        // Poll events, which should trigger cleanup
        var events = eventManager.pollEvents();
        assertEquals(0, events.size()); // All events should be expired
    }

    @Test
    public void testConcurrentEventHandling() {
        EventManager eventManager = new EventManager();

        // Test that multiple threads can publish events concurrently
        Runnable publisher = () -> {
            for (int i = 0; i < 10; i++) {
                eventManager.publishEvent("concurrent_test", new JsonObject());
            }
        };

        Thread t1 = new Thread(publisher);
        Thread t2 = new Thread(publisher);

        t1.start();
        t2.start();

        try {
            t1.join();
            t2.join();
        } catch (InterruptedException e) {
            fail("Test interrupted");
        }

        // Should have processed all events
        assertTrue(eventManager.getStats().get("total_events_processed").getAsInt() >= 20);
    }
}