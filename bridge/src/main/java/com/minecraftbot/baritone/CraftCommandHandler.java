package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.gui.screen.ingame.CraftingScreen;
import net.minecraft.entity.player.PlayerInventory;
import net.minecraft.item.ItemStack;
import net.minecraft.registry.Registries;
import net.minecraft.screen.slot.SlotActionType;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.Socket;
import java.util.Map;

public class CraftCommandHandler implements CommandHandler {

    private static final Logger LOGGER = LoggerFactory.getLogger(CraftCommandHandler.class);

    @Override
    public CommandResult handle(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        if (client.player == null || client.interactionManager == null) {
            return CommandResult.error("Player not available");
        }
        
        // Accept both 'recipe', 'item', and 'recipe_id' parameters
        String recipeId = null;
        if (params.has("recipe")) {
            recipeId = params.get("recipe").getAsString();
        } else if (params.has("item")) {
            recipeId = params.get("item").getAsString(); 
        } else if (params.has("recipe_id")) {
            recipeId = params.get("recipe_id").getAsString();
        }
        
        if (recipeId == null || recipeId.isEmpty()) {
            return CommandResult.error("Missing recipe/item argument");
        }
        
        int count = params.has("count") ? params.get("count").getAsInt() : 1;
        
        // Normalize recipe ID
        if (!recipeId.contains(":")) {
            recipeId = "minecraft:" + recipeId;
        }
        
        // Get recipe definition
        CraftRecipe recipe = getRecipeDefinition(recipeId);
        if (recipe == null) {
            JsonObject err = new JsonObject();
            err.addProperty("error", "Unknown recipe: " + recipeId);
            err.addProperty("note", "Recipe not in hardcoded list. Add to getRecipeDefinition().");
            return CommandResult.success(err); // Return as success with error field to match old behavior or change to error? 
            // Old behavior: data.addProperty("error", ...) then return success(data) sometimes? 
            // Actually old behavior returns data with error. CommandHandler usually returns CommandResult.error for hard errors.
            // Let's return CommandResult.error but maybe with data?
            // The dispatcher wraps CommandResult.error into {status: error, error: msg}.
            // So return CommandResult.error("Unknown recipe: " + recipeId);
        }
        
        // Check if we need a crafting table
        boolean hasCraftingTable = client.player.currentScreenHandler instanceof net.minecraft.screen.CraftingScreenHandler;
        if (recipe.requiresTable && !hasCraftingTable) {
            return CommandResult.error("Recipe requires crafting table but none is open");
        }
        
        int crafted = 0;
        int syncId = client.player.currentScreenHandler.syncId;
        JsonObject data = new JsonObject();
        
        // Craft the requested count
        for (int i = 0; i < count; i += recipe.outputCount) {
            try {
                // Check ingredients
                if (!hasIngredients(client, recipe)) {
                    if (crafted == 0) {
                        return CommandResult.error("Missing ingredients for " + recipeId + " (Have " + dumpIngredients(client, recipe) + ")");
                    }
                    break;
                }
                
                // Clear crafting grid first
                clearCraftingGrid(client, syncId, recipe.requiresTable);
                Thread.sleep(100); // Increased delay
                
                // Place ingredients in grid
                String result = placeIngredients(client, syncId, recipe);
                if (result != null) {
                    return CommandResult.error("Failed to place ingredients: " + result);
                }
                Thread.sleep(150); // Increased delay
                
                // Click output slot (slot 0) to craft
                client.execute(() -> {
                    client.interactionManager.clickSlot(syncId, 0, 0, SlotActionType.QUICK_MOVE, client.player);
                });
                Thread.sleep(150); // Increased delay
                
                crafted += recipe.outputCount;
            } catch (Exception e) {
                LOGGER.error("Crafting error", e);
                if (crafted == 0) {
                    return CommandResult.error("Crafting failed: " + e.getMessage());
                }
                break;
            }
        }
        
        data.addProperty("crafted", crafted > 0);
        data.addProperty("count", crafted);
        data.addProperty("recipe", recipeId);
        if (crafted > 0) {
            data.addProperty("status", "ok");
        }
        
        return CommandResult.success(data);
    }

    @Override
    public String getCommandName() {
        return "craft";
    }

    // ================= Helper Methods =================

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
                return new CraftRecipe(recipeId, 4, false, new String[]{"any_log", null, null, null});
            
            // Sticks
            case "minecraft:stick":
                return new CraftRecipe(recipeId, 4, false, new String[]{
                    "any_planks", null,
                    "any_planks", null
                });
            
            // Crafting Table
            case "minecraft:crafting_table":
                return new CraftRecipe(recipeId, 1, false, new String[]{
                    "any_planks", "any_planks",
                    "any_planks", "any_planks"
                });
            
            // Wooden Pickaxe (3x3)
            case "minecraft:wooden_pickaxe":
                return new CraftRecipe(recipeId, 1, true, new String[]{
                    "any_planks", "any_planks", "any_planks",
                    null, "minecraft:stick", null,
                    null, "minecraft:stick", null
                });
            
            // Stone Pickaxe (3x3)
            case "minecraft:stone_pickaxe":
                return new CraftRecipe(recipeId, 1, true, new String[]{
                    "minecraft:cobblestone", "minecraft:cobblestone", "minecraft:cobblestone",
                    null, "minecraft:stick", null,
                    null, "minecraft:stick", null
                });
            
            // Stone Axe (3x3)
            case "minecraft:stone_axe":
                return new CraftRecipe(recipeId, 1, true, new String[]{
                    "minecraft:cobblestone", "minecraft:cobblestone", null,
                    "minecraft:cobblestone", "minecraft:stick", null,
                    null, "minecraft:stick", null
                });
            
            // Stone Sword (3x3)
            case "minecraft:stone_sword":
                return new CraftRecipe(recipeId, 1, true, new String[]{
                    null, "minecraft:cobblestone", null,
                    null, "minecraft:cobblestone", null,
                    null, "minecraft:stick", null
                });
            
            // Furnace (3x3)
            case "minecraft:furnace":
                return new CraftRecipe(recipeId, 1, true, new String[]{
                    "minecraft:cobblestone", "minecraft:cobblestone", "minecraft:cobblestone",
                    "minecraft:cobblestone", null, "minecraft:cobblestone",
                    "minecraft:cobblestone", "minecraft:cobblestone", "minecraft:cobblestone"
                });
            
            // Chest (3x3)
            case "minecraft:chest":
                return new CraftRecipe(recipeId, 1, true, new String[]{
                    "any_planks", "any_planks", "any_planks",
                    "any_planks", null, "any_planks",
                    "any_planks", "any_planks", "any_planks"
                });
            
            default:
                return null;
        }
    }
    
    private String dumpIngredients(MinecraftClient client, CraftRecipe recipe) {
        StringBuilder sb = new StringBuilder();
        Map<String, Integer> required = new java.util.HashMap<>();
        for (String s : recipe.grid) {
            if (s != null && !s.isEmpty()) {
                required.merge(s, 1, Integer::sum);
            }
        }
        for (String item : required.keySet()) {
            sb.append(item).append(":").append(countItemInInventory(client, item)).append(" ");
        }
        return sb.toString().trim();
    }

    private boolean hasIngredients(MinecraftClient client, CraftRecipe recipe) {
        Map<String, Integer> required = new java.util.HashMap<>();
        for (String s : recipe.grid) {
            if (s != null && !s.isEmpty()) {
                required.merge(s, 1, Integer::sum);
            }
        }
        
        for (Map.Entry<String, Integer> entry : required.entrySet()) {
            String item = entry.getKey();
            int needed = entry.getValue();
            int have = countItemInInventory(client, item);
            if (have < needed) {
                return false;
            }
        }
        return true;
    }
    
    private int countItemInInventory(MinecraftClient client, String itemId) {
        PlayerInventory inv = client.player.getInventory();
        int count = 0;
        
        for (int i = 0; i < inv.size(); i++) {
            ItemStack stack = inv.getStack(i);
            if (stack.isEmpty()) continue;
            
            String stackId = Registries.ITEM.getId(stack.getItem()).toString();
            
            if (itemId.equals("any_log")) {
                if (stackId.endsWith("_log") || stackId.contains("_wood")) {
                    count += stack.getCount();
                }
            } else if (itemId.equals("any_planks")) {
                if (stackId.endsWith("_planks")) {
                    count += stack.getCount();
                }
            } else if (itemId.equals(stackId)) {
                count += stack.getCount();
            }
        }
        
        if (count == 0) {
            LOGGER.warn("countItemInInventory: Found 0 of {}, but caller might think otherwise", itemId);
        }
        return count;
    }
    
    private int findItemSlot(MinecraftClient client, String itemId, int syncId, boolean isTable) {
        // ALWAYS check cursor stack first during multi-step placement
        ItemStack cursor = client.player.currentScreenHandler.getCursorStack();
        if (!cursor.isEmpty()) {
            String cursorId = Registries.ITEM.getId(cursor.getItem()).toString();
            if (itemId.equals(cursorId) || 
               (itemId.equals("any_log") && (cursorId.endsWith("_log") || cursorId.contains("_wood"))) ||
               (itemId.equals("any_planks") && cursorId.endsWith("_planks"))) {
                LOGGER.info("  FOUND {} in CURSOR stack", itemId);
                return -2; // Special value for cursor
            }
        }

        PlayerInventory inv = client.player.getInventory();
        LOGGER.info("findItemSlot: Searching for {}", itemId);
        
        for (int i = 0; i < 36; i++) {
            ItemStack stack = inv.getStack(i);
            if (stack.isEmpty()) continue;
            
            String stackId = Registries.ITEM.getId(stack.getItem()).toString();
            
            if (itemId.equals("any_log")) {
                if (stackId.endsWith("_log") || stackId.contains("_wood")) {
                    int dst = screenSlotFromInvSlot(i, isTable);
                    LOGGER.info("  FOUND any_log ({}) in inv {} -> screen {}", stackId, i, dst);
                    return dst;
                }
            } else if (itemId.equals("any_planks")) {
                if (stackId.endsWith("_planks")) {
                    int dst = screenSlotFromInvSlot(i, isTable);
                    LOGGER.info("  FOUND any_planks ({}) in inv {} -> screen {}", stackId, i, dst);
                    return dst;
                }
            } else if (itemId.equals(stackId)) {
                int dst = screenSlotFromInvSlot(i, isTable);
                LOGGER.info("  FOUND {} in inv {} -> screen {}", stackId, i, dst);
                return dst;
            }
        }
        
        LOGGER.warn("findItemSlot: FAILED to find {}", itemId);
        return -1;
    }
    
    private int screenSlotFromInvSlot(int invSlot, boolean isTable) {
        if (isTable) {
            if (invSlot < 9) {
                return invSlot + 37; // Hotbar
            } else {
                return invSlot + 1; // Main inventory
            }
        } else {
            if (invSlot < 9) {
                return invSlot + 36; // Hotbar
            } else {
                return invSlot; // Main inventory
            }
        }
    }
    
    private void clearCraftingGrid(MinecraftClient client, int syncId, boolean isTable) {
        int gridSize = isTable ? 9 : 4;
        int gridStart = 1; // Slot 0 is output, grid starts at 1
        
        for (int i = gridStart; i <= gridSize; i++) {
            final int slot = i;
            client.execute(() -> {
                client.interactionManager.clickSlot(syncId, slot, 0, SlotActionType.QUICK_MOVE, client.player);
            });
            try { Thread.sleep(30); } catch (InterruptedException e) {}
        }
    }
    
    private String placeIngredients(MinecraftClient client, int syncId, CraftRecipe recipe) {
        int gridStart = 1; // Output is slot 0
        boolean isTable = recipe.requiresTable;
        
        for (int i = 0; i < recipe.grid.length; i++) {
            String item = recipe.grid[i];
            if (item == null || item.isEmpty()) continue;
            
            int gridSlot = gridStart + i;
            int sourceSlot = findItemSlot(client, item, syncId, isTable);
            
            if (sourceSlot == -1) {
                // Return items from cursor to inventory before failing if it's cluttered?
                // For now just error.
                LOGGER.warn("Could not find {} for crafting", item);
                return "Slot not found for " + item + " (Inv count: " + countItemInInventory(client, item) + ")";
            }
            
            final int dst = gridSlot;
            
            if (sourceSlot == -2) {
                // Already in cursor! Just place one.
                client.execute(() -> {
                    client.interactionManager.clickSlot(syncId, dst, 1, SlotActionType.PICKUP, client.player); // Right click = place 1
                });
                try { Thread.sleep(80); } catch (InterruptedException e) {}
            } else {
                final int src = sourceSlot;
                // If cursor is NOT empty and not our item, clear it?
                // Assuming it's clean for now.
                
                // Pick up from source (Right click to pick up 1)
                client.execute(() -> {
                    client.interactionManager.clickSlot(syncId, src, 1, SlotActionType.PICKUP, client.player);
                });
                try { Thread.sleep(80); } catch (InterruptedException e) {}
                
                // Place in grid
                client.execute(() -> {
                    client.interactionManager.clickSlot(syncId, dst, 0, SlotActionType.PICKUP, client.player);
                });
                try { Thread.sleep(80); } catch (InterruptedException e) {}
            }
        }
        
        // Final safety: if something's left in cursor, put it back in first empty slot
        client.execute(() -> {
            ItemStack cursor = client.player.currentScreenHandler.getCursorStack();
            if (!cursor.isEmpty()) {
                // Attempt to dump back to inventory (slots 9-45 in Player screen, but depends on screen)
                // We'll just click a slot in the main inventory area.
                // For Crafting table, main starts at 10.
                int dumpSlot = isTable ? 10 : 9; 
                client.interactionManager.clickSlot(syncId, dumpSlot, 0, SlotActionType.QUICK_MOVE, client.player);
            }
        });
        
        return null; // success
    }
}
