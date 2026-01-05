package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.entity.player.PlayerInventory;
import net.minecraft.item.ItemStack;
import net.minecraft.item.Items;
import net.minecraft.registry.Registries;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;

import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;

import org.junit.jupiter.api.Disabled;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

@Disabled("Requires Minecraft Bootstrap (Items, Registries, Recipes) which fails in unit test environment")
class AutoCraftCommandHandlerPhase4Test {

    @Mock
    private MinecraftClient mockClient;

    @Mock
    private IBaritone mockBaritone;

    @Mock
    private PlayerInventory mockInventory;

    private AutoCraftCommandHandler handler;

    @BeforeEach
    void setUp() {
        MockitoAnnotations.openMocks(this);
        handler = new AutoCraftCommandHandler();

        when(mockClient.player).thenReturn(mock(net.minecraft.client.network.ClientPlayerEntity.class));
        when(mockClient.player.getInventory()).thenReturn(mockInventory);
    }

    @Test
    void testCommandName() {
        assertEquals("auto_craft", handler.getCommandName());
    }

    @Test
    void testDiscoverRecipes_WithOakLogs() throws ExecutionException, InterruptedException {
        // Setup - Player has oak logs
        ItemStack oakLogStack = new ItemStack(Items.OAK_LOG, 4);
        when(mockInventory.size()).thenReturn(46);
        when(mockInventory.getStack(anyInt())).thenReturn(ItemStack.EMPTY);
        when(mockInventory.getStack(0)).thenReturn(oakLogStack);

        JsonObject params = new JsonObject();
        params.addProperty("action", "discover");

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertTrue(result.getData().has("discovered_recipes"));
        // Should discover planks and sticks
    }

    @Test
    void testDiscoverRecipes_WithCobblestone() throws ExecutionException, InterruptedException {
        // Setup - Player has cobblestone
        ItemStack cobbleStack = new ItemStack(Items.COBBLESTONE, 8);
        when(mockInventory.size()).thenReturn(46);
        when(mockInventory.getStack(anyInt())).thenReturn(ItemStack.EMPTY);
        when(mockInventory.getStack(0)).thenReturn(cobbleStack);

        JsonObject params = new JsonObject();
        params.addProperty("action", "discover");

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertTrue(result.getData().has("discovered_recipes"));
        // Should discover furnace
    }

    @Test
    void testOptimizeCrafting_SimpleItem() throws ExecutionException, InterruptedException {
        // Setup - Player has enough materials for sticks
        ItemStack plankStack = new ItemStack(Items.OAK_PLANKS, 4);
        when(mockInventory.size()).thenReturn(46);
        when(mockInventory.getStack(anyInt())).thenReturn(ItemStack.EMPTY);
        when(mockInventory.getStack(0)).thenReturn(plankStack);

        JsonObject params = new JsonObject();
        params.addProperty("action", "optimize");
        params.addProperty("target", "minecraft:stick");
        params.addProperty("quantity", 4);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertTrue(result.getData().get("optimized").getAsBoolean());
        assertEquals("minecraft:stick", result.getData().get("target_item").getAsString());
        assertTrue(result.getData().has("crafting_steps"));
    }

    @Test
    void testOptimizeCrafting_ComplexItem() throws ExecutionException, InterruptedException {
        // Setup - Player has enough materials for wooden pickaxe (needs planks and sticks)
        ItemStack plankStack = new ItemStack(Items.OAK_PLANKS, 12);
        ItemStack stickStack = new ItemStack(Items.STICK, 4);
        when(mockInventory.size()).thenReturn(46);
        when(mockInventory.getStack(anyInt())).thenReturn(ItemStack.EMPTY);
        when(mockInventory.getStack(0)).thenReturn(plankStack);
        when(mockInventory.getStack(1)).thenReturn(stickStack);

        JsonObject params = new JsonObject();
        params.addProperty("action", "optimize");
        params.addProperty("target", "minecraft:wooden_pickaxe");
        params.addProperty("quantity", 1);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertTrue(result.getData().get("optimized").getAsBoolean());
        assertTrue(result.getData().has("crafting_steps"));
        assertTrue(result.getData().get("steps").getAsInt() >= 0);
    }

    @Test
    void testOptimizeCrafting_InsufficientMaterials() throws ExecutionException, InterruptedException {
        // Setup - Player has no materials
        when(mockInventory.size()).thenReturn(46);
        when(mockInventory.getStack(anyInt())).thenReturn(ItemStack.EMPTY);

        JsonObject params = new JsonObject();
        params.addProperty("action", "optimize");
        params.addProperty("target", "minecraft:stick");
        params.addProperty("quantity", 4);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify - Should still return a plan but with missing materials
        assertTrue(result.isSuccess());
        assertTrue(result.getData().get("optimized").getAsBoolean());
        assertTrue(result.getData().has("crafting_steps"));
    }

    @Test
    void testOptimizeCrafting_InvalidTarget() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("action", "optimize");
        params.addProperty("target", "minecraft:nonexistent_item");
        params.addProperty("quantity", 1);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertFalse(result.isSuccess());
        assertEquals("Could not create crafting plan", result.getData().get("error").getAsString());
    }

    @Test
    void testCraftingQueueWithDependencies() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("action", "queue");
        params.addProperty("item", "minecraft:wooden_pickaxe");
        params.addProperty("quantity", 1);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertTrue(result.getData().get("queued").getAsBoolean());
        assertTrue(result.getData().get("quantity").getAsInt() == 1);
    }

    @Test
    void testQueueStatus() throws ExecutionException, InterruptedException {
        // First queue an item
        JsonObject queueParams = new JsonObject();
        queueParams.addProperty("action", "queue");
        queueParams.addProperty("item", "minecraft:stick");
        queueParams.addProperty("quantity", 4);

        handler.handle(queueParams, mockClient, mockBaritone, null).get();

        // Now check status
        JsonObject statusParams = new JsonObject();
        statusParams.addProperty("action", "status");

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(statusParams, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertTrue(result.getData().has("queue"));
        assertTrue(result.getData().get("queue_size").getAsInt() >= 0);
    }

    @Test
    void testClearQueue() throws ExecutionException, InterruptedException {
        // First queue an item
        JsonObject queueParams = new JsonObject();
        queueParams.addProperty("action", "queue");
        queueParams.addProperty("item", "minecraft:oak_planks");
        queueParams.addProperty("quantity", 4);

        handler.handle(queueParams, mockClient, mockBaritone, null).get();

        // Now clear queue
        JsonObject clearParams = new JsonObject();
        clearParams.addProperty("action", "clear");

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(clearParams, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertTrue(result.getData().get("cleared").getAsBoolean());
    }

    @Test
    void testInvalidAction() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("action", "invalid_action");

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertFalse(result.isSuccess());
        assertEquals("Unknown action: invalid_action", result.getErrorMessage());
    }
}