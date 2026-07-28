package com.minecraftbot.baritone;

import com.google.gson.JsonObject;
import baritone.api.IBaritone;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;

import java.net.Socket;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.block.Blocks;
import org.junit.jupiter.api.Disabled;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

@Disabled("Requires Minecraft Bootstrap (Registries) which fails in unit test environment")
public class PlaceFireCommandHandlerTest {

    @Mock
    private Minecraft mockClient;

    @Mock
    private IBaritone mockBaritone;

    @Mock
    private Socket mockSocket;

    @Mock
    private ClientLevel mockWorld;

    @Mock
    private LocalPlayer mockPlayer;

    private PlaceFireCommandHandler handler;

    @org.junit.jupiter.api.BeforeAll
    static void init() {
        TestUtils.initializeBootstrap();
    }

    @BeforeEach
    void setUp() {
        MockitoAnnotations.openMocks(this);
        handler = new PlaceFireCommandHandler();

        // Setup common mocks
        TestUtils.setField(mockClient, "level", mockWorld);
        TestUtils.setField(mockClient, "player", mockPlayer);
        when(mockPlayer.blockPosition()).thenReturn(new BlockPos(0, 64, 0));
    }

    @Test
    void testGetCommandName() {
        assertEquals("place_fire", handler.getCommandName());
    }

    @Test
    void testValidation_MissingCoordinates() {
        JsonObject params = new JsonObject();

        CompletableFuture<CommandResult> future = handler.execute(params, mockClient, mockBaritone, mockSocket);
        CommandResult result = future.join();

        assertFalse(result.isSuccess());
        assertEquals("Missing required coordinates (x, y, z)", result.getErrorMessage());
    }

    @Test
    void testValidation_InvalidCoordinates() {
        JsonObject params = new JsonObject();
        params.addProperty("x", 50000000); // Out of bounds
        params.addProperty("y", 64);
        params.addProperty("z", 0);

        CompletableFuture<CommandResult> future = handler.execute(params, mockClient, mockBaritone, mockSocket);
        CommandResult result = future.join();

        assertFalse(result.isSuccess());
        assertEquals("Coordinates out of valid Minecraft range", result.getErrorMessage());
    }

    @Test
    void testSafetyCheck_InvalidSurface() {
        JsonObject params = new JsonObject();
        params.addProperty("x", 10);
        params.addProperty("y", 64);
        params.addProperty("z", 0);

        // Mock invalid surface (dirt block)
        BlockPos targetPos = new BlockPos(10, 64, 0);
        when(mockWorld.getBlockState(targetPos)).thenReturn(Blocks.DIRT.defaultBlockState());

        // Mock inventory with flint and steel
        ItemStack flintAndSteel = new ItemStack(Items.FLINT_AND_STEEL);
        when(mockPlayer.getInventory().getItem(0)).thenReturn(flintAndSteel);
        when(mockPlayer.getInventory().selected).thenReturn(0);

        CompletableFuture<CommandResult> future = handler.execute(params, mockClient, mockBaritone, mockSocket);
        CommandResult result = future.join();

        assertFalse(result.isSuccess());
        assertEquals("Fire placement unsafe: Invalid surface for fire placement", result.getErrorMessage());
    }

    @Test
    void testSafetyCheck_TooManyFlammableBlocks() {
        JsonObject params = new JsonObject();
        params.addProperty("x", 10);
        params.addProperty("y", 64);
        params.addProperty("z", 0);

        // Mock valid surface
        BlockPos targetPos = new BlockPos(10, 64, 0);
        when(mockWorld.getBlockState(targetPos)).thenReturn(Blocks.NETHERRACK.defaultBlockState());

        // Mock excessive flammable blocks nearby
        for (int dx = -3; dx <= 3; dx++) {
            for (int dy = -3; dy <= 3; dy++) {
                for (int dz = -3; dz <= 3; dz++) {
                    if (dx == 0 && dy == 0 && dz == 0) continue;
                    BlockPos checkPos = targetPos.offset(dx, dy, dz);
                    when(mockWorld.getBlockState(checkPos)).thenReturn(Blocks.OAK_LOG.defaultBlockState());
                }
            }
        }

        // Mock inventory
        ItemStack flintAndSteel = new ItemStack(Items.FLINT_AND_STEEL);
        when(mockPlayer.getInventory().getItem(0)).thenReturn(flintAndSteel);
        when(mockPlayer.getInventory().selected).thenReturn(0);

        CommandResult result = handler.execute(params, mockClient, mockBaritone, mockSocket).join();

        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("Too many flammable blocks nearby"));
    }

    @Test
    void testSafetyCheck_TooCloseToPlayer() {
        JsonObject params = new JsonObject();
        params.addProperty("x", 0);
        params.addProperty("y", 64);
        params.addProperty("z", 0);

        // Mock valid surface
        BlockPos targetPos = new BlockPos(0, 64, 0);
        when(mockWorld.getBlockState(targetPos)).thenReturn(Blocks.NETHERRACK.defaultBlockState());

        // Mock inventory
        ItemStack flintAndSteel = new ItemStack(Items.FLINT_AND_STEEL);
        when(mockPlayer.getInventory().getItem(0)).thenReturn(flintAndSteel);
        when(mockPlayer.getInventory().selected).thenReturn(0);

        CommandResult result = handler.execute(params, mockClient, mockBaritone, mockSocket).join();

        assertFalse(result.isSuccess());
        assertEquals("Fire placement unsafe: Too close to player position", result.getErrorMessage());
    }

    @Test
    void testInventoryCheck_NoTools() {
        JsonObject params = new JsonObject();
        params.addProperty("x", 10);
        params.addProperty("y", 64);
        params.addProperty("z", 0);

        // Mock valid surface
        BlockPos targetPos = new BlockPos(10, 64, 0);
        when(mockWorld.getBlockState(targetPos)).thenReturn(Blocks.NETHERRACK.defaultBlockState());

        // Mock empty inventory
        when(mockPlayer.getInventory().getItem(anyInt())).thenReturn(ItemStack.EMPTY);

        CommandResult result = handler.execute(params, mockClient, mockBaritone, mockSocket).join();

        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("No usable flint and steel or fire charge"));
    }

    @Test
    void testInventoryCheck_FlintAndSteelLowDurability() {
        JsonObject params = new JsonObject();
        params.addProperty("x", 10);
        params.addProperty("y", 64);
        params.addProperty("z", 0);

        // Mock valid surface
        BlockPos targetPos = new BlockPos(10, 64, 0);
        when(mockWorld.getBlockState(targetPos)).thenReturn(Blocks.NETHERRACK.defaultBlockState());

        // Mock flint and steel with 0 durability left
        ItemStack flintAndSteel = new ItemStack(Items.FLINT_AND_STEEL);
        flintAndSteel.setDamageValue(flintAndSteel.getMaxDamage());
        when(mockPlayer.getInventory().getItem(0)).thenReturn(flintAndSteel);

        CommandResult result = handler.execute(params, mockClient, mockBaritone, mockSocket).join();

        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("No usable flint and steel or fire charge"));
    }

    @Test
    void testSuccessfulFirePlacement() {
        JsonObject params = new JsonObject();
        params.addProperty("x", 10);
        params.addProperty("y", 64);
        params.addProperty("z", 0);

        // Mock valid surface
        BlockPos targetPos = new BlockPos(10, 64, 0);
        when(mockWorld.getBlockState(targetPos)).thenReturn(Blocks.NETHERRACK.defaultBlockState());

        // Mock safe environment (no flammable blocks nearby)
        when(mockWorld.getBlockState(any(BlockPos.class))).thenReturn(Blocks.AIR.defaultBlockState());
        when(mockWorld.getBlockState(targetPos)).thenReturn(Blocks.NETHERRACK.defaultBlockState());

        // Mock inventory with flint and steel
        ItemStack flintAndSteel = new ItemStack(Items.FLINT_AND_STEEL);
        when(mockPlayer.getInventory().getItem(0)).thenReturn(flintAndSteel);
        when(mockPlayer.getInventory().selected).thenReturn(0);

        // Mock player position far enough away
        when(mockPlayer.blockPosition()).thenReturn(new BlockPos(0, 64, 0));

        CommandResult result = handler.execute(params, mockClient, mockBaritone, mockSocket).join();

        assertTrue(result.isSuccess());
        assertTrue(result.getData().has("ignited"));
        assertTrue(result.getData().get("ignited").getAsBoolean());
        assertTrue(result.getData().has("fuel_score"));
        assertTrue(result.getData().has("risk_level"));
        assertTrue(result.getData().has("flammable_blocks_nearby"));
        assertTrue(result.getData().has("tool_durability"));
    }

    @Test
    void testPlacementOptimization() {
        JsonObject params = new JsonObject();
        params.addProperty("x", 10);
        params.addProperty("y", 64);
        params.addProperty("z", 0);

        // Mock valid surface at target
        BlockPos targetPos = new BlockPos(10, 64, 0);
        when(mockWorld.getBlockState(targetPos)).thenReturn(Blocks.NETHERRACK.defaultBlockState());

        // Mock better position with flammable blocks nearby
        BlockPos betterPos = new BlockPos(11, 64, 0);
        when(mockWorld.getBlockState(betterPos)).thenReturn(Blocks.NETHERRACK.defaultBlockState());
        when(mockWorld.getBlockState(betterPos.offset(-1, 0, 0))).thenReturn(Blocks.OAK_LOG.defaultBlockState()); // Adjacent flammable

        // Mock safe environment
        when(mockWorld.getBlockState(any(BlockPos.class))).thenReturn(Blocks.AIR.defaultBlockState());
        when(mockWorld.getBlockState(targetPos)).thenReturn(Blocks.NETHERRACK.defaultBlockState());
        when(mockWorld.getBlockState(betterPos)).thenReturn(Blocks.NETHERRACK.defaultBlockState());
        when(mockWorld.getBlockState(betterPos.offset(-1, 0, 0))).thenReturn(Blocks.OAK_LOG.defaultBlockState());

        // Mock inventory
        ItemStack flintAndSteel = new ItemStack(Items.FLINT_AND_STEEL);
        when(mockPlayer.getInventory().getItem(0)).thenReturn(flintAndSteel);
        when(mockPlayer.getInventory().selected).thenReturn(0);

        // Mock player position
        when(mockPlayer.blockPosition()).thenReturn(new BlockPos(0, 64, 0));

        CommandResult result = handler.execute(params, mockClient, mockBaritone, mockSocket).join();

        assertTrue(result.isSuccess());
        // Should suggest optimal position
        assertTrue(result.getData().has("optimal_position"));
        JsonObject optimal = result.getData().getAsJsonObject("optimal_position");
        assertEquals(11, optimal.get("x").getAsInt());
    }

    @Test
    void testFireChargeUsage() {
        JsonObject params = new JsonObject();
        params.addProperty("x", 10);
        params.addProperty("y", 64);
        params.addProperty("z", 0);

        // Mock valid surface
        BlockPos targetPos = new BlockPos(10, 64, 0);
        when(mockWorld.getBlockState(targetPos)).thenReturn(Blocks.NETHERRACK.defaultBlockState());

        // Mock safe environment
        when(mockWorld.getBlockState(any(BlockPos.class))).thenReturn(Blocks.AIR.defaultBlockState());
        when(mockWorld.getBlockState(targetPos)).thenReturn(Blocks.NETHERRACK.defaultBlockState());

        // Mock inventory with fire charge (stack of 32)
        ItemStack fireCharge = new ItemStack(Items.FIRE_CHARGE, 32);
        when(mockPlayer.getInventory().getItem(0)).thenReturn(fireCharge);
        when(mockPlayer.getInventory().selected).thenReturn(0);

        // Mock player position
        when(mockPlayer.blockPosition()).thenReturn(new BlockPos(0, 64, 0));

        CommandResult result = handler.execute(params, mockClient, mockBaritone, mockSocket).join();

        assertTrue(result.isSuccess());
        assertEquals(32.0/64.0, result.getData().get("tool_durability").getAsDouble(), 0.01);
    }
}
