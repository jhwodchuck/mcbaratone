package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;

/**
 * Command handler for cancel: Stops the current Baritone process.
 */
public class CancelCommandHandler implements CommandHandler {

    @Override
    public CompletableFuture<CommandResult> handle(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        CompletableFuture<CommandResult> result = new CompletableFuture<>();
        Runnable cancel = () -> {
            try {
                NavigationLifecycleTracker.getInstance().markCancelRequested();
                baritone.getPathingBehavior().cancelEverything();
                ManualMiningController.getInstance().stop();
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
