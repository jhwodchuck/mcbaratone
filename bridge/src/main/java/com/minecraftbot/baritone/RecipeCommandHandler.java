package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.net.Socket;
import net.minecraft.client.Minecraft;

/**
 * Command handler for retrieving crafting recipes.
 * NOTE: In 1.21.4, client-side recipe access is not reliable.
 * This handler returns early with a message directing Python to use defaults.
 */
public class RecipeCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "get_recipes";
    }

    @Override
    protected CommandResult execute(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        // In 1.21.4+, client-side recipe access is not reliable
        // The RecipeManager on the client throws NoSuchFieldError when accessed
        // Return a graceful error message so Python falls back to hardcoded defaults
        return CommandResult.error("Recipe listing not available on client in 1.21.4. Using defaults.");
    }
}

