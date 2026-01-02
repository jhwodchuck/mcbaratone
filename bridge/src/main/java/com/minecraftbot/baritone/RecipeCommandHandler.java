package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.item.ItemStack;
import net.minecraft.recipe.CraftingRecipe;
import net.minecraft.recipe.Ingredient;
import net.minecraft.recipe.RecipeEntry;
import net.minecraft.registry.Registries;
import net.minecraft.util.Identifier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.Socket;
import java.util.Collection;

/**
 * Command handler for retrieving crafting recipes.
 */
public class RecipeCommandHandler extends AbstractCommandHandler {
    private static final Logger logger = LoggerFactory.getLogger(RecipeCommandHandler.class);

    @Override
    public String getCommandName() {
        return "get_recipes";
    }

    @Override
    protected CommandResult execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        if (client.world == null) {
            return CommandResult.error("World not available");
        }

        String filter = params.has("filter") ? params.get("filter").getAsString() : null;
        int limit = params.has("limit") ? params.get("limit").getAsInt() : 1000;

        JsonArray recipes = new JsonArray();
        int count = 0;

        try {
            net.minecraft.recipe.RecipeManager recipeManager = client.world.getRecipeManager();

            // In 1.21.4, RecipeManager has values() method or similar depending on implementation
            Collection<RecipeEntry<?>> entries = null;

            // Try direct access if possible
            if (recipeManager instanceof net.minecraft.recipe.ServerRecipeManager serverRecipeManager) {
                entries = serverRecipeManager.values();
            } else {
                // Client-side fallback: try to access via reflection
                try {
                    java.lang.reflect.Method valuesMethod = recipeManager.getClass().getMethod("values");
                    @SuppressWarnings("unchecked")
                    Collection<RecipeEntry<?>> result = (Collection<RecipeEntry<?>>) valuesMethod.invoke(recipeManager);
                    entries = result;
                } catch (Exception reflectEx) {
                    logger.debug("Could not get recipes via reflection: {}", reflectEx.getMessage());
                    return CommandResult.error("Recipe listing not available on client in 1.21.4 without reflection or server access");
                }
            }

            if (entries == null) {
                return CommandResult.error("No recipes available");
            }

            for (RecipeEntry<?> entry : entries) {
                if (count >= limit) break;

                Identifier id = entry.id().getValue();
                if (filter != null && !id.toString().contains(filter)) {
                    continue;
                }

                net.minecraft.recipe.Recipe<?> recipe = entry.value();

                JsonObject recipeJson = new JsonObject();
                recipeJson.addProperty("id", id.toString());
                recipeJson.addProperty("type", Registries.RECIPE_TYPE.getId(recipe.getType()).toString());

                // Check if it's a crafting recipe to get ingredients
                if (recipe instanceof CraftingRecipe craftingRecipe) {
                    JsonArray ingredients = new JsonArray();
                    try {
                        for (Ingredient ingredient : craftingRecipe.getIngredientPlacement().getIngredients()) {
                            JsonArray inputItems = new JsonArray();
                            ingredient.getMatchingItems().forEach(itemEntry -> {
                                inputItems.add(Registries.ITEM.getId(itemEntry.value()).toString());
                            });
                            if (inputItems.size() > 0) {
                                ingredients.add(inputItems);
                            }
                        }
                    } catch (Exception e) {
                        logger.debug("Could not get ingredients for recipe {}: {}", id, e.getMessage());
                    }
                    recipeJson.add("ingredients", ingredients);
                }

                recipes.add(recipeJson);
                count++;
            }

            JsonObject data = new JsonObject();
            data.add("recipes", recipes);
            data.addProperty("count", count);
            return CommandResult.success(data);

        } catch (Exception e) {
            logger.error("Error getting recipes: {}", e.getMessage());
            return CommandResult.error("Failed to get recipes: " + e.getMessage());
        }
    }
}
