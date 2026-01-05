package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;

import java.net.Socket;
import java.util.concurrent.CompletableFuture;

/**
 * Command handler for cancel: Stops the current Baritone process.
 */
public class CancelCommandHandler implements CommandHandler {

    @Override
    public CompletableFuture<CommandResult> handle(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        try {
            baritone.getPathingBehavior().cancelEverything();
            
            JsonObject data = new JsonObject();
            data.addProperty("cancelled", true);
            return CompletableFuture.completedFuture(CommandResult.success(data));
        } catch (Exception e) {
            return CompletableFuture.completedFuture(CommandResult.error("Cancel failed: " + e.getMessage()));
        }
    }

    @Override
    public String getCommandName() {
        return "cancel";
    }
}
