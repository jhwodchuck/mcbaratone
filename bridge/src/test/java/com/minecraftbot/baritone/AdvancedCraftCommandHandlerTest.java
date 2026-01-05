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

/**
 * Test suite for AdvancedCraftCommandHandler.
 * Tests advanced crafting features including recipe validation, multi-step sequences,
 * workbench vs inventory crafting, and error handling.
 */
@Disabled("Requires Minecraft Bootstrap (Items, Registries, Recipes) which fails in unit test environment")
class AdvancedCraftCommandHandlerTest {

    @Mock
    private MinecraftClient mockClient;

    @Mock
    private IBaritone mockBaritone;

    @Mock
    private PlayerInventory mockInventory;

    private AdvancedCraftCommandHandler handler;

    @BeforeEach
    void setUp() {
        MockitoAnnotations.openMocks(this);
        handler = new AdvancedCraftCommandHandler();

        when(mockClient.player).thenReturn(mock(net.minecraft.client.network.ClientPlayerEntity.class));
        when(mockClient.player.getInventory()).thenReturn(mockInventory);
    }

    @Test
    void testCommandName() {
        assertEquals("advanced_craft", handler.getCommandName());
    }

    @Test
    void testRecipeValidation_ValidSimpleRecipe() throws ExecutionException, InterruptedException {
        // Test recipe validation for a simple item like sticks
        JsonObject params = new JsonObject();
        params.addProperty("action", "validate_recipe");
        params.addProperty("item", "minecraft:stick");

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertTrue(result.getData().get("valid").getAsBoolean());
        assertTrue(result.getData().has("recipe_requirements"));
    }

    @Test
    void testRecipeValidation_ComplexRecipe() throws ExecutionException, InterruptedException {
        // Test recipe validation for complex item like diamond pickaxe
        JsonObject params = new JsonObject();
        params.addProperty("action", "validate_recipe");
        params.addProperty("item", "minecraft:diamond_pickaxe");

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertTrue(result.getData().get("valid").getAsBoolean());
        assertTrue(result.getData().has("recipe_requirements"));
        // Should include diamonds, sticks, etc.
    }

    @Test
    void testRecipeValidation_InvalidRecipe() throws ExecutionException, InterruptedException {
        // Test validation for non-existent item
        JsonObject params = new JsonObject();
        params.addProperty("action", "validate_recipe");
        params.addProperty("item", "minecraft:nonexistent_item");

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertFalse(result.isSuccess());
        assertEquals("Recipe not found for item: minecraft:nonexistent_item", result.getErrorMessage());
    }

    @Test
    void testMultiStepCraftingSequence() throws ExecutionException, InterruptedException {
        // Setup - Player has oak logs
        ItemStack oakLogStack = new ItemStack(Items.OAK_LOG, 4);
        when(mockInventory.size()).thenReturn(46);
        when(mockInventory.getStack(anyInt())).thenReturn(ItemStack.EMPTY);
        when(mockInventory.getStack(0)).thenReturn(oakLogStack);

        JsonObject params = new JsonObject();
        params.addProperty("action", "craft_sequence");
        params.addProperty("target", "minecraft:wooden_pickaxe");
        params.addProperty("quantity", 1);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertTrue(result.getData().has("sequence_steps"));
        assertTrue(result.getData().get("sequence_steps").getAsJsonArray().size() >= 2); // At least logs->planks and planks->sticks->pickaxe
    }

    @Test
    void testMultiStepCraftingSequence_WithInsufficientMaterials() throws ExecutionException, InterruptedException {
        // Setup - Player has no materials
        when(mockInventory.size()).thenReturn(46);
        when(mockInventory.getStack(anyInt())).thenReturn(ItemStack.EMPTY);

        JsonObject params = new JsonObject();
        params.addProperty("action", "craft_sequence");
        params.addProperty("target", "minecraft:wooden_pickaxe");
        params.addProperty("quantity", 1);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify - Should fail due to missing materials
        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("Missing required materials"));
        assertTrue(result.getData().has("missing_materials"));
    }

    @Test
    void testWorkbenchVsInventoryCrafting_WorkbenchRequired() throws ExecutionException, InterruptedException {
        // Setup - Player has materials for furnace (requires workbench)
        ItemStack cobbleStack = new ItemStack(Items.COBBLESTONE, 8);
        when(mockInventory.size()).thenReturn(46);
        when(mockInventory.getStack(anyInt())).thenReturn(ItemStack.EMPTY);
        when(mockInventory.getStack(0)).thenReturn(cobbleStack);

        JsonObject params = new JsonObject();
        params.addProperty("action", "analyze_crafting_method");
        params.addProperty("item", "minecraft:furnace");

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertEquals("workbench", result.getData().get("crafting_method").getAsString());
        assertTrue(result.getData().get("workbench_required").getAsBoolean());
    }

    @Test
    void testWorkbenchVsInventoryCrafting_InventoryOnly() throws ExecutionException, InterruptedException {
        // Setup - Player has materials for sticks (can craft in inventory)
        ItemStack plankStack = new ItemStack(Items.OAK_PLANKS, 2);
        when(mockInventory.size()).thenReturn(46);
        when(mockInventory.getStack(anyInt())).thenReturn(ItemStack.EMPTY);
        when(mockInventory.getStack(0)).thenReturn(plankStack);

        JsonObject params = new JsonObject();
        params.addProperty("action", "analyze_crafting_method");
        params.addProperty("item", "minecraft:stick");

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertEquals("inventory", result.getData().get("crafting_method").getAsString());
        assertFalse(result.getData().get("workbench_required").getAsBoolean());
    }

    @Test
    void testErrorHandling_MissingIngredients() throws ExecutionException, InterruptedException {
        // Setup - Player has insufficient materials
        ItemStack plankStack = new ItemStack(Items.OAK_PLANKS, 1); // Need 3 for pickaxe
        when(mockInventory.size()).thenReturn(46);
        when(mockInventory.getStack(anyInt())).thenReturn(ItemStack.EMPTY);
        when(mockInventory.getStack(0)).thenReturn(plankStack);

        JsonObject params = new JsonObject();
        params.addProperty("action", "craft");
        params.addProperty("item", "minecraft:wooden_pickaxe");

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("Insufficient materials"));
        assertTrue(result.getData().has("missing_ingredients"));
        assertTrue(result.getData().get("missing_ingredients").getAsJsonObject().has("minecraft:oak_planks"));
    }

    @Test
    void testErrorHandling_NoWorkbenchAvailable() throws ExecutionException, InterruptedException {
        // Setup - Player has materials for furnace but no workbench available
        ItemStack cobbleStack = new ItemStack(Items.COBBLESTONE, 8);
        when(mockInventory.size()).thenReturn(46);
        when(mockInventory.getStack(anyInt())).thenReturn(ItemStack.EMPTY);
        when(mockInventory.getStack(0)).thenReturn(cobbleStack);

        JsonObject params = new JsonObject();
        params.addProperty("action", "craft");
        params.addProperty("item", "minecraft:furnace");
        params.addProperty("allow_workbench_placement", false);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("Workbench required"));
    }

    @Test
    void testRecipeValidation_WithAlternativeRecipes() throws ExecutionException, InterruptedException {
        // Test validation for items with multiple recipes (like beds)
        JsonObject params = new JsonObject();
        params.addProperty("action", "validate_recipe");
        params.addProperty("item", "minecraft:white_bed");

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertTrue(result.getData().has("alternative_recipes"));
        assertTrue(result.getData().get("alternative_recipes").getAsJsonArray().size() > 0);
    }

    @Test
    void testBulkCrafting_WithEfficiency() throws ExecutionException, InterruptedException {
        // Setup - Player has lots of materials
        ItemStack logStack = new ItemStack(Items.OAK_LOG, 64);
        when(mockInventory.size()).thenReturn(46);
        when(mockInventory.getStack(anyInt())).thenReturn(ItemStack.EMPTY);
        when(mockInventory.getStack(0)).thenReturn(logStack);

        JsonObject params = new JsonObject();
        params.addProperty("action", "bulk_craft");
        params.addProperty("item", "minecraft:stick");
        params.addProperty("quantity", 16);
        params.addProperty("optimize_efficiency", true);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertTrue(result.getData().get("efficiency_optimized").getAsBoolean());
        assertTrue(result.getData().has("crafting_plan"));
    }

    @Test
    void testInvalidAction() throws ExecutionException, InterruptedException {
        JsonObject params = new JsonObject();
        params.addProperty("action", "invalid_action");

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertFalse(result.isSuccess());
        assertEquals("Unknown action: invalid_action", result.getErrorMessage());
    }

    @Test
    void testEnhancedValidationFramework_DetailedErrorMessages() throws ExecutionException, InterruptedException {
        // Test detailed error messages for invalid parameters
        JsonObject params = new JsonObject();
        params.addProperty("action", "craft");
        params.addProperty("item", "invalid:item:name"); // Invalid item name format

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify detailed error message
        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("Invalid item name format"));
        assertTrue(result.getData().has("validation_details"));
    }

    @Test
    void testEnhancedValidationFramework_ErrorRecoverySuggestions() throws ExecutionException, InterruptedException {
        // Test error recovery suggestions
        JsonObject params = new JsonObject();
        params.addProperty("action", "craft");
        params.addProperty("item", "minecraft:wooden_pickaxe");
        // Missing count parameter

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify error recovery suggestions
        assertFalse(result.isSuccess());
        assertTrue(result.getData().has("recovery_suggestions"));
        var suggestions = result.getData().get("recovery_suggestions").getAsJsonArray();
        assertTrue(suggestions.size() > 0);
        // Should suggest adding count parameter or using default
    }

    @Test
    void testParameterValidationFramework_TypeValidation() throws ExecutionException, InterruptedException {
        // Test parameter type validation
        JsonObject params = new JsonObject();
        params.addProperty("action", "craft");
        params.addProperty("item", "minecraft:stick");
        params.addProperty("count", "not_a_number"); // Should be integer

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify type validation error
        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("count") && result.getErrorMessage().contains("integer"));
        assertTrue(result.getData().has("parameter_validation_errors"));
    }

    @Test
    void testParameterValidationFramework_RangeValidation() throws ExecutionException, InterruptedException {
        // Test parameter range validation
        JsonObject params = new JsonObject();
        params.addProperty("action", "bulk_craft");
        params.addProperty("item", "minecraft:stick");
        params.addProperty("quantity", -5); // Negative quantity should be invalid

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify range validation error
        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("quantity") && result.getErrorMessage().contains("positive"));
        assertTrue(result.getData().has("range_validation_errors"));
    }

    @Test
    void testDetailedErrorMessages_WithContext() throws ExecutionException, InterruptedException {
        // Test detailed error messages with context
        JsonObject params = new JsonObject();
        params.addProperty("action", "craft_sequence");
        params.addProperty("target", "minecraft:diamond_pickaxe");
        // Missing materials

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify detailed error with context
        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("diamond_pickaxe"));
        assertTrue(result.getData().has("error_context"));
        var context = result.getData().get("error_context").getAsJsonObject();
        assertTrue(context.has("required_materials"));
        assertTrue(context.has("available_materials"));
    }

    @Test
    void testErrorRecoverySuggestions_MultipleOptions() throws ExecutionException, InterruptedException {
        // Test multiple recovery suggestions
        JsonObject params = new JsonObject();
        params.addProperty("action", "craft");
        params.addProperty("item", "minecraft:furnace");
        // Missing cobblestone but has other materials

        ItemStack woodStack = new ItemStack(Items.OAK_PLANKS, 8);
        when(mockInventory.size()).thenReturn(46);
        when(mockInventory.getStack(anyInt())).thenReturn(ItemStack.EMPTY);
        when(mockInventory.getStack(0)).thenReturn(woodStack);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify multiple recovery suggestions
        assertFalse(result.isSuccess());
        assertTrue(result.getData().has("recovery_suggestions"));
        var suggestions = result.getData().get("recovery_suggestions").getAsJsonArray();
        assertTrue(suggestions.size() >= 2); // At least mine cobblestone or use existing materials
    }
}