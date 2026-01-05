package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;

import java.net.Socket;
import java.util.concurrent.CompletableFuture;

/**
 * Abstract class for ASYNCHRONOUS command handlers.
 * Handlers extending this must implement the execute method returning CompletableFuture.
 */
public abstract class AsyncCommandHandler extends AbstractBaseCommandHandler {

    @Override
    public CompletableFuture<CommandResult> handle(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        try {
            // Validate common requirements
            if (requiresPlayer() && client.player == null) {
                return CompletableFuture.completedFuture(CommandResult.error("Player not available"));
            }

            if (baritone == null && !isOfflineSafe()) {
                return CompletableFuture.completedFuture(CommandResult.error("Baritone not available. Make sure Baritone mod is installed and loaded."));
            }

            // Execute asynchronous command logic
            return execute(params, client, baritone, clientSocket);

        } catch (Exception e) {
            logger.error("Error executing command: " + getCommandName(), e);
            return CompletableFuture.completedFuture(CommandResult.error("Command execution failed: " + e.getMessage()));
        }
    }

    /**
     * Execute the specific command logic asynchronously.
     *
     * @param params The command parameters
     * @param client The Minecraft client
     * @param baritone The Baritone API instance
     * @param clientSocket The client socket
     * @return CompletableFuture containing the command result
     */
    public abstract CompletableFuture<CommandResult> execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket);
}
