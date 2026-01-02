package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;

import java.net.Socket;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

public class CommandDispatcherTest {

    @Mock
    private MissionController mockMissionController;

    @Mock
    private LegacyCommandHandler mockLegacyHandler;

    @Mock
    private MinecraftClient mockClient;

    @Mock
    private IBaritone mockBaritone;

    @Mock
    private Socket mockSocket;

    @Mock
    private CommandHandler mockCommandHandler;

    private CommandDispatcher dispatcher;

    @BeforeEach
    void setUp() {
        MockitoAnnotations.openMocks(this);
        dispatcher = new CommandDispatcher(mockMissionController, mockLegacyHandler);

        // Clear the factory registry for each test
        CommandHandlerFactory.clearRegistry();
    }

    @Test
    void testDispatchCommand_NullRequest_ShouldReturnError() {
        CommandResult result = dispatcher.dispatchCommand(null, mockSocket, mockClient, mockBaritone);

        assertNotNull(result);
        assertFalse(result.isSuccess());
        assertEquals("Missing command", result.getErrorMessage());
    }

    @Test
    void testDispatchCommand_RequestWithoutCommand_ShouldReturnError() {
        JsonObject request = new JsonObject();

        CommandResult result = dispatcher.dispatchCommand(request, mockSocket, mockClient, mockBaritone);

        assertNotNull(result);
        assertFalse(result.isSuccess());
        assertEquals("Missing command", result.getErrorMessage());
    }

    @Test
    void testDispatchCommand_MissionControllerHandlesCommand_ShouldReturnSuccess() {
        JsonObject request = new JsonObject();
        request.addProperty("command", "mission_status");
        request.add("params", new JsonObject());

        JsonObject missionData = new JsonObject();
        missionData.addProperty("phase", "idle");

        when(mockMissionController.tryHandle(eq("mission_status"), any(JsonObject.class), eq(missionData), eq(mockClient), eq(mockBaritone), eq(mockSocket)))
            .thenReturn(true);

        CommandResult result = dispatcher.dispatchCommand(request, mockSocket, mockClient, mockBaritone);

        assertNotNull(result);
        assertTrue(result.isSuccess());
        assertEquals(missionData, result.getData());
        verify(mockMissionController).tryHandle("mission_status", request.getAsJsonObject("params"), missionData, mockClient, mockBaritone, mockSocket);
        verifyNoInteractions(mockLegacyHandler);
    }

    @Test
    void testDispatchCommand_HandlerFactoryProvidesHandler_ShouldExecuteHandler() {
        JsonObject request = new JsonObject();
        request.addProperty("command", "test_command");
        JsonObject params = new JsonObject();
        request.add("params", params);

        CommandResult expectedResult = CommandResult.success(new JsonObject());
        when(mockCommandHandler.handle(params, mockClient, mockBaritone, mockSocket)).thenReturn(expectedResult);
        when(mockCommandHandler.getCommandName()).thenReturn("test_command");

        // Register handler in factory
        CommandHandlerFactory.registerHandler("test_command", TestCommandHandler.class);

        // Mock the factory to return our mock handler
        try {
            CommandHandlerFactory.class.getDeclaredField("handlerInstances").setAccessible(true);
            java.util.Map<String, CommandHandler> instances =
                (java.util.Map<String, CommandHandler>) CommandHandlerFactory.class.getDeclaredField("handlerInstances").get(null);
            instances.put("test_command", mockCommandHandler);
        } catch (Exception e) {
            fail("Failed to setup mock handler");
        }

        CommandResult result = dispatcher.dispatchCommand(request, mockSocket, mockClient, mockBaritone);

        assertNotNull(result);
        assertEquals(expectedResult, result);
        verify(mockCommandHandler).handle(params, mockClient, mockBaritone, mockSocket);
        verify(mockMissionController).tryHandle(eq("test_command"), any(JsonObject.class), any(JsonObject.class), eq(mockClient), eq(mockBaritone), eq(mockSocket));
        verifyNoInteractions(mockLegacyHandler);
    }

    @Test
    void testDispatchCommand_NoHandlerFound_ShouldFallbackToLegacy() {
        JsonObject request = new JsonObject();
        request.addProperty("command", "unknown_command");
        request.add("params", new JsonObject());

        CommandResult legacyResult = CommandResult.success(new JsonObject());
        when(mockLegacyHandler.handleLegacyCommand("unknown_command", request.getAsJsonObject("params"), mockClient, mockBaritone, mockSocket))
            .thenReturn(legacyResult);

        CommandResult result = dispatcher.dispatchCommand(request, mockSocket, mockClient, mockBaritone);

        assertNotNull(result);
        assertEquals(legacyResult, result);
        verify(mockLegacyHandler).handleLegacyCommand("unknown_command", request.getAsJsonObject("params"), mockClient, mockBaritone, mockSocket);
    }

    @Test
    void testDispatchCommand_NoLegacyHandler_ShouldReturnError() {
        // Create dispatcher without legacy handler
        CommandDispatcher dispatcherNoLegacy = new CommandDispatcher(mockMissionController, null);

        JsonObject request = new JsonObject();
        request.addProperty("command", "unknown_command");
        request.add("params", new JsonObject());

        CommandResult result = dispatcherNoLegacy.dispatchCommand(request, mockSocket, mockClient, mockBaritone);

        assertNotNull(result);
        assertFalse(result.isSuccess());
        assertEquals("Unknown command: unknown_command (no legacy handler configured)", result.getErrorMessage());
    }

    @Test
    void testDispatchCommand_ExceptionDuringDispatch_ShouldReturnError() {
        JsonObject request = new JsonObject();
        request.addProperty("command", "mission_status");

        when(mockMissionController.tryHandle(any(), any(), any(), any(), any(), any()))
            .thenThrow(new RuntimeException("Test exception"));

        CommandResult result = dispatcher.dispatchCommand(request, mockSocket, mockClient, mockBaritone);

        assertNotNull(result);
        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("Command execution failed"));
        assertTrue(result.getErrorMessage().contains("Test exception"));
    }

    @Test
    void testCheckRateLimit_NullSocket_ShouldAllow() {
        // Null socket should be allowed for testing
        CommandResult result = dispatcher.dispatchCommand(createValidRequest("test"), null, mockClient, mockBaritone);

        // Should proceed to fallback since no handler found
        verify(mockLegacyHandler).handleLegacyCommand(any(), any(), any(), any(), isNull());
    }

    @Test
    void testCheckRateLimit_WithinLimit_ShouldAllow() {
        // First request should be allowed
        CommandResult result1 = dispatcher.dispatchCommand(createValidRequest("test"), mockSocket, mockClient, mockBaritone);
        // Should proceed to legacy handler

        // Second request within window should still be allowed (under 100)
        CommandResult result2 = dispatcher.dispatchCommand(createValidRequest("test"), mockSocket, mockClient, mockBaritone);

        verify(mockLegacyHandler, times(2)).handleLegacyCommand(any(), any(), any(), any(), eq(mockSocket));
    }

    @Test
    void testCheckRateLimit_ExceedsLimit_ShouldBlock() {
        // Simulate exceeding the rate limit by directly calling checkRateLimit
        // This is tricky to test without exposing internal methods, so we'll test through dispatch

        // Create a custom dispatcher to manipulate rate limiting
        CommandDispatcher testDispatcher = new CommandDispatcher(mockMissionController, mockLegacyHandler);

        // Make many requests quickly
        for (int i = 0; i < 101; i++) {
            testDispatcher.dispatchCommand(createValidRequest("test"), mockSocket, mockClient, mockBaritone);
        }

        // The 101st request should be rate limited
        CommandResult result = testDispatcher.dispatchCommand(createValidRequest("test"), mockSocket, mockClient, mockBaritone);

        assertNotNull(result);
        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("Rate limit exceeded"));
        assertTrue(result.getErrorMessage().contains("100 requests per 10 seconds"));
    }

    @Test
    void testCheckRateLimit_AfterWindowExpires_ShouldReset() throws InterruptedException {
        // Make requests up to the limit
        for (int i = 0; i < 100; i++) {
            dispatcher.dispatchCommand(createValidRequest("test"), mockSocket, mockClient, mockBaritone);
        }

        // Wait for the rate limit window to expire
        Thread.sleep(10001); // Wait 10+ seconds

        // Next request should be allowed
        CommandResult result = dispatcher.dispatchCommand(createValidRequest("test"), mockSocket, mockClient, mockBaritone);

        // Should proceed to legacy handler, not be rate limited
        verify(mockLegacyHandler, atLeast(101)).handleLegacyCommand(any(), any(), any(), any(), eq(mockSocket));
    }

    @Test
    void testExecuteWithTimeout_HandlerThrowsException_ShouldReturnError() {
        JsonObject request = new JsonObject();
        request.addProperty("command", "test_command");
        JsonObject params = new JsonObject();
        request.add("params", params);

        when(mockCommandHandler.handle(params, mockClient, mockBaritone, mockSocket))
            .thenThrow(new RuntimeException("Handler exception"));

        // Register and setup mock handler
        CommandHandlerFactory.registerHandler("test_command", TestCommandHandler.class);
        try {
            java.util.Map<String, CommandHandler> instances =
                (java.util.Map<String, CommandHandler>) CommandHandlerFactory.class.getDeclaredField("handlerInstances").get(null);
            instances.put("test_command", mockCommandHandler);
        } catch (Exception e) {
            fail("Failed to setup mock handler");
        }

        CommandResult result = dispatcher.dispatchCommand(request, mockSocket, mockClient, mockBaritone);

        assertNotNull(result);
        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("Command execution failed"));
    }

    @Test
    void testClearRateLimit_ShouldResetCounts() {
        // Make some requests to build up counts
        for (int i = 0; i < 10; i++) {
            dispatcher.dispatchCommand(createValidRequest("test"), mockSocket, mockClient, mockBaritone);
        }

        // Clear rate limit
        dispatcher.clearRateLimit(mockSocket);

        // Next request should reset the window
        CommandResult result = dispatcher.dispatchCommand(createValidRequest("test"), mockSocket, mockClient, mockBaritone);

        // Should proceed normally
        verify(mockLegacyHandler, times(11)).handleLegacyCommand(any(), any(), any(), any(), eq(mockSocket));
    }

    @Test
    void testFallbackOrder_MissionControllerFirst() {
        JsonObject request = createValidRequest("mission_status");

        // Mission controller returns false (doesn't handle)
        when(mockMissionController.tryHandle(any(), any(), any(), any(), any(), any())).thenReturn(false);

        // Handler factory has no handler
        // Should fall back to legacy

        CommandResult result = dispatcher.dispatchCommand(request, mockSocket, mockClient, mockBaritone);

        verify(mockMissionController).tryHandle(eq("mission_status"), any(), any(), eq(mockClient), eq(mockBaritone), eq(mockSocket));
        verify(mockLegacyHandler).handleLegacyCommand(eq("mission_status"), any(), eq(mockClient), eq(mockBaritone), eq(mockSocket));
    }

    @Test
    void testFallbackOrder_HandlerFactoryBeforeLegacy() {
        JsonObject request = createValidRequest("registered_command");

        // Mission controller doesn't handle
        when(mockMissionController.tryHandle(any(), any(), any(), any(), any(), any())).thenReturn(false);

        // Register a handler
        CommandHandlerFactory.registerHandler("registered_command", TestCommandHandler.class);
        CommandResult handlerResult = CommandResult.success(new JsonObject());
        try {
            java.util.Map<String, CommandHandler> instances =
                (java.util.Map<String, CommandHandler>) CommandHandlerFactory.class.getDeclaredField("handlerInstances").get(null);
            instances.put("registered_command", mockCommandHandler);
        } catch (Exception e) {
            fail("Failed to setup mock handler");
        }
        when(mockCommandHandler.handle(any(), eq(mockClient), eq(mockBaritone), eq(mockSocket))).thenReturn(handlerResult);

        CommandResult result = dispatcher.dispatchCommand(request, mockSocket, mockClient, mockBaritone);

        verify(mockMissionController).tryHandle(any(), any(), any(), any(), any(), any());
        verify(mockCommandHandler).handle(any(), eq(mockClient), eq(mockBaritone), eq(mockSocket));
        verifyNoInteractions(mockLegacyHandler);
    }

    private JsonObject createValidRequest(String command) {
        JsonObject request = new JsonObject();
        request.addProperty("command", command);
        request.add("params", new JsonObject());
        return request;
    }

    // Test implementation of CommandHandler for registry testing
    private static class TestCommandHandler implements CommandHandler {
        @Override
        public CommandResult handle(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
            return CommandResult.success(new JsonObject());
        }

        @Override
        public String getCommandName() {
            return "test";
        }
    }
}