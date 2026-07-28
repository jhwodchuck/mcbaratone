package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;

/**
 * Abstract base class for SYNCHRONOUS command handlers.
 * Implements the CommandHandler interface and provides utility methods.
 * Handlers extending this class must implement the execute method returning CommandResult.
 */
public abstract class AbstractCommandHandler extends AbstractBaseCommandHandler {

    @Override
    public CompletableFuture<CommandResult> handle(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        try {
            // Validate common requirements
            if (requiresPlayer() && client.player == null) {
                return CompletableFuture.completedFuture(CommandResult.error("Player not available"));
            }

            if (baritone == null && !isOfflineSafe()) {
                return CompletableFuture.completedFuture(CommandResult.error("Baritone not available. Make sure Baritone mod is installed and loaded."));
            }

            // Execute the specific command logic synchronously
            CommandResult result = execute(params, client, baritone, clientSocket);
            return CompletableFuture.completedFuture(result);

        } catch (Exception e) {
            logger.error("Error executing command: " + getCommandName(), e);
            return CompletableFuture.completedFuture(CommandResult.error("Command execution failed: " + e.getMessage()));
        }
    }

    /**
     * Execute the specific command logic synchronously.
     *
     * @param params The command parameters
     * @param client The Minecraft client
     * @param baritone The Baritone API instance
     * @param clientSocket The client socket
     * @return The command result
     */
    protected abstract CommandResult execute(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket);
}