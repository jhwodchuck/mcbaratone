package com.minecraftbot.baritone;

import com.google.gson.JsonObject;
import baritone.api.IBaritone;
import java.net.Socket;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;

/**
 * CommandHandler interface for implementing the command pattern in the Baritone API Bridge.
 * Each command type should have its own handler implementation that encapsulates
 * the command's execution logic.
 */
public interface CommandHandler {

    /**
     * Handle a command execution asynchronously.
     *
     * @param params The command parameters from the request
     * @param client The Minecraft client instance
     * @param baritone The Baritone API instance
     * @param clientSocket The client socket (may be null for testing)
     * @return CompletableFuture containing the CommandResult with the execution outcome
     */
    CompletableFuture<CommandResult> handle(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket);

    /**
     * Get the command name this handler handles.
     *
     * @return The command name string
     */
    String getCommandName();

    /**
     * Check if this command requires a player to be available.
     *
     * @return true if player is required, false otherwise
     */
    default boolean requiresPlayer() {
        return true;
    }

    /**
     * Check if this command can be executed offline (without player/world).
     *
     * @return true if can execute offline, false otherwise
     */
    default boolean isOfflineSafe() {
        return false;
    }
}