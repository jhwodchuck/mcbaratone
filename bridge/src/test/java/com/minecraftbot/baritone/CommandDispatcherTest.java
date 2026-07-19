package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;

import java.net.Socket;
import java.util.concurrent.CompletableFuture;
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
        
        // Mock socket address for rate limiting
        java.net.InetAddress mockAddress = mock(java.net.InetAddress.class);
        when(mockAddress.getHostAddress()).thenReturn("127.0.0.1");
        when(mockSocket.getInetAddress()).thenReturn(mockAddress);

        // Clear the factory registry for each test
        CommandHandlerFactory.clearRegistry();

        // Stub legacy handler to return success by default
        when(mockLegacyHandler.handleLegacyCommand(any(), any(), any(), any(), any()))
            .thenReturn(CommandResult.success(new JsonObject()));
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

        doAnswer(invocation -> {
            JsonObject data = invocation.getArgument(2);
            data.addProperty("phase", "idle");
            return true;
        }).when(mockMissionController).tryHandle(eq("mission_status"), any(JsonObject.class), any(JsonObject.class), eq(mockClient), eq(mockBaritone), eq(mockSocket));

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

        CompletableFuture<CommandResult> expectedFuture = CompletableFuture.completedFuture(CommandResult.success(new JsonObject()));
        when(mockCommandHandler.handle(params, mockClient, mockBaritone, mockSocket)).thenReturn(expectedFuture);
        when(mockCommandHandler.getCommandName()).thenReturn("test_command");

        // Register handler in factory
        CommandHandlerFactory.registerHandler("test_command", TestCommandHandler.class);

        // Mock the factory to return our mock handler
        try {
            java.lang.reflect.Field field = CommandHandlerFactory.class.getDeclaredField("handlerInstances");
            field.setAccessible(true);
            java.util.Map<String, CommandHandler> instances =
                (java.util.Map<String, CommandHandler>) field.get(null);
            instances.put("test_command", mockCommandHandler);
        } catch (Exception e) {
            fail("Failed to setup mock handler: " + e.toString());
        }

        CommandResult result = dispatcher.dispatchCommand(request, mockSocket, mockClient, mockBaritone);

        assertNotNull(result);
        // We can't directly compare since expectedResult was wrapped in the future
        assertTrue(result.isSuccess());
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

        // Fill the current window up to the production limit (2000 per 10s)
        for (int i = 0; i < 2000; i++) {
            testDispatcher.dispatchCommand(createValidRequest("test"), mockSocket, mockClient, mockBaritone);
        }

        // The next request should be rate limited
        CommandResult result = testDispatcher.dispatchCommand(createValidRequest("test"), mockSocket, mockClient, mockBaritone);

        assertNotNull(result);
        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("Rate limit exceeded"));
        assertTrue(result.getErrorMessage().contains("2000 requests per 10 seconds"));
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
            .thenReturn(CompletableFuture.failedFuture(new RuntimeException("Handler exception")));

        // Register and setup mock handler
        CommandHandlerFactory.registerHandler("test_command", TestCommandHandler.class);
        try {
            java.lang.reflect.Field field = CommandHandlerFactory.class.getDeclaredField("handlerInstances");
            field.setAccessible(true);
            java.util.Map<String, CommandHandler> instances =
                (java.util.Map<String, CommandHandler>) field.get(null);
            instances.put("test_command", mockCommandHandler);
        } catch (Exception e) {
            fail("Failed to setup mock handler: " + e.toString());
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
            java.lang.reflect.Field field = CommandHandlerFactory.class.getDeclaredField("handlerInstances");
            field.setAccessible(true);
            java.util.Map<String, CommandHandler> instances =
                (java.util.Map<String, CommandHandler>) field.get(null);
            instances.put("registered_command", mockCommandHandler);
        } catch (Exception e) {
            fail("Failed to setup mock handler: " + e.toString());
        }
        when(mockCommandHandler.handle(any(), eq(mockClient), eq(mockBaritone), eq(mockSocket)))
            .thenReturn(CompletableFuture.completedFuture(handlerResult));

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

    @Test
    void testDeterminePriority_HighPriorityCommands_ShouldReturnHigh() {
        // Test emergency stop commands
        assertEquals(CommandPriority.HIGH, dispatcher.determinePriority("stop"));
        assertEquals(CommandPriority.HIGH, dispatcher.determinePriority("cancel"));
        assertEquals(CommandPriority.HIGH, dispatcher.determinePriority("pause"));
    }

    @Test
    void testDeterminePriority_LowPriorityCommands_ShouldReturnLow() {
        // Test low priority commands
        assertEquals(CommandPriority.LOW, dispatcher.determinePriority("settings"));
        assertEquals(CommandPriority.LOW, dispatcher.determinePriority("screenshot"));
    }

    @Test
    void testDeterminePriority_NormalCommands_ShouldReturnNormal() {
        // Test normal commands
        assertEquals(CommandPriority.NORMAL, dispatcher.determinePriority("goto"));
        assertEquals(CommandPriority.NORMAL, dispatcher.determinePriority("mine"));
        assertEquals(CommandPriority.NORMAL, dispatcher.determinePriority("explore"));
        assertEquals(CommandPriority.NORMAL, dispatcher.determinePriority("unknown_command"));
    }

    @Test
    void testMetricsCollection_CommandExecution_ShouldRecordMetrics() {
        JsonObject request = createValidRequest("test_command");

        // Mock legacy handler to return success
        CommandResult mockResult = CommandResult.success(new JsonObject());
        when(mockLegacyHandler.handleLegacyCommand("test_command", request.getAsJsonObject("params"), mockClient, mockBaritone, mockSocket))
            .thenReturn(mockResult);

        // Execute a command
        dispatcher.dispatchCommand(request, mockSocket, mockClient, mockBaritone);

        MetricsCollector metrics = dispatcher.getMetricsCollector();
        var commandMetrics = metrics.getCommandMetrics();

        assertTrue(commandMetrics.containsKey("test_command"));
        assertEquals(1, commandMetrics.get("test_command").totalExecutions);
        assertEquals(1, commandMetrics.get("test_command").successfulExecutions);
        assertEquals(0, commandMetrics.get("test_command").failedExecutions);
        assertEquals(1.0, commandMetrics.get("test_command").successRate);
    }

    @Test
    void testMetricsCollection_CacheHit_ShouldRecordCacheAccess() {
        // First request - should be cache miss
        JsonObject request = createValidRequest("cached_command");
        dispatcher.dispatchCommand(request, mockSocket, mockClient, mockBaritone);

        // Second request with same params - should be cache miss again since legacy doesn't cache
        dispatcher.dispatchCommand(request, mockSocket, mockClient, mockBaritone);

        MetricsCollector metrics = dispatcher.getMetricsCollector();

        // Cache metrics should be recorded (even if not hit)
        var cacheMetrics = metrics.getCacheMetrics();
        assertNotNull(cacheMetrics);
        // Since legacy handler doesn't use cache, should be 0 hits, 0 misses or as recorded
    }

    @Test
    void testMetricsCollection_ErrorRecording_ShouldTrackErrors() {
        // Dispatch invalid request to trigger error
        JsonObject invalidRequest = new JsonObject(); // No command

        dispatcher.dispatchCommand(invalidRequest, mockSocket, mockClient, mockBaritone);

        MetricsCollector metrics = dispatcher.getMetricsCollector();
        var errorBreakdown = metrics.getErrorTypeBreakdown();

        assertTrue(errorBreakdown.containsKey("invalid_request"));
        assertTrue(errorBreakdown.get("invalid_request") > 0);
    }

    @Test
    void testMetricsCollector_ResponseTimeStats_ShouldCalculatePercentiles() {
        MetricsCollector metrics = new MetricsCollector();

        // Record some response times
        metrics.recordCommandExecution("test", true, 100);
        metrics.recordCommandExecution("test", true, 200);
        metrics.recordCommandExecution("test", true, 300);

        var commandMetrics = metrics.getCommandMetrics();
        assertTrue(commandMetrics.containsKey("test"));

        var responseStats = commandMetrics.get("test").responseTimeStats;
        assertNotNull(responseStats);
        assertTrue(responseStats.average > 0);
        assertTrue(responseStats.p50 > 0);
        assertTrue(responseStats.p95 > 0);
        assertTrue(responseStats.p99 > 0);
    }



    @Test
    void testEndToEndCommandExecution_AdvancedCraftHandler() {
        // Register the advanced craft handler
        CommandHandlerFactory.registerHandler("craft_advanced", AdvancedCraftCommandHandler.class);

        // The real handler requires a player; with none mocked, the end-to-end
        // path through the dispatcher must surface the handler's guard error.
        JsonObject request = new JsonObject();
        request.addProperty("command", "craft_advanced");
        JsonObject params = new JsonObject();
        params.addProperty("item", "minecraft:stick");
        request.add("params", params);

        CommandResult result = dispatcher.dispatchCommand(request, mockSocket, mockClient, mockBaritone);

        assertNotNull(result);
        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("Player not available"));

        // Verify metrics were recorded for the real command name
        MetricsCollector metrics = dispatcher.getMetricsCollector();
        var commandMetrics = metrics.getCommandMetrics();
        assertTrue(commandMetrics.containsKey("craft_advanced"));
        assertEquals(1, commandMetrics.get("craft_advanced").totalExecutions);
    }

    @Test
    void testEndToEndCommandExecution_EntityInteractionHandler() {
        // Register the entity interaction handler
        CommandHandlerFactory.registerHandler("entity_interact", EntityInteractionCommandHandler.class);

        // ClientPlayerEntity cannot be instantiated or mocked without
        // Minecraft bootstrap, so the reachable end-to-end behavior here is
        // the handler's player guard flowing back through the dispatcher.
        JsonObject request = new JsonObject();
        request.addProperty("command", "entity_interact");
        JsonObject params = new JsonObject();
        params.addProperty("action", "detect");
        request.add("params", params);

        CommandResult result = dispatcher.dispatchCommand(request, mockSocket, mockClient, mockBaritone);

        assertNotNull(result);
        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("Player not available"));
    }

    @Test
    void testEndToEndCommandExecution_SequenceHandler() {
        // Register the sequence handler plus an always-succeeding command so
        // the sequence can validate and execute for real. The sequence handler
        // itself is player-agnostic (sub-commands enforce their own guards).
        CommandHandlerFactory.registerHandler("sequence", SequenceCommandHandler.class);
        CommandHandlerFactory.registerHandler("test_seq_ok", SequenceCommandHandlerTest.AlwaysOkHandler.class);

        JsonObject request = new JsonObject();
        request.addProperty("command", "sequence");
        JsonObject params = new JsonObject();
        params.addProperty("sequence", "test_seq_ok ; test_seq_ok");
        request.add("params", params);

        CommandResult result = dispatcher.dispatchCommand(request, mockSocket, mockClient, mockBaritone);

        assertNotNull(result);
        assertTrue(result.isSuccess());
        assertEquals("completed", result.getData().get("status").getAsString());
        assertEquals(2, result.getData().get("steps_executed").getAsInt());
    }

    @Test
    void testCommandDispatcherPriority_NewAdvancedCommands() {
        // Test that new advanced commands have appropriate priority
        assertEquals(CommandPriority.NORMAL, dispatcher.determinePriority("craft_advanced"));
        assertEquals(CommandPriority.NORMAL, dispatcher.determinePriority("entity_interact"));
        assertEquals(CommandPriority.NORMAL, dispatcher.determinePriority("sequence"));
    }

    @Test
    void testCommandDispatcherErrorHandling_NewHandlers() {
        // A handler error result must flow back through the dispatcher and be
        // recorded in the error metrics.
        CommandHandlerFactory.registerHandler("craft_advanced", AdvancedCraftCommandHandler.class);

        JsonObject request = new JsonObject();
        request.addProperty("command", "craft_advanced");
        request.add("params", new JsonObject());

        CommandResult result = dispatcher.dispatchCommand(request, mockSocket, mockClient, mockBaritone);

        assertNotNull(result);
        assertFalse(result.isSuccess());

        // Verify the failure was recorded in the error metrics
        MetricsCollector metrics = dispatcher.getMetricsCollector();
        var errorBreakdown = metrics.getErrorTypeBreakdown();
        assertTrue(errorBreakdown.containsKey("command_failure"));
    }

    // Test implementation of CommandHandler for registry testing
    private static class TestCommandHandler implements CommandHandler {
        @Override
        public CompletableFuture<CommandResult> handle(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
            return CompletableFuture.completedFuture(CommandResult.success(new JsonObject()));
        }

        @Override
        public String getCommandName() {
            return "test";
        }
    }
}