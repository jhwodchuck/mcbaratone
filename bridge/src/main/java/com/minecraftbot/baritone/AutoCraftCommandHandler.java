package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.Socket;
import java.util.*;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.item.ItemStack;

public class AutoCraftCommandHandler extends AsyncCommandHandler {

    private static final Logger LOGGER = LoggerFactory.getLogger(AutoCraftCommandHandler.class);

    // ================= Crafting Queue and Progress Tracking =================

    private static class CraftingQueue {
        final String itemId;
        final int quantity;
        int completed = 0;
        boolean inProgress = false;
        String status = "pending";

        CraftingQueue(String itemId, int quantity) {
            this.itemId = itemId;
            this.quantity = quantity;
        }
    }

    private final List<CraftingQueue> craftingQueue = new ArrayList<>();
    private boolean isProcessingQueue = false;

    // ================= CraftRecipe Definition =================

    private static class CraftRecipe {
        final String output;
        final int outputCount;
        final boolean requiresTable; // true = 3x3, false = 2x2
        final String[] grid; // 2x2 or 3x3 grid, null = empty slot, "any_log" = any log type
        final Map<String, Integer> ingredients; // Ingredient counts

        CraftRecipe(String output, int outputCount, boolean requiresTable, String[] grid) {
            this.output = output;
            this.outputCount = outputCount;
            this.requiresTable = requiresTable;
            this.grid = grid;
            this.ingredients = new java.util.HashMap<>();
            for (String s : grid) {
                if (s != null && !s.isEmpty()) {
                    ingredients.merge(s, 1, Integer::sum);
                }
            }
        }
    }

    private CraftRecipe getRecipeDefinition(String recipeId) {
        // Normalize recipe ID
        if (!recipeId.contains(":")) {
            recipeId = "minecraft:" + recipeId;
        }

        // Common early-game recipes
        switch (recipeId) {
            // Planks from logs
            case "minecraft:oak_planks":
            case "minecraft:spruce_planks":
            case "minecraft:birch_planks":
            case "minecraft:jungle_planks":
            case "minecraft:acacia_planks":
            case "minecraft:dark_oak_planks":
            case "minecraft:mangrove_planks":
            case "minecraft:cherry_planks":
                return new CraftRecipe(recipeId, 4, false, new String[] { "any_log", null, null, null });

            // Sticks
            case "minecraft:stick":
                return new CraftRecipe(recipeId, 4, false, new String[] {
                        "any_planks", null,
                        "any_planks", null
                });

            // Crafting Table
            case "minecraft:crafting_table":
                return new CraftRecipe(recipeId, 1, false, new String[] {
                        "any_planks", "any_planks",
                        "any_planks", "any_planks"
                });

            // Wooden Pickaxe (3x3)
            case "minecraft:wooden_pickaxe":
                return new CraftRecipe(recipeId, 1, true, new String[] {
                        "any_planks", "any_planks", "any_planks",
                        null, "minecraft:stick", null,
                        null, "minecraft:stick", null
                });

            // Stone Pickaxe (3x3)
            case "minecraft:stone_pickaxe":
                return new CraftRecipe(recipeId, 1, true, new String[] {
                        "minecraft:cobblestone", "minecraft:cobblestone", "minecraft:cobblestone",
                        null, "minecraft:stick", null,
                        null, "minecraft:stick", null
                });

            // Stone Tools
            case "minecraft:stone_axe":
                return new CraftRecipe(recipeId, 1, true, new String[] {
                        "minecraft:cobblestone", "minecraft:cobblestone", null,
                        "minecraft:cobblestone", "minecraft:stick", null,
                        null, "minecraft:stick", null
                });

            case "minecraft:stone_sword":
                return new CraftRecipe(recipeId, 1, true, new String[] {
                        null, "minecraft:cobblestone", null,
                        null, "minecraft:cobblestone", null,
                        null, "minecraft:stick", null
                });

            case "minecraft:stone_shovel":
                return new CraftRecipe(recipeId, 1, true, new String[] {
                        null, "minecraft:cobblestone", null,
                        null, "minecraft:stick", null,
                        null, "minecraft:stick", null
                });

            case "minecraft:stone_hoe":
                return new CraftRecipe(recipeId, 1, true, new String[] {
                        "minecraft:cobblestone", "minecraft:cobblestone", null,
                        null, "minecraft:stick", null,
                        null, "minecraft:stick", null
                });

            // Furnace
            case "minecraft:furnace":
                return new CraftRecipe(recipeId, 1, true, new String[] {
                        "minecraft:cobblestone", "minecraft:cobblestone", "minecraft:cobblestone",
                        "minecraft:cobblestone", null, "minecraft:cobblestone",
                        "minecraft:cobblestone", "minecraft:cobblestone", "minecraft:cobblestone"
                });

            case "minecraft:chest":
                return new CraftRecipe(recipeId, 1, true, new String[] {
                        "any_planks", "any_planks", "any_planks",
                        "any_planks", null, "any_planks",
                        "any_planks", "any_planks", "any_planks"
                });

            // Iron Tools
            case "minecraft:iron_pickaxe":
                return new CraftRecipe(recipeId, 1, true, new String[] {
                        "minecraft:iron_ingot", "minecraft:iron_ingot", "minecraft:iron_ingot",
                        null, "minecraft:stick", null,
                        null, "minecraft:stick", null
                });

            case "minecraft:iron_sword":
                return new CraftRecipe(recipeId, 1, true, new String[] {
                        null, "minecraft:iron_ingot", null,
                        null, "minecraft:iron_ingot", null,
                        null, "minecraft:stick", null
                });

            // Torch
            case "minecraft:torch":
                // any_coal: charcoal is interchangeable with coal here.
                return new CraftRecipe(recipeId, 4, false, new String[] {
                        "any_coal", null,
                        "minecraft:stick", null
                });

            default:
                return null;
        }
    }

    // ================= Recipe Discovery and Optimization =================

    private final Map<String, CraftRecipe> recipeCache = new HashMap<>();
    private final Map<String, Integer> craftingCosts = new HashMap<>();

    @Override
    public String getCommandName() {
        return "auto_craft";
    }

    @Override
    public CompletableFuture<CommandResult> execute(JsonObject params, Minecraft client, IBaritone baritone,
            Socket clientSocket) {
        // Support multiple modes: single item, queue items, get status, clear queue
        String action = params.has("action") ? params.get("action").getAsString() : "craft";

        switch (action) {
            case "craft":
                return handleCraftAction(params, client);
            case "queue":
                return handleQueueAction(params, client);
            case "status":
                return handleStatusAction();
            case "clear":
                return handleClearAction();
            case "discover":
                return handleDiscoverRecipes(client);
            case "optimize":
                return handleOptimizeCrafting(params, client);
            default:
                return CompletableFuture.completedFuture(CommandResult.error("Unknown action: " + action));
        }
    }

    private CompletableFuture<CommandResult> handleCraftAction(JsonObject params, Minecraft client) {
        String itemId = params.has("item") ? params.get("item").getAsString() : "";
        if (itemId.isEmpty()) {
            itemId = params.has("recipe_id") ? params.get("recipe_id").getAsString() : "";
        }

        if (itemId.isEmpty()) {
            return CompletableFuture.completedFuture(CommandResult.error("Missing item or recipe_id"));
        }

        int quantity = params.has("quantity") ? params.get("quantity").getAsInt() : 1;
        final String targetItem = itemId;

        return executeOnMainThread(client, () -> {
            if (client.player == null) {
                return CommandResult.error("Player not available");
            }

            // Define simple hardcoded recipes for the vertical slice
            Map<String, String[]> recipes = new HashMap<>();

            // Oak Planks (from Oak Log)
            recipes.put("minecraft:oak_planks", new String[] { "minecraft:oak_log", null, null, null });
            recipes.put("minecraft:spruce_planks", new String[] { "minecraft:spruce_log", null, null, null });
            recipes.put("minecraft:birch_planks", new String[] { "minecraft:birch_log", null, null, null });
            recipes.put("minecraft:jungle_planks", new String[] { "minecraft:jungle_log", null, null, null });
            recipes.put("minecraft:acacia_planks", new String[] { "minecraft:acacia_log", null, null, null });
            recipes.put("minecraft:dark_oak_planks", new String[] { "minecraft:dark_oak_log", null, null, null });

            // Sticks
            String[] stickRecipe = new String[9];
            stickRecipe[0] = "planks";
            stickRecipe[3] = "planks";
            recipes.put("minecraft:stick", stickRecipe);

            // Crafting Table
            recipes.put("minecraft:crafting_table", new String[] { "planks", "planks", "planks", "planks" });

            // Wooden Pickaxe
            String[] woodPick = new String[9];
            woodPick[0] = "planks";
            woodPick[1] = "planks";
            woodPick[2] = "planks";
            woodPick[4] = "minecraft:stick";
            woodPick[7] = "minecraft:stick";
            recipes.put("minecraft:wooden_pickaxe", woodPick);

            // Wooden Sword
            String[] woodSword = new String[9];
            woodSword[1] = "planks";
            woodSword[4] = "planks";
            woodSword[7] = "minecraft:stick";
            recipes.put("minecraft:wooden_sword", woodSword);

            // Wooden Axe
            String[] woodAxe = new String[9];
            woodAxe[0] = "planks";
            woodAxe[1] = "planks";
            woodAxe[3] = "planks";
            woodAxe[4] = "minecraft:stick";
            woodAxe[7] = "minecraft:stick";
            recipes.put("minecraft:wooden_axe", woodAxe);

            // Wooden Shovel
            String[] woodShovel = new String[9];
            woodShovel[1] = "planks";
            woodShovel[4] = "minecraft:stick";
            woodShovel[7] = "minecraft:stick";
            recipes.put("minecraft:wooden_shovel", woodShovel);

            // Chest
            String[] chestRecipe = new String[9];
            chestRecipe[0] = "planks";
            chestRecipe[1] = "planks";
            chestRecipe[2] = "planks";
            chestRecipe[3] = "planks";
            chestRecipe[5] = "planks";
            chestRecipe[6] = "planks";
            chestRecipe[7] = "planks";
            chestRecipe[8] = "planks";
            recipes.put("minecraft:chest", chestRecipe);

            String[] ingredients = recipes.get(targetItem);

            if (ingredients == null) {
                return CommandResult.error("Recipe not found (Hardcoded vertical slice only)");
            }

            int perCraft = (targetItem.endsWith("_planks") || targetItem.equals("minecraft:stick")) ? 4 : 1;
            int crafted = 0;
            for (int i = 0; i < quantity; i += perCraft) {
                if (!performCrafting(client, ingredients))
                    break;
                crafted += perCraft;
                try {
                    Thread.sleep(50);
                } catch (Exception e) {
                }
            }

            JsonObject data = new JsonObject();
            data.addProperty("crafted", crafted > 0);
            data.addProperty("item", targetItem);
            data.addProperty("count", crafted);
            return CommandResult.success(data);
        });
    }

    // ================= New Crafting Methods =================

    private CommandResult startQueueProcessing(Minecraft client) {
        if (isProcessingQueue) {
            JsonObject data = new JsonObject();
            data.addProperty("queued", true);
            data.addProperty("message", "Already processing queue");
            return CommandResult.success(data);
        }

        // Start processing in background
        new Thread(() -> processQueue(client)).start();

        JsonObject data = new JsonObject();
        data.addProperty("queued", true);
        data.addProperty("queueSize", craftingQueue.size());
        return CommandResult.success(data);
    }

    private void processQueue(Minecraft client) {
        isProcessingQueue = true;
        try {
            while (!craftingQueue.isEmpty()) {
                CraftingQueue item = craftingQueue.get(0);
                item.status = "in_progress";
                item.inProgress = true;

                CraftRecipe recipe = getRecipeDefinition(item.itemId);
                if (recipe == null) {
                    item.status = "error: no recipe";
                    continue;
                }

                // Craft one batch at a time
                int remaining = item.quantity - item.completed;
                int batchSize = Math.min(remaining, recipe.outputCount);
                if (batchSize > 0) {
                    CommandResult result = craftItemsSync(client, item.itemId, batchSize, recipe);
                    if (result.isSuccess()) {
                        item.completed += batchSize;
                        if (item.completed >= item.quantity) {
                            item.status = "completed";
                            craftingQueue.remove(0);
                        }
                    } else {
                        item.status = "error: " + result.getErrorMessage();
                        break; // Stop on error
                    }
                }

                Thread.sleep(100); // Small delay between batches
            }
        } catch (Exception e) {
            LOGGER.error("Queue processing error", e);
        } finally {
            isProcessingQueue = false;
        }
    }

    private CommandResult craftItems(Minecraft client, String itemId, int quantity, CraftRecipe recipe) {
        int crafted = 0;

        for (int i = 0; i < quantity; i += recipe.outputCount) {
            if (!hasIngredients(client, recipe)) {
                if (crafted == 0) {
                    return CommandResult.error("Missing ingredients for " + itemId);
                }
                break;
            }

            boolean success = performCrafting(client, recipe);
            if (success) {
                crafted += recipe.outputCount;
            } else {
                break;
            }
        }

        JsonObject data = new JsonObject();
        data.addProperty("crafted", crafted);
        data.addProperty("item", itemId);
        data.addProperty("quantity", quantity);
        return CommandResult.success(data);
    }

    private CommandResult craftItemsSync(Minecraft client, String itemId, int quantity, CraftRecipe recipe) {
        // Synchronous version for queue processing
        int crafted = 0;

        for (int i = 0; i < quantity; i += recipe.outputCount) {
            if (!hasIngredients(client, recipe)) {
                break;
            }

            boolean success = performCrafting(client, recipe);
            if (success) {
                crafted += recipe.outputCount;
            } else {
                break;
            }
        }

        if (crafted > 0) {
            JsonObject data = new JsonObject();
            data.addProperty("crafted", crafted);
            return CommandResult.success(data);
        } else {
            return CommandResult.error("Failed to craft");
        }
    }

    private boolean hasIngredients(Minecraft client, CraftRecipe recipe) {
        for (Map.Entry<String, Integer> entry : recipe.ingredients.entrySet()) {
            String ingredient = entry.getKey();
            int needed = entry.getValue();
            if (countItemInInventory(client, ingredient) < needed) {
                return false;
            }
        }
        return true;
    }

    private int countItemInInventory(Minecraft client, String itemId) {
        Inventory inv = client.player.getInventory();
        int count = 0;

        for (int i = 0; i < inv.getContainerSize(); i++) {
            ItemStack stack = inv.getItem(i);
            if (stack.isEmpty())
                continue;

            String stackId = BuiltInRegistries.ITEM.getKey(stack.getItem()).toString();

            // Shared matcher: see CraftCommandHandler.matchesSelector. The
            // inline copies here and in AdvancedCraftCommandHandler are why
            // charcoal was never accepted for torches.
            if (CraftCommandHandler.matchesSelector(itemId, stackId)) {
                count += stack.getCount();
            }
        }

        return count;
    }

    private boolean hasItemInInventory(Minecraft client, String itemId, int minCount) {
        return countItemInInventory(client, itemId) >= minCount;
    }

    private boolean isInQueue(String itemId) {
        return craftingQueue.stream().anyMatch(q -> q.itemId.equals(itemId));
    }

    private List<String> resolveDependencies(String itemId) {
        List<String> deps = new ArrayList<>();
        CraftRecipe recipe = getRecipeDefinition(itemId);
        if (recipe != null) {
            for (String ingredient : recipe.ingredients.keySet()) {
                // Add dependencies for complex items
                if (ingredient.equals("minecraft:stick")) {
                    deps.add("minecraft:stick");
                } else if (ingredient.equals("any_planks")) {
                    // Assume oak planks for simplicity
                    deps.add("minecraft:oak_planks");
                }
            }
        }
        return deps;
    }

    private boolean performCrafting(Minecraft client, CraftRecipe recipe) {
        AbstractContainerMenu handler = client.player.containerMenu;
        int syncId = handler.containerId;

        boolean isTable = handler instanceof net.minecraft.world.inventory.CraftingMenu;
        boolean isPlayer = handler instanceof net.minecraft.world.inventory.InventoryMenu;

        if (!isTable && !isPlayer)
            return false;

        int gridStart = 1;
        int gridSize = isTable ? 9 : 4;

        if (recipe.grid.length > 4 && !isTable) {
            LOGGER.warn("Recipe requires 3x3 grid but player inventory is 2x2");
            return false;
        }

        for (int i = 0; i < recipe.grid.length; i++) {
            if (i >= gridSize)
                break;

            String ingredient = recipe.grid[i];
            if (ingredient == null || ingredient.isEmpty())
                continue;

            int sourceSlot = findIngredientSlot(client, ingredient);
            if (sourceSlot == -1) {
                LOGGER.warn("Missing ingredient: {}", ingredient);
                return false;
            }

            int gridSlot = gridStart + i;
            if (isTable && recipe.grid.length == 4) {
                int row = i / 2;
                int col = i % 2;
                gridSlot = gridStart + (row * 3) + col;
            }

            try {
                if (client.gameMode != null) {
                    client.gameMode.handleContainerInput(syncId, sourceSlot, 0, ContainerInput.PICKUP, client.player);
                    client.gameMode.handleContainerInput(syncId, gridSlot, 1, ContainerInput.PICKUP, client.player);
                    client.gameMode.handleContainerInput(syncId, sourceSlot, 0, ContainerInput.PICKUP, client.player);
                }
            } catch (Exception e) {
                LOGGER.error("Crafting click failed", e);
                return false;
            }
        }

        // Take result
        try {
            client.gameMode.handleContainerInput(syncId, 0, 0, ContainerInput.QUICK_MOVE, client.player);
        } catch (Exception e) {
            return false;
        }

        return true;
    }

    private boolean performCrafting(Minecraft client, String[] ingredients) {
        if (client.player == null)
            return false;

        AbstractContainerMenu handler = client.player.containerMenu;
        int syncId = handler.containerId;

        boolean isTable = handler instanceof net.minecraft.world.inventory.CraftingMenu;
        boolean isPlayer = handler instanceof net.minecraft.world.inventory.InventoryMenu;

        if (!isTable && !isPlayer)
            return false;

        int gridStart = 1;
        int gridSize = isTable ? 9 : 4;

        if (ingredients.length > 4 && !isTable) {
            LOGGER.warn("Recipe requires 3x3 grid but player inventory is 2x2");
            return false;
        }

        for (int i = 0; i < ingredients.length; i++) {
            if (i >= gridSize)
                break;

            String ingredient = ingredients[i];
            if (ingredient == null)
                continue;

            int sourceSlot = findIngredientSlot(client, ingredient);

            if (sourceSlot == -1) {
                LOGGER.warn("Missing ingredient: {}", ingredient);
                return false;
            }

            // Logic to calculate grid slot mapping
            int gridSlot = gridStart + i;

            if (isTable && ingredients.length == 4) {
                int row = i / 2;
                int col = i % 2;
                gridSlot = gridStart + (row * 3) + col;
            }

            final int src = sourceSlot;
            final int dst = gridSlot;

            try {
                // PICKUP 1 item from source
                if (client.gameMode != null) {
                    client.gameMode.handleContainerInput(syncId, src, 0, ContainerInput.PICKUP, client.player);
                    // Place 1 item in grid (Right Click)
                    client.gameMode.handleContainerInput(syncId, dst, 1, ContainerInput.PICKUP, client.player);
                    // Return remainder to source
                    client.gameMode.handleContainerInput(syncId, src, 0, ContainerInput.PICKUP, client.player);
                }
            } catch (Exception e) {
                LOGGER.error("Crafting click failed", e);
                return false;
            }
        }

        // Take result
        try {
            client.gameMode.handleContainerInput(syncId, 0, 0, ContainerInput.QUICK_MOVE, client.player);
        } catch (Exception e) {
            return false;
        }

        return true;
    }

    private int findIngredientSlot(Minecraft client, String ingredient) {
        if (client.player == null)
            return -1;

        AbstractContainerMenu handler = client.player.containerMenu;
        boolean isTable = handler instanceof net.minecraft.world.inventory.CraftingMenu;

        int startSlot = isTable ? 10 : 9;
        int endSlot = isTable ? 46 : 45;

        for (int i = startSlot; i < endSlot; i++) {
            ItemStack stack = handler.getSlot(i).getItem();
            if (stack.isEmpty())
                continue;

            String id = BuiltInRegistries.ITEM.getKey(stack.getItem()).toString();

            if (ingredient.equals("planks")) {
                if (id.endsWith("_planks"))
                    return i;
            } else {
                if (id.equals(ingredient))
                    return i;
            }
        }
        return -1;
    }

    // ================= Queue Management Methods =================

    private CompletableFuture<CommandResult> handleQueueAction(JsonObject params, Minecraft client) {
        String itemId = params.has("item") ? params.get("item").getAsString() : "";
        if (itemId.isEmpty()) {
            return CompletableFuture.completedFuture(CommandResult.error("Missing item for queue"));
        }

        int quantity = params.has("quantity") ? params.get("quantity").getAsInt() : 1;

        synchronized (craftingQueue) {
            craftingQueue.add(new CraftingQueue(itemId, quantity));
        }

        JsonObject data = new JsonObject();
        data.addProperty("queued", true);
        data.addProperty("item", itemId);
        data.addProperty("quantity", quantity);
        data.addProperty("queue_size", craftingQueue.size());
        return CompletableFuture.completedFuture(CommandResult.success(data));
    }

    private CompletableFuture<CommandResult> handleStatusAction() {
        JsonObject data = new JsonObject();
        data.addProperty("is_processing", isProcessingQueue);

        JsonArray queueArray = new JsonArray();
        synchronized (craftingQueue) {
            for (CraftingQueue q : craftingQueue) {
                JsonObject item = new JsonObject();
                item.addProperty("item", q.itemId);
                item.addProperty("quantity", q.quantity);
                item.addProperty("completed", q.completed);
                item.addProperty("status", q.status);
                queueArray.add(item);
            }
        }
        data.add("queue", queueArray);
        data.addProperty("queue_size", craftingQueue.size());

        return CompletableFuture.completedFuture(CommandResult.success(data));
    }

    private CompletableFuture<CommandResult> handleClearAction() {
        synchronized (craftingQueue) {
            int cleared = craftingQueue.size();
            craftingQueue.clear();
            isProcessingQueue = false;

            JsonObject data = new JsonObject();
            data.addProperty("cleared", true);
            data.addProperty("items_cleared", cleared);
            return CompletableFuture.completedFuture(CommandResult.success(data));
        }
    }

    // ================= Phase 4: Enhanced Crafting Features =================

    private CompletableFuture<CommandResult> handleDiscoverRecipes(Minecraft client) {
        return executeOnMainThread(client, () -> {
            // Discover available recipes from inventory items
            Set<String> craftableItems = discoverCraftableItems(client);

            JsonObject data = new JsonObject();
            JsonArray recipes = new JsonArray();
            for (String item : craftableItems) {
                CraftRecipe recipe = getRecipeDefinition(item);
                if (recipe != null) {
                    JsonObject recipeObj = new JsonObject();
                    recipeObj.addProperty("item", item);
                    recipeObj.addProperty("output_count", recipe.outputCount);
                    recipeObj.addProperty("requires_table", recipe.requiresTable);
                    recipes.add(recipeObj);
                }
            }
            data.add("discovered_recipes", recipes);
            return CommandResult.success(data);
        });
    }

    private CompletableFuture<CommandResult> handleOptimizeCrafting(JsonObject params, Minecraft client) {
        String targetItem = params.has("target") ? params.get("target").getAsString() : "";
        int targetQuantity = params.has("quantity") ? params.get("quantity").getAsInt() : 1;

        if (targetItem.isEmpty()) {
            return CompletableFuture.completedFuture(CommandResult.error("Missing target item"));
        }

        return executeOnMainThread(client, () -> {
            CraftingPlan plan = optimizeCraftingPlan(client, targetItem, targetQuantity);

            JsonObject data = new JsonObject();
            if (plan != null) {
                data.addProperty("optimized", true);
                data.addProperty("target_item", targetItem);
                data.addProperty("target_quantity", targetQuantity);
                data.addProperty("total_cost", plan.totalCost);
                data.addProperty("steps", plan.steps.size());

                JsonArray steps = new JsonArray();
                for (CraftingStep step : plan.steps) {
                    JsonObject stepObj = new JsonObject();
                    stepObj.addProperty("item", step.item);
                    stepObj.addProperty("quantity", step.quantity);
                    stepObj.addProperty("cost", step.cost);
                    steps.add(stepObj);
                }
                data.add("crafting_steps", steps);
            } else {
                data.addProperty("optimized", false);
                data.addProperty("error", "Could not create crafting plan");
            }

            return CommandResult.success(data);
        });
    }

    private Set<String> discoverCraftableItems(Minecraft client) {
        Set<String> craftable = new HashSet<>();
        Inventory inv = client.player.getInventory();

        // Check all inventory items to see what can be crafted
        for (int i = 0; i < inv.getContainerSize(); i++) {
            ItemStack stack = inv.getItem(i);
            if (stack.isEmpty())
                continue;

            String itemId = BuiltInRegistries.ITEM.getKey(stack.getItem()).toString();
            int count = stack.getCount();

            // Check common crafting patterns
            if (itemId.endsWith("_log") || itemId.endsWith("_planks")) {
                if (count >= 1)
                    craftable.add("minecraft:stick");
                if (count >= 4)
                    craftable.add("minecraft:crafting_table");
            }

            if (itemId.equals("minecraft:cobblestone")) {
                if (count >= 8)
                    craftable.add("minecraft:furnace");
            }
        }

        return craftable;
    }

    private static class CraftingStep {
        final String item;
        final int quantity;
        final int cost;

        CraftingStep(String item, int quantity, int cost) {
            this.item = item;
            this.quantity = quantity;
            this.cost = cost;
        }
    }

    private static class CraftingPlan {
        final List<CraftingStep> steps = new ArrayList<>();
        int totalCost = 0;
    }

    private CraftingPlan optimizeCraftingPlan(Minecraft client, String targetItem, int quantity) {
        CraftingPlan plan = new CraftingPlan();

        // Calculate dependencies and optimize crafting order
        Map<String, Integer> required = calculateRequirements(targetItem, quantity);
        Map<String, Integer> inventory = getInventoryCounts(client);

        // Simple optimization: craft items with most dependencies first
        List<String> order = topologicalSort(required.keySet());

        for (String item : order) {
            int needed = required.get(item);
            int available = inventory.getOrDefault(item, 0);
            int toCraft = Math.max(0, needed - available);

            if (toCraft > 0) {
                CraftRecipe recipe = getRecipeDefinition(item);
                if (recipe == null) {
                    return null; // Cannot craft this item
                }

                int batches = (int) Math.ceil((double) toCraft / recipe.outputCount);
                int cost = calculateCraftingCost(recipe, batches);

                plan.steps.add(new CraftingStep(item, toCraft, cost));
                plan.totalCost += cost;
            }
        }

        return plan;
    }

    private Map<String, Integer> calculateRequirements(String item, int quantity) {
        Map<String, Integer> requirements = new HashMap<>();
        Deque<Map.Entry<String, Integer>> queue = new LinkedList<>();

        CraftRecipe recipe = getRecipeDefinition(item);
        if (recipe == null)
            return requirements;

        int batches = (int) Math.ceil((double) quantity / recipe.outputCount);
        for (Map.Entry<String, Integer> entry : recipe.ingredients.entrySet()) {
            requirements.put(entry.getKey(), entry.getValue() * batches);
            queue.add(new AbstractMap.SimpleEntry<>(entry.getKey(), entry.getValue() * batches));
        }

        // Resolve nested dependencies (simplified - only one level for now)
        while (!queue.isEmpty()) {
            Map.Entry<String, Integer> req = queue.poll();
            CraftRecipe subRecipe = getRecipeDefinition(req.getKey());
            if (subRecipe != null) {
                int subBatches = (int) Math.ceil((double) req.getValue() / subRecipe.outputCount);
                for (Map.Entry<String, Integer> subEntry : subRecipe.ingredients.entrySet()) {
                    requirements.put(subEntry.getKey(),
                            requirements.getOrDefault(subEntry.getKey(), 0) + subEntry.getValue() * subBatches);
                }
            }
        }

        return requirements;
    }

    private Map<String, Integer> getInventoryCounts(Minecraft client) {
        Map<String, Integer> counts = new HashMap<>();
        Inventory inv = client.player.getInventory();

        for (int i = 0; i < inv.getContainerSize(); i++) {
            ItemStack stack = inv.getItem(i);
            if (!stack.isEmpty()) {
                String itemId = BuiltInRegistries.ITEM.getKey(stack.getItem()).toString();
                counts.put(itemId, counts.getOrDefault(itemId, 0) + stack.getCount());
            }
        }

        return counts;
    }

    private List<String> topologicalSort(Set<String> items) {
        // Simple topological sort for crafting dependencies
        List<String> result = new ArrayList<>();
        Set<String> visited = new HashSet<>();
        Set<String> visiting = new HashSet<>();

        for (String item : items) {
            if (!visited.contains(item)) {
                topologicalSortVisit(item, items, visited, visiting, result);
            }
        }

        return result;
    }

    private void topologicalSortVisit(String item, Set<String> allItems, Set<String> visited,
            Set<String> visiting, List<String> result) {
        if (visiting.contains(item))
            return; // Cycle detected, skip
        if (visited.contains(item))
            return;

        visiting.add(item);

        // Add dependencies
        CraftRecipe recipe = getRecipeDefinition(item);
        if (recipe != null) {
            for (String ingredient : recipe.ingredients.keySet()) {
                if (allItems.contains(ingredient)) {
                    topologicalSortVisit(ingredient, allItems, visited, visiting, result);
                }
            }
        }

        visiting.remove(item);
        visited.add(item);
        result.add(item);
    }

    private int calculateCraftingCost(CraftRecipe recipe, int batches) {
        // Simple cost calculation based on number of ingredients
        int cost = 0;
        for (int count : recipe.ingredients.values()) {
            cost += count * batches;
        }
        return cost;
    }
}
