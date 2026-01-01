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

public class CommandDispatcherTest {

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
    public void testDispatchCommandMissingCommand() {
        JsonObject request = new JsonObject();

        CommandResult result = dispatcher.dispatchCommand(request, clientSocket, minecraftClient, baritone);

        assertFalse(result.isSuccess());
        assertEquals("Missing command", result.getErrorMessage());
    }

    @Test
    public void testDispatchCommandHandledByMissionController() {
        JsonObject request = new JsonObject();
        request.addProperty("command", "mission");
        request.addProperty("id", "123");

        JsonObject missionData = new JsonObject();
        missionData.addProperty("mission_started", true);

        when(missionController.tryHandle(eq("mission"), any(), any(), any(), any(), any())).thenReturn(true);

        CommandResult result = dispatcher.dispatchCommand(request, clientSocket, minecraftClient, baritone);

        assertTrue(result.isSuccess());
        assertEquals(missionData, result.getData());
        verify(missionController).tryHandle(eq("mission"), any(), any(), any(), any(), any());
    }

    @Test
    public void testDispatchCommandHandledByFactory() {
        JsonObject request = new JsonObject();
        request.addProperty("command", "goto");
        request.addProperty("id", "123");

        JsonObject params = new JsonObject();
        params.addProperty("x", 100);
        params.addProperty("y", 64);
        params.addProperty("z", 200);
        request.add("params", params);

        // Mock a command handler
        CommandHandler mockHandler = mock(CommandHandler.class);
        when(mockHandler.handle(any(), any(), any(), any())).thenReturn(CommandResult.success(new JsonObject()));

        // Temporarily register the handler
        CommandHandlerFactory.registerHandler("goto", mockHandler.getClass());

        try {
            CommandResult result = dispatcher.dispatchCommand(request, clientSocket, minecraftClient, baritone);

            assertTrue(result.isSuccess());
            verify(mockHandler).handle(eq(params), eq(minecraftClient), eq(baritone), eq(clientSocket));
        } finally {
            // Clean up
            CommandHandlerFactory.clearRegistry();
        }
    }

    @Test
    public void testDispatchCommandFallbackToLegacy() {
        JsonObject request = new JsonObject();
        request.addProperty("command", "unknown_command");
        request.addProperty("id", "123");

        JsonObject legacyResult = new JsonObject();
        legacyResult.addProperty("legacy_handled", true);

        when(missionController.tryHandle(any(), any(), any(), any(), any(), any())).thenReturn(false);
        when(legacyHandler.handleLegacyCommand(eq("unknown_command"), any(), any(), any(), any()))
            .thenReturn(CommandResult.success(legacyResult));

        CommandResult result = dispatcher.dispatchCommand(request, clientSocket, minecraftClient, baritone);

        assertTrue(result.isSuccess());
        assertEquals(legacyResult, result.getData());
        verify(legacyHandler).handleLegacyCommand(eq("unknown_command"), any(), any(), any(), any());
    }

    @Test
    public void testRateLimiting() {
        JsonObject request = new JsonObject();
        request.addProperty("command", "goto");
        request.addProperty("id", "123");

        // Make multiple requests quickly to trigger rate limit
        for (int i = 0; i < 101; i++) {
            dispatcher.dispatchCommand(request, clientSocket, minecraftClient, baritone);
        }

        CommandResult result = dispatcher.dispatchCommand(request, clientSocket, minecraftClient, baritone);

        assertFalse(result.isSuccess());
        assertTrue(result.getError().contains("Rate limit exceeded"));
    }

    @Test
    public void testRateLimitResetAfterWindow() throws InterruptedException {
        JsonObject request = new JsonObject();
        request.addProperty("command", "goto");
        request.addProperty("id", "123");

        // Mock the last request time to be older than the rate limit window
        dispatcher.clearRateLimit(clientSocket);

        // Wait for rate limit window to pass (simulate)
        Thread.sleep(11000); // 11 seconds > 10 second window

        CommandResult result = dispatcher.dispatchCommand(request, clientSocket, minecraftClient, baritone);

        // Should not be rate limited since window passed
        assertFalse(result.getError() != null && result.getError().contains("Rate limit exceeded"));
    }

    @Test
    public void testExceptionHandling() {
        JsonObject request = new JsonObject();
        request.addProperty("command", "goto");

        when(missionController.tryHandle(any(), any(), any(), any(), any(), any()))
            .thenThrow(new RuntimeException("Test exception"));

        CommandResult result = dispatcher.dispatchCommand(request, clientSocket, minecraftClient, baritone);

        assertFalse(result.isSuccess());
        assertTrue(result.getError().contains("Command execution failed"));
    }

    @Test
    public void testNoLegacyHandler() {
        // Create dispatcher without legacy handler
        CommandDispatcher noLegacyDispatcher = new CommandDispatcher(missionController, null);

        JsonObject request = new JsonObject();
        request.addProperty("command", "unknown_command");

        when(missionController.tryHandle(any(), any(), any(), any(), any(), any())).thenReturn(false);

        CommandResult result = noLegacyDispatcher.dispatchCommand(request, clientSocket, minecraftClient, baritone);

        assertFalse(result.isSuccess());
        assertTrue(result.getError().contains("no legacy handler configured"));
    }
}