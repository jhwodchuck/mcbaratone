package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.Socket;
import java.util.*;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;

/**
 * Advanced crafting command handler with recipe validation and multi-step crafting support.
 * Supports complex crafting sequences with intermediate item creation and detailed error reporting.
 */
public class AdvancedCraftCommandHandler extends AbstractCommandHandler {

    private static final Logger logger = LoggerFactory.getLogger(AdvancedCraftCommandHandler.class);
    private static final int WORKBENCH_SEARCH_RADIUS = 5;
    private static final int WORKBENCH_OPEN_POLLS = 30;
    private static final long WORKBENCH_OPEN_POLL_MS = 50L;

    // Recipe definition class
    private static class CraftRecipe {
        final String output;
        final int outputCount;
        final boolean requiresTable;
        final String[] grid;
        final Map<String, Integer> ingredients;
        final List<String> dependencies; // Items that need to be crafted first

        CraftRecipe(String output, int outputCount, boolean requiresTable, String[] grid, List<String> dependencies) {
            this.output = output;
            this.outputCount = outputCount;
            this.requiresTable = requiresTable;
            this.grid = grid;
            this.dependencies = dependencies != null ? dependencies : new ArrayList<>();
            this.ingredients = new HashMap<>();
            for (String s : grid) {
                if (s != null && !s.isEmpty()) {
                    ingredients.merge(s, 1, Integer::sum);
                }
            }
        }
    }

    // Crafting step for multi-step operations
    private static class CraftingStep {
        final String item;
        final int quantity;
        final CraftRecipe recipe;

        CraftingStep(String item, int quantity, CraftRecipe recipe) {
            this.item = item;
            this.quantity = quantity;
            this.recipe = recipe;
        }
    }

    @Override
    public boolean requiresPlayer() {
        return true;
    }

    @Override
    public String getCommandName() {
        return "craft_advanced";
    }

    @Override
    protected CommandResult execute(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        // Parse command parameters
        String itemId = params.has("item") ? params.get("item").getAsString() : "";
        if (itemId.isEmpty()) {
            return CommandResult.error("Missing required parameter: item");
        }

        int quantity = params.has("quantity") ? params.get("quantity").getAsInt() : 1;
        if (quantity <= 0) {
            return CommandResult.error("Quantity must be positive");
        }

        boolean useWorkbench = params.has("use_workbench") ? params.get("use_workbench").getAsBoolean() : false;

        // Normalize item ID
        if (!itemId.contains(":")) {
            itemId = "minecraft:" + itemId;
        }

        try {
            // Validate and plan the crafting operation
            List<CraftingStep> craftingPlan = planCraftingSequence(client, itemId, quantity, useWorkbench);
            if (craftingPlan == null) {
                return CommandResult.error("Unable to create crafting plan for " + itemId + ". Recipe may not exist or ingredients unavailable.");
            }

            // Execute the crafting sequence
            return executeCraftingSequence(client, craftingPlan, useWorkbench);

        } catch (Exception e) {
            logger.error("Crafting execution failed", e);
            return CommandResult.error("Crafting failed: " + e.getMessage());
        }
    }

    /**
     * Plans the complete crafting sequence including intermediate steps.
     */
    private List<CraftingStep> planCraftingSequence(Minecraft client, String targetItem, int quantity, boolean useWorkbench) {
        List<CraftingStep> plan = new ArrayList<>();
        Queue<String> dependencyQueue = new LinkedList<>();
        Set<String> plannedItems = new HashSet<>();

        dependencyQueue.add(targetItem);
        plannedItems.add(targetItem);

        // Resolve all dependencies using BFS
        while (!dependencyQueue.isEmpty()) {
            String currentItem = dependencyQueue.poll();
            CraftRecipe recipe = getRecipeDefinition(currentItem);

            if (recipe == null) {
                // Check if item is already in inventory
                if (countItemInInventory(client, currentItem) > 0) {
                    continue; // No need to craft
                }
                return null; // Cannot craft this item
            }

            // Validate workbench requirement
            if (recipe.requiresTable && !useWorkbench && !isWorkbenchOpen(client)) {
                return null; // Requires workbench but none available
            }

            // Calculate batches needed
            int batchesNeeded = calculateBatchesNeeded(currentItem, quantity, client);

            // Add crafting step
            plan.add(new CraftingStep(currentItem, batchesNeeded * recipe.outputCount, recipe));

            // Add dependencies to queue
            for (String dependency : recipe.dependencies) {
                if (!plannedItems.contains(dependency)) {
                    dependencyQueue.add(dependency);
                    plannedItems.add(dependency);
                }
            }

            // Also check ingredients
            for (String ingredient : recipe.ingredients.keySet()) {
                if (!plannedItems.contains(ingredient) && getRecipeDefinition(ingredient) != null) {
                    dependencyQueue.add(ingredient);
                    plannedItems.add(ingredient);
                }
            }
        }

        // Reverse the plan so dependencies are crafted first
        Collections.reverse(plan);
        return plan;
    }

    /**
     * Executes the complete crafting sequence.
     */
    private CommandResult executeCraftingSequence(Minecraft client, List<CraftingStep> plan, boolean useWorkbench) {
        JsonObject result = new JsonObject();
        int totalCrafted = 0;
        List<String> errors = new ArrayList<>();

        for (CraftingStep step : plan) {
            try {
                logger.info("Crafting step: {} x{}", step.item, step.quantity);

                // Validate ingredients before crafting
                Map<String, Integer> missingIngredients = validateIngredients(client, step.recipe, step.quantity / step.recipe.outputCount);
                if (!missingIngredients.isEmpty()) {
                    String errorMsg = "Missing ingredients for " + step.item + ": " + formatMissingIngredients(missingIngredients);
                    errors.add(errorMsg);
                    continue;
                }

                // Check workbench access if needed
                if (step.recipe.requiresTable && !ensureWorkbenchAccess(client, useWorkbench)) {
                    errors.add("Cannot access workbench for " + step.item);
                    continue;
                }

                // Execute crafting
                int crafted = performCrafting(client, step.recipe, step.quantity / step.recipe.outputCount);
                if (crafted > 0) {
                    totalCrafted += crafted;
                    logger.info("Successfully crafted {} x{}", step.item, crafted);
                } else {
                    errors.add("Failed to craft " + step.item);
                }

            } catch (Exception e) {
                logger.error("Error in crafting step for " + step.item, e);
                errors.add("Error crafting " + step.item + ": " + e.getMessage());
            }
        }

        result.addProperty("success", errors.isEmpty());
        result.addProperty("total_crafted", totalCrafted);
        result.addProperty("steps_completed", plan.size() - errors.size());
        result.addProperty("total_steps", plan.size());

        if (!errors.isEmpty()) {
            result.addProperty("errors", String.join("; ", errors));
        }

        return CommandResult.success(result);
    }

    /**
     * Validates that all required ingredients are available.
     */
    private Map<String, Integer> validateIngredients(Minecraft client, CraftRecipe recipe, int batches) {
        Map<String, Integer> missing = new HashMap<>();

        for (Map.Entry<String, Integer> entry : recipe.ingredients.entrySet()) {
            String ingredient = entry.getKey();
            int required = entry.getValue() * batches;
            int available = countItemInInventory(client, ingredient);

            if (available < required) {
                missing.put(ingredient, required - available);
            }
        }

        return missing;
    }

    /**
     * Ensures workbench access when required.
     */
    private boolean ensureWorkbenchAccess(Minecraft client, boolean useWorkbench) {
        if (isWorkbenchOpen(client)) {
            return true;
        }

        if (!useWorkbench) {
            return false; // Don't try to open workbench if not requested
        }

        if (client.player == null || client.level == null || client.gameMode == null) {
            return false;
        }

        BlockPos workbench = findReachableWorkbench(client);
        if (workbench == null) {
            logger.debug("No crafting table found within interaction range");
            return false;
        }

        // Server screen-open packets are applied on the Minecraft thread. This
        // synchronous legacy handler normally runs on the bridge worker thread,
        // so submit the click and wait here without blocking packet processing.
        // If invoked from the Minecraft thread, only accept an immediately-open
        // screen; waiting there would deadlock the client.
        try {
            if (client.isSameThread()) {
                interactWithWorkbench(client, workbench);
                return isWorkbenchOpen(client);
            }

            boolean accepted = client.submit(
                () -> interactWithWorkbench(client, workbench)
            ).get(2, TimeUnit.SECONDS);
            if (!accepted) {
                return false;
            }

            for (int poll = 0; poll < WORKBENCH_OPEN_POLLS; poll++) {
                if (isWorkbenchOpen(client)) {
                    return true;
                }
                Thread.sleep(WORKBENCH_OPEN_POLL_MS);
            }
            logger.warn("Crafting table interaction was accepted but its screen did not open");
            return false;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return false;
        } catch (Exception e) {
            logger.warn("Failed to open crafting table at {}", workbench, e);
            return false;
        }
    }

    private BlockPos findReachableWorkbench(Minecraft client) {
        BlockPos origin = client.player.blockPosition();
        Vec3 eyePosition = client.player.getEyePosition();
        BlockPos nearest = null;
        double nearestDistance = Double.MAX_VALUE;

        for (BlockPos candidate : BlockPos.withinManhattan(
                origin, WORKBENCH_SEARCH_RADIUS, WORKBENCH_SEARCH_RADIUS,
                WORKBENCH_SEARCH_RADIUS)) {
            if (!client.level.getBlockState(candidate).is(Blocks.CRAFTING_TABLE)) {
                continue;
            }
            double distance = eyePosition.distanceToSqr(Vec3.atCenterOf(candidate));
            if (distance <= WORKBENCH_SEARCH_RADIUS * WORKBENCH_SEARCH_RADIUS
                    && distance < nearestDistance) {
                nearest = candidate.immutable();
                nearestDistance = distance;
            }
        }
        return nearest;
    }

    private boolean interactWithWorkbench(Minecraft client, BlockPos workbench) {
        BlockHitResult hitResult = new BlockHitResult(
            Vec3.atCenterOf(workbench), Direction.UP, workbench, false);
        boolean accepted = client.gameMode.useItemOn(
            client.player, InteractionHand.MAIN_HAND, hitResult).consumesAction();
        if (accepted) {
            client.player.swing(InteractionHand.MAIN_HAND);
        }
        return accepted;
    }

    /**
     * Performs the actual crafting operation.
     */
    private int performCrafting(Minecraft client, CraftRecipe recipe, int batches) {
        int totalCrafted = 0;

        for (int batch = 0; batch < batches; batch++) {
            if (!validateIngredients(client, recipe, 1).isEmpty()) {
                break; // Stop if ingredients become unavailable
            }

            clearCraftingGrid(client, recipe.requiresTable);
            placeIngredients(client, recipe);

            // Small delay
            try { Thread.sleep(100); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }

            // Take result
            if (takeCraftingResult(client)) {
                totalCrafted += recipe.outputCount;
            } else {
                break;
            }
        }

        return totalCrafted;
    }

    // ================= Helper Methods =================

    private int calculateBatchesNeeded(String item, int totalQuantity, Minecraft client) {
        CraftRecipe recipe = getRecipeDefinition(item);
        if (recipe == null) return 0;

        int available = countItemInInventory(client, item);
        int needed = Math.max(0, totalQuantity - available);
        return (int) Math.ceil((double) needed / recipe.outputCount);
    }

    private boolean isWorkbenchOpen(Minecraft client) {
        return client.player.containerMenu instanceof net.minecraft.world.inventory.CraftingMenu;
    }

    private void clearCraftingGrid(Minecraft client, boolean isTable) {
        AbstractContainerMenu handler = client.player.containerMenu;
        int syncId = handler.containerId;
        int gridSize = isTable ? 9 : 4;
        int gridStart = 1;

        for (int i = gridStart; i <= gridSize; i++) {
            final int slot = i;
            client.execute(() -> {
                try {
                    client.gameMode.handleContainerInput(syncId, slot, 0, ContainerInput.QUICK_MOVE, client.player);
                } catch (Exception e) {
                    logger.warn("Failed to clear grid slot {}", slot, e);
                }
            });
            try { Thread.sleep(30); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
        }
    }

    private void placeIngredients(Minecraft client, CraftRecipe recipe) {
        AbstractContainerMenu handler = client.player.containerMenu;
        int syncId = handler.containerId;
        boolean isTable = recipe.requiresTable;
        int gridStart = 1;

        for (int i = 0; i < recipe.grid.length; i++) {
            String ingredient = recipe.grid[i];
            if (ingredient == null || ingredient.isEmpty()) continue;

            int gridSlot = gridStart + i;
            if (isTable && recipe.grid.length == 4) {
                // Map 2x2 recipe to 3x3 grid
                int row = i / 2;
                int col = i % 2;
                gridSlot = gridStart + (row * 3) + col;
            }

            int sourceSlot = findIngredientSlot(client, ingredient, isTable);
            if (sourceSlot != -1) {
                final int src = sourceSlot;
                final int dst = gridSlot;

                client.execute(() -> {
                    try {
                        // Pick up ingredient
                        client.gameMode.handleContainerInput(syncId, src, 0, ContainerInput.PICKUP, client.player);
                        // Place in grid
                        client.gameMode.handleContainerInput(syncId, dst, 1, ContainerInput.PICKUP, client.player);
                        // Return remainder
                        client.gameMode.handleContainerInput(syncId, src, 0, ContainerInput.PICKUP, client.player);
                    } catch (Exception e) {
                        logger.warn("Failed to place ingredient {} in slot {}", ingredient, dst, e);
                    }
                });

                try { Thread.sleep(50); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
            }
        }
    }

    private boolean takeCraftingResult(Minecraft client) {
        AbstractContainerMenu handler = client.player.containerMenu;
        int syncId = handler.containerId;

        try {
            client.execute(() -> {
                try {
                    client.gameMode.handleContainerInput(syncId, 0, 0, ContainerInput.QUICK_MOVE, client.player);
                } catch (Exception e) {
                    logger.warn("Failed to take crafting result", e);
                }
            });
            Thread.sleep(100);
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    private int findIngredientSlot(Minecraft client, String ingredient, boolean isTable) {
        AbstractContainerMenu handler = client.player.containerMenu;
        int startSlot = isTable ? 10 : 9;
        int endSlot = isTable ? 46 : 45;

        for (int i = startSlot; i < endSlot; i++) {
            ItemStack stack = handler.getSlot(i).getItem();
            if (stack.isEmpty()) continue;

            String id = BuiltInRegistries.ITEM.getKey(stack.getItem()).toString();
            if (matchesIngredient(ingredient, id)) {
                return i;
            }
        }
        return -1;
    }

    private boolean matchesIngredient(String ingredient, String itemId) {
        if (ingredient.equals(itemId)) return true;

        if (ingredient.equals("any_log")) {
            return itemId.endsWith("_log") || itemId.contains("_wood");
        }

        if (ingredient.equals("any_planks")) {
            return itemId.endsWith("_planks");
        }

        return false;
    }

    private int countItemInInventory(Minecraft client, String itemId) {
        Inventory inv = client.player.getInventory();
        int count = 0;

        for (int i = 0; i < inv.getContainerSize(); i++) {
            ItemStack stack = inv.getItem(i);
            if (stack.isEmpty()) continue;

            String id = BuiltInRegistries.ITEM.getKey(stack.getItem()).toString();
            if (matchesIngredient(itemId, id)) {
                count += stack.getCount();
            }
        }

        return count;
    }

    private String formatMissingIngredients(Map<String, Integer> missing) {
        List<String> parts = new ArrayList<>();
        for (Map.Entry<String, Integer> entry : missing.entrySet()) {
            parts.add(entry.getKey() + " (need " + entry.getValue() + " more)");
        }
        return String.join(", ", parts);
    }

    // ================= Recipe Definitions =================

    private CraftRecipe getRecipeDefinition(String recipeId) {
        switch (recipeId) {
            // Basic materials
            case "minecraft:oak_planks":
            case "minecraft:spruce_planks":
            case "minecraft:birch_planks":
            case "minecraft:jungle_planks":
            case "minecraft:acacia_planks":
            case "minecraft:dark_oak_planks":
            case "minecraft:mangrove_planks":
            case "minecraft:cherry_planks":
                return new CraftRecipe(recipeId, 4, false, new String[]{"any_log", null, null, null}, Collections.emptyList());

            case "minecraft:stick":
                return new CraftRecipe(recipeId, 4, false,
                    new String[]{"any_planks", null, "any_planks", null},
                    Arrays.asList("oak_planks")); // Assume oak planks for dependency

            case "minecraft:crafting_table":
                return new CraftRecipe(recipeId, 1, false,
                    new String[]{"any_planks", "any_planks", "any_planks", "any_planks"},
                    Arrays.asList("oak_planks"));

            // Tools requiring intermediate crafting
            case "minecraft:wooden_pickaxe":
                return new CraftRecipe(recipeId, 1, true, new String[]{
                    "any_planks", "any_planks", "any_planks",
                    null, "minecraft:stick", null,
                    null, "minecraft:stick", null
                }, Arrays.asList("oak_planks", "stick"));

            case "minecraft:stone_pickaxe":
                return new CraftRecipe(recipeId, 1, true, new String[]{
                    "minecraft:cobblestone", "minecraft:cobblestone", "minecraft:cobblestone",
                    null, "minecraft:stick", null,
                    null, "minecraft:stick", null
                }, Arrays.asList("stick"));

            case "minecraft:iron_pickaxe":
                return new CraftRecipe(recipeId, 1, true, new String[]{
                    "minecraft:iron_ingot", "minecraft:iron_ingot", "minecraft:iron_ingot",
                    null, "minecraft:stick", null,
                    null, "minecraft:stick", null
                }, Arrays.asList("stick"));

            case "minecraft:diamond_pickaxe":
                return new CraftRecipe(recipeId, 1, true, new String[]{
                    "minecraft:diamond", "minecraft:diamond", "minecraft:diamond",
                    null, "minecraft:stick", null,
                    null, "minecraft:stick", null
                }, Arrays.asList("stick"));

            // Furnace for smelting
            case "minecraft:furnace":
                return new CraftRecipe(recipeId, 1, true, new String[]{
                    "minecraft:cobblestone", "minecraft:cobblestone", "minecraft:cobblestone",
                    "minecraft:cobblestone", null, "minecraft:cobblestone",
                    "minecraft:cobblestone", "minecraft:cobblestone", "minecraft:cobblestone"
                }, Collections.emptyList());

            // Chest for storage
            case "minecraft:chest":
                return new CraftRecipe(recipeId, 1, true, new String[]{
                    "any_planks", "any_planks", "any_planks",
                    "any_planks", null, "any_planks",
                    "any_planks", "any_planks", "any_planks"
                }, Arrays.asList("oak_planks"));

            case "minecraft:torch":
                return new CraftRecipe(recipeId, 4, false,
                    new String[]{"minecraft:coal", null, "minecraft:stick", null},
                    Arrays.asList("stick"));

            default:
                return null;
        }
    }
}
