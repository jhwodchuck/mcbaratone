package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import baritone.api.behavior.IPathingBehavior;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.entity.player.HungerManager;
import net.minecraft.util.math.BlockPos;
import net.minecraft.world.World;
import net.minecraft.registry.RegistryKey;
import net.minecraft.util.Identifier;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;

import java.net.Socket;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

public class StateCommandHandlerTest {

    @Mock
    private MinecraftClient minecraftClient;
    @Mock
    private IBaritone baritone;
    @Mock
    private ClientPlayerEntity player;
    @Mock
    private World world;
    @Mock
    private HungerManager hungerManager;
    @Mock
    private IPathingBehavior pathingBehavior;
    @Mock
    private Socket clientSocket;

    private StateCommandHandler handler;

    @BeforeEach
    public void setUp() {
        MockitoAnnotations.openMocks(this);
        handler = new StateCommandHandler();
        when(minecraftClient.player).thenReturn(player);
        when(minecraftClient.world).thenReturn(world);
        when(player.getHungerManager()).thenReturn(hungerManager);
        when(baritone.getPathingBehavior()).thenReturn(pathingBehavior);
    }

    @Test
    public void testGetCommandName() {
        assertEquals("state", handler.getCommandName());
    }

    @Test
    public void testHandleGetState() {
        JsonObject params = new JsonObject();
        params.addProperty("action", "get");

        // Mock player state
        when(player.getX()).thenReturn(100.5);
        when(player.getY()).thenReturn(64.0);
        when(player.getZ()).thenReturn(200.25);
        when(player.getYaw()).thenReturn(45.0f);
        when(player.getPitch()).thenReturn(-15.0f);
        when(player.getBlockPos()).thenReturn(new BlockPos(100, 64, 200));
        when(player.getHealth()).thenReturn(20.0f);
        when(player.getMaxHealth()).thenReturn(20.0f);
        when(hungerManager.getFoodLevel()).thenReturn(10);
        when(hungerManager.getSaturationLevel()).thenReturn(5.0f);
        when(player.experienceLevel).thenReturn(15);
        when(player.totalExperience).thenReturn(1234);
        when(player.isDead()).thenReturn(false);
        when(pathingBehavior.isPathing()).thenReturn(true);

        RegistryKey<World> dimensionKey = mock(RegistryKey.class);
        when(world.getRegistryKey()).thenReturn(dimensionKey);
        when(dimensionKey.getValue()).thenReturn(new Identifier("minecraft:overworld"));

        CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

        assertTrue(result.isSuccess());
        JsonObject data = result.getData();
        assertEquals(100.5, data.getAsJsonObject("position").get("x").getAsDouble());
        assertEquals(64.0, data.getAsJsonObject("position").get("y").getAsDouble());
        assertEquals(200.25, data.getAsJsonObject("position").get("z").getAsDouble());
        assertEquals(45.0, data.getAsJsonObject("position").get("yaw").getAsDouble());
        assertEquals(-15.0, data.getAsJsonObject("position").get("pitch").getAsDouble());
        assertEquals(100, data.getAsJsonObject("block_position").get("x").getAsInt());
        assertEquals(20.0, data.get("health").getAsDouble());
        assertEquals(10, data.get("food_level").getAsInt());
        assertEquals(15, data.get("experience_level").getAsInt());
        assertFalse(data.get("is_dead").getAsBoolean());
        assertTrue(data.get("is_pathing").getAsBoolean());
        assertEquals("minecraft:overworld", data.get("dimension").getAsString());
    }

    @Test
    public void testHandleGetStateNoPlayer() {
        JsonObject params = new JsonObject();
        params.addProperty("action", "get");

        when(minecraftClient.player).thenReturn(null);

        CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

        assertFalse(result.isSuccess());
        assertEquals("Player not available", result.getError());
    }

    @Test
    public void testHandleGetEntities() {
        JsonObject params = new JsonObject();
        params.addProperty("action", "entities");
        params.addProperty("radius", 32);

        // Mock world and entities - simplified for testing
        when(player.getX()).thenReturn(0.0);
        when(player.getY()).thenReturn(0.0);
        when(player.getZ()).thenReturn(0.0);

        // Mock empty entity list for simplicity
        when(world.getOtherEntities(any(), any())).thenReturn(java.util.Collections.emptyList());

        CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

        assertTrue(result.isSuccess());
        JsonObject data = result.getData();
        assertTrue(data.has("entities"));
        assertEquals(0, data.get("count").getAsInt());
        assertEquals(32, data.get("radius").getAsInt());
    }

    @Test
    public void testHandleGetEntitiesNoWorld() {
        JsonObject params = new JsonObject();
        params.addProperty("action", "entities");

        when(minecraftClient.world).thenReturn(null);

        CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

        assertFalse(result.isSuccess());
        assertEquals("World or player not available", result.getError());
    }

    @Test
    public void testHandleGetEntitiesNoPlayer() {
        JsonObject params = new JsonObject();
        params.addProperty("action", "entities");

        when(minecraftClient.player).thenReturn(null);

        CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

        assertFalse(result.isSuccess());
        assertEquals("World or player not available", result.getError());
    }

    @Test
    public void testHandleUnknownAction() {
        JsonObject params = new JsonObject();
        params.addProperty("action", "unknown");

        CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

        assertFalse(result.isSuccess());
        assertEquals("Unknown state action: unknown", result.getError());
    }

    @Test
    public void testDefaultAction() {
        JsonObject params = new JsonObject();
        // No action specified, should default to "get"

        when(pathingBehavior.isPathing()).thenReturn(false);

        CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

        assertTrue(result.isSuccess());
        assertTrue(result.getData().has("position"));
    }
}