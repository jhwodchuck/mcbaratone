package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import baritone.api.pathing.goals.GoalBlock;
import baritone.api.pathing.goals.GoalNear;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.util.math.BlockPos;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mock;
import org.mockito.MockedStatic;
import org.mockito.MockitoAnnotations;

import java.net.Socket;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

public class GotoCommandHandlerTest {

    @Mock
    private MinecraftClient minecraftClient;
    @Mock
    private IBaritone baritone;
    @Mock
    private ClientPlayerEntity player;
    @Mock
    private Socket clientSocket;

    private GotoCommandHandler handler;

    @BeforeEach
    public void setUp() {
        MockitoAnnotations.openMocks(this);
        handler = new GotoCommandHandler();
    }

    @Test
    public void testGetCommandName() {
        assertEquals("goto", handler.getCommandName());
    }

    @Test
    public void testHandleGotoWithCoordinates() {
        JsonObject params = new JsonObject();
        params.addProperty("x", 100);
        params.addProperty("y", 64);
        params.addProperty("z", 200);

        try (MockedStatic<MinecraftClient> minecraftStatic = mockStatic(MinecraftClient.class)) {
            minecraftStatic.when(MinecraftClient::getInstance).thenReturn(minecraftClient);

            CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

            assertTrue(result.isSuccess());
            JsonObject data = result.getData();
            assertTrue(data.get("started").getAsBoolean());
            assertEquals(100, data.get("x").getAsInt());
            assertEquals(64, data.get("y").getAsInt());
            assertEquals(200, data.get("z").getAsInt());
            assertEquals(0, data.get("radius").getAsInt());

            // Verify baritone was called with GoalBlock
            verify(baritone.getCustomGoalProcess()).setGoalAndPath(any(GoalBlock.class));
        }
    }

    @Test
    public void testHandleGotoWithRadius() {
        JsonObject params = new JsonObject();
        params.addProperty("x", 50);
        params.addProperty("y", 70);
        params.addProperty("z", 150);
        params.addProperty("radius", 5);

        try (MockedStatic<MinecraftClient> minecraftStatic = mockStatic(MinecraftClient.class)) {
            minecraftStatic.when(MinecraftClient::getInstance).thenReturn(minecraftClient);

            CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

            assertTrue(result.isSuccess());
            JsonObject data = result.getData();
            assertTrue(data.get("started").getAsBoolean());
            assertEquals(5, data.get("radius").getAsInt());

            // Verify baritone was called with GoalNear
            verify(baritone.getCustomGoalProcess()).setGoalAndPath(any(GoalNear.class));
        }
    }

    @Test
    public void testHandleGotoMissingCoordinates() {
        JsonObject params = new JsonObject();
        // Missing x, y, z coordinates

        CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

        assertFalse(result.isSuccess());
        assertTrue(result.getError().contains("Missing required coordinates"));
    }

    @Test
    public void testHandleCome() {
        JsonObject params = new JsonObject();
        params.addProperty("action", "come");

        BlockPos playerPos = new BlockPos(10, 20, 30);
        when(minecraftClient.player).thenReturn(player);
        when(player.getBlockPos()).thenReturn(playerPos);

        try (MockedStatic<MinecraftClient> minecraftStatic = mockStatic(MinecraftClient.class)) {
            minecraftStatic.when(MinecraftClient::getInstance).thenReturn(minecraftClient);

            CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

            assertTrue(result.isSuccess());
            JsonObject data = result.getData();
            assertTrue(data.get("started").getAsBoolean());
            assertEquals(10, data.get("target_x").getAsInt());
            assertEquals(20, data.get("target_y").getAsInt());
            assertEquals(30, data.get("target_z").getAsInt());

            verify(baritone.getCustomGoalProcess()).setGoalAndPath(any(GoalBlock.class));
        }
    }

    @Test
    public void testHandleComeNoPlayer() {
        JsonObject params = new JsonObject();
        params.addProperty("action", "come");

        when(minecraftClient.player).thenReturn(null);

        CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

        assertFalse(result.isSuccess());
        assertEquals("Player not available", result.getError());
    }

    @Test
    public void testHandleFollow() {
        JsonObject params = new JsonObject();
        params.addProperty("action", "follow");
        params.addProperty("entity", "Steve");

        try (MockedStatic<MinecraftClient> minecraftStatic = mockStatic(MinecraftClient.class)) {
            minecraftStatic.when(MinecraftClient::getInstance).thenReturn(minecraftClient);

            CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

            assertTrue(result.isSuccess());
            JsonObject data = result.getData();
            assertTrue(data.get("sent").getAsBoolean());
            assertEquals("Steve", data.get("entity").getAsString());

            // Verify chat command was sent
            verify(minecraftClient.player.networkHandler).sendChatMessage("#follow Steve");
        }
    }

    @Test
    public void testHandleFollowDefaultEntity() {
        JsonObject params = new JsonObject();
        params.addProperty("action", "follow");
        // No entity specified, should default to "player"

        try (MockedStatic<MinecraftClient> minecraftStatic = mockStatic(MinecraftClient.class)) {
            minecraftStatic.when(MinecraftClient::getInstance).thenReturn(minecraftClient);

            CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

            assertTrue(result.isSuccess());
            JsonObject data = result.getData();
            assertEquals("player", data.get("entity").getAsString());

            verify(minecraftClient.player.networkHandler).sendChatMessage("#follow player");
        }
    }

    @Test
    public void testHandleUnknownAction() {
        JsonObject params = new JsonObject();
        params.addProperty("action", "unknown_action");

        CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

        assertFalse(result.isSuccess());
        assertEquals("Unknown goto action: unknown_action", result.getError());
    }

    @Test
    public void testHandleGotoException() {
        JsonObject params = new JsonObject();
        params.addProperty("x", 100);
        params.addProperty("y", 64);
        params.addProperty("z", 200);

        try (MockedStatic<MinecraftClient> minecraftStatic = mockStatic(MinecraftClient.class)) {
            minecraftStatic.when(MinecraftClient::getInstance).thenReturn(minecraftClient);
            doThrow(new RuntimeException("Baritone error")).when(baritone.getCustomGoalProcess());

            CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

            assertFalse(result.isSuccess());
            assertTrue(result.getError().contains("Failed to execute goto command"));
        }
    }
}