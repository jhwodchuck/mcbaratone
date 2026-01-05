package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;

import java.net.Socket;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * Handler for the use_item command.
 * Simulates a right-click (use) action, optionally held for a duration.
 * Used for eating food, using bows, interacting with items in hand, etc.
 */
public class UseItemCommandHandler implements CommandHandler {
    
    private static final ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor();

    @Override
    public CompletableFuture<CommandResult> handle(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        if (client.player == null) {
            return CompletableFuture.completedFuture(CommandResult.error("Player not available"));
        }
        
        try {
            int durationMs = params.has("duration_ms") ? params.get("duration_ms").getAsInt() : 0;
            
            // Press the use key on the main thread
            client.execute(() -> client.options.useKey.setPressed(true));
            
            JsonObject data = new JsonObject();
            
            if (durationMs > 0) {
                // Schedule release after duration using scheduler (avoids creating new threads)
                scheduler.schedule(() -> {
                    client.execute(() -> client.options.useKey.setPressed(false));
                }, durationMs, TimeUnit.MILLISECONDS);
                
                data.addProperty("holding", true);
                data.addProperty("duration_ms", durationMs);
            } else {
                // Immediate release - schedule on next tick
                scheduler.schedule(() -> {
                    client.execute(() -> client.options.useKey.setPressed(false));
                }, 50, TimeUnit.MILLISECONDS);
                
                data.addProperty("used", true);
            }
            
            return CompletableFuture.completedFuture(CommandResult.success(data));
        } catch (Exception e) {
            return CompletableFuture.completedFuture(CommandResult.error("Use item failed: " + e.getMessage()));
        }
    }

    @Override
    public String getCommandName() {
        return "use_item";
    }
}
