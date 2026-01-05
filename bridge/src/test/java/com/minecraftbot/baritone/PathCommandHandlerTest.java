package com.minecraftbot.baritone;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import baritone.api.IBaritone;
import baritone.api.pathing.goals.Goal;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;

import java.io.File;
import java.net.Socket;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

public class PathCommandHandlerTest {

    @Mock
    private MinecraftClient mockClient;

    @Mock
    private IBaritone mockBaritone;

    @Mock
    private Socket mockSocket;

    private PathCommandHandler handler;

    @BeforeEach
    void setUp() {
        MockitoAnnotations.openMocks(this);
        handler = new PathCommandHandler();
        // Clear active paths before each test
        PathCommandHandler.clearActivePaths();
    }

    @AfterEach
    void tearDown() {
        // Clean up after each test
        PathCommandHandler.clearActivePaths();
        // Clean up test path files
        cleanupTestFiles();
    }

    private void cleanupTestFiles() {
        File pathsDir = new File("paths");
        if (pathsDir.exists()) {
            File[] files = pathsDir.listFiles();
            if (files != null) {
                for (File file : files) {
                    if (file.getName().startsWith("test_")) {
                        file.delete();
                    }
                }
            }
        }
    }

    @Test
    void testGetCommandName() {
        assertEquals("path", handler.getCommandName());
    }

    @Test
    void testHandleNavigate_SingleWaypoint() {
        JsonObject params = new JsonObject();
        params.addProperty("x", 100);
        params.addProperty("y", 64);
        params.addProperty("z", 200);

        when(mockBaritone.getCustomGoalProcess()).thenReturn(mock(baritone.api.process.ICustomGoalProcess.class));

        CommandResult result = handler.execute(params, mockClient, mockBaritone, mockSocket);

        assertTrue(result.isSuccess());
        assertTrue(result.getData().has("navigating"));
        assertTrue(result.getData().get("navigating").getAsBoolean());
    }

    @Test
    void testHandleNavigate_MultipleWaypoints() {
        JsonObject params = new JsonObject();
        JsonArray waypoints = new JsonArray();

        JsonObject wp1 = new JsonObject();
        wp1.addProperty("x", 100);
        wp1.addProperty("y", 64);
        wp1.addProperty("z", 200);
        waypoints.add(wp1);

        JsonObject wp2 = new JsonObject();
        wp2.addProperty("x", 150);
        wp2.addProperty("y", 65);
        wp2.addProperty("z", 250);
        waypoints.add(wp2);

        params.add("waypoints", waypoints);

        when(mockBaritone.getCustomGoalProcess()).thenReturn(mock(baritone.api.process.ICustomGoalProcess.class));

        CommandResult result = handler.execute(params, mockClient, mockBaritone, mockSocket);

        assertTrue(result.isSuccess());
        assertEquals(2, result.getData().get("total_waypoints").getAsInt());
        assertEquals(0, result.getData().get("current_waypoint").getAsInt());
    }

    @Test
    void testHandleNavigate_NoWaypoints() {
        JsonObject params = new JsonObject();

        CommandResult result = handler.execute(params, mockClient, mockBaritone, mockSocket);

        assertFalse(result.isSuccess());
        assertEquals("No waypoints provided", result.getErrorMessage());
    }

    @Test
    void testHandleSave() {
        JsonObject params = new JsonObject();
        params.addProperty("action", "save");
        params.addProperty("path_name", "test_path");

        JsonArray waypoints = new JsonArray();
        JsonObject wp = new JsonObject();
        wp.addProperty("x", 100);
        wp.addProperty("y", 64);
        wp.addProperty("z", 200);
        waypoints.add(wp);
        params.add("waypoints", waypoints);

        CommandResult result = handler.execute(params, mockClient, mockBaritone, mockSocket);

        assertTrue(result.isSuccess());
        assertTrue(result.getData().has("saved"));
        assertTrue(result.getData().get("saved").getAsBoolean());

        // Verify file was created
        File pathFile = new File("paths/test_path.json");
        assertTrue(pathFile.exists());
    }

    @Test
    void testHandleLoad() {
        // First save a path
        JsonObject saveParams = new JsonObject();
        saveParams.addProperty("action", "save");
        saveParams.addProperty("path_name", "test_load");

        JsonArray waypoints = new JsonArray();
        JsonObject wp = new JsonObject();
        wp.addProperty("x", 100);
        wp.addProperty("y", 64);
        wp.addProperty("z", 200);
        waypoints.add(wp);
        saveParams.add("waypoints", waypoints);

        handler.execute(saveParams, mockClient, mockBaritone, mockSocket);

        // Now load it
        JsonObject loadParams = new JsonObject();
        loadParams.addProperty("action", "load");
        loadParams.addProperty("path_name", "test_load");

        when(mockBaritone.getCustomGoalProcess()).thenReturn(mock(baritone.api.process.ICustomGoalProcess.class));

        CommandResult result = handler.execute(loadParams, mockClient, mockBaritone, mockSocket);

        assertTrue(result.isSuccess());
        assertTrue(result.getData().has("loaded"));
        assertTrue(result.getData().get("loaded").getAsBoolean());
    }

    @Test
    void testHandleLoad_NonExistentPath() {
        JsonObject params = new JsonObject();
        params.addProperty("action", "load");
        params.addProperty("path_name", "nonexistent");

        CommandResult result = handler.execute(params, mockClient, mockBaritone, mockSocket);

        assertFalse(result.isSuccess());
        assertEquals("Path not found: nonexistent", result.getErrorMessage());
    }

    @Test
    void testHandleList() {
        // Save a test path first
        JsonObject saveParams = new JsonObject();
        saveParams.addProperty("action", "save");
        saveParams.addProperty("path_name", "test_list");

        JsonArray waypoints = new JsonArray();
        JsonObject wp = new JsonObject();
        wp.addProperty("x", 100);
        wp.addProperty("y", 64);
        wp.addProperty("z", 200);
        waypoints.add(wp);
        saveParams.add("waypoints", waypoints);

        handler.execute(saveParams, mockClient, mockBaritone, mockSocket);

        // Now list
        JsonObject listParams = new JsonObject();
        listParams.addProperty("action", "list");

        CommandResult result = handler.execute(listParams, mockClient, mockBaritone, mockSocket);

        assertTrue(result.isSuccess());
        assertTrue(result.getData().has("paths"));
        JsonArray paths = result.getData().getAsJsonArray("paths");
        assertTrue(paths.size() > 0);
        assertTrue(paths.toString().contains("test_list"));
    }

    @Test
    void testHandleStatus_NoActivePaths() {
        JsonObject params = new JsonObject();
        params.addProperty("action", "status");

        CommandResult result = handler.execute(params, mockClient, mockBaritone, mockSocket);

        assertTrue(result.isSuccess());
        assertTrue(result.getData().has("active_paths"));
    }

    @Test
    void testHandleCancel() {
        // Start a path first
        JsonObject navParams = new JsonObject();
        navParams.addProperty("x", 100);
        navParams.addProperty("y", 64);
        navParams.addProperty("z", 200);

        when(mockBaritone.getCustomGoalProcess()).thenReturn(mock(baritone.api.process.ICustomGoalProcess.class));

        handler.execute(navParams, mockClient, mockBaritone, mockSocket);

        // Now cancel
        JsonObject cancelParams = new JsonObject();
        cancelParams.addProperty("action", "cancel");

        CommandResult result = handler.execute(cancelParams, mockClient, mockBaritone, mockSocket);

        assertTrue(result.isSuccess());
        assertTrue(result.getData().has("cancelled_all"));
        assertEquals(1, result.getData().get("count").getAsInt());
    }

    @Test
    void testWaypointDistanceCalculation() {
        // Test the waypoint distance method indirectly through optimization
        JsonObject params = new JsonObject();
        JsonArray waypoints = new JsonArray();

        // Create waypoints that form a triangle
        JsonObject wp1 = new JsonObject();
        wp1.addProperty("x", 0);
        wp1.addProperty("y", 64);
        wp1.addProperty("z", 0);
        waypoints.add(wp1);

        JsonObject wp2 = new JsonObject();
        wp2.addProperty("x", 100);
        wp2.addProperty("y", 64);
        wp2.addProperty("z", 0);
        waypoints.add(wp2);

        JsonObject wp3 = new JsonObject();
        wp3.addProperty("x", 50);
        wp3.addProperty("y", 64);
        wp3.addProperty("z", 100);
        waypoints.add(wp3);

        params.add("waypoints", waypoints);
        params.addProperty("optimize", true);

        when(mockBaritone.getCustomGoalProcess()).thenReturn(mock(baritone.api.process.ICustomGoalProcess.class));

        CommandResult result = handler.execute(params, mockClient, mockBaritone, mockSocket);

        assertTrue(result.isSuccess());
        // Optimization should work without errors
    }
}