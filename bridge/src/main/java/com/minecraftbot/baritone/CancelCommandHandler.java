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
        CompletableFuture<CommandResult> result = new CompletableFuture<>();
        Runnable cancel = () -> {
            try {
                baritone.getPathingBehavior().cancelEverything();
                JsonObject data = new JsonObject();
                data.addProperty("cancelled", true);
                result.complete(CommandResult.success(data));
            } catch (Exception e) {
                result.complete(CommandResult.error("Cancel failed: " + e.getMessage()));
            }
        };
        if (client != null) {
            client.execute(cancel);
        } else {
            cancel.run();
        }
        return result;
    }

    @Override
    public String getCommandName() {
        return "cancel";
    }
}
