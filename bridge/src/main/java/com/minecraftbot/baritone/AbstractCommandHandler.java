package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.util.math.BlockPos;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.Socket;

/**
 * Abstract base class for command handlers providing common functionality.
 * Implements the CommandHandler interface and provides utility methods for
 * validation, coordinate handling, and common operations.
 */
public abstract class AbstractCommandHandler implements CommandHandler {
    protected final Logger logger = LoggerFactory.getLogger(getClass());

    @Override
    public CommandResult handle(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        try {
            // Validate common requirements
            if (requiresPlayer() && client.player == null) {
                return CommandResult.error("Player not available");
            }

            if (baritone == null && !isOfflineSafe()) {
                return CommandResult.error("Baritone not available. Make sure Baritone mod is installed and loaded.");
            }

            // Execute the specific command logic
            return execute(params, client, baritone, clientSocket);

        } catch (Exception e) {
            logger.error("Error executing command: " + getCommandName(), e);
            return CommandResult.error("Command execution failed: " + e.getMessage());
        }
    }

    /**
     * Execute the specific command logic. Subclasses must implement this method.
     *
     * @param params The command parameters
     * @param client The Minecraft client
     * @param baritone The Baritone API instance
     * @param clientSocket The client socket
     * @return The command result
     */
    protected abstract CommandResult execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket);

    /**
     * Validate that required coordinates (x, y, z) are present in parameters.
     *
     * @param params The parameters to validate
     * @return CommandResult with error if validation fails, null if valid
     */
    protected CommandResult validateCoordinates(JsonObject params) {
        if (params.has("x") && params.has("y") && params.has("z")) {
            int x = params.get("x").getAsInt();
            int y = params.get("y").getAsInt();
            int z = params.get("z").getAsInt();

            // Minecraft coordinate bounds validation
            if (x < -30000000 || x > 30000000 || y < -64 || y > 320 || z < -30000000 || z > 30000000) {
                return CommandResult.error("Coordinates out of valid Minecraft range");
            }
            return null; // Valid
        }
        return CommandResult.error("Missing required coordinates (x, y, z)");
    }

    /**
     * Validate that a block ID is present and properly formatted.
     *
     * @param params The parameters to validate
     * @param paramName The parameter name containing the block ID
     * @return CommandResult with error if validation fails, null if valid
     */
    protected CommandResult validateBlockId(JsonObject params, String paramName) {
        if (!params.has(paramName)) {
            return CommandResult.error("Missing required parameter: " + paramName);
        }

        String blockId = params.get(paramName).getAsString();
        if (blockId == null || blockId.trim().isEmpty()) {
            return CommandResult.error("Block ID cannot be null or empty");
        }

        // Basic validation - could be enhanced
        if (!blockId.contains(":") && !blockId.startsWith("minecraft:")) {
            // Assume minecraft namespace
            params.addProperty(paramName, "minecraft:" + blockId);
        }

        return null; // Valid
    }

    /**
     * Validate a file path for security (no path traversal).
     *
     * @param path The file path to validate
     * @return CommandResult with error if validation fails, null if valid
     */
    protected CommandResult validateFilePath(String path) {
        if (path == null) {
            return CommandResult.error("File path cannot be null");
        }
        if (path.contains("..") || path.contains("/") || path.contains("\\")) {
            return CommandResult.error("Invalid file path - path traversal not allowed");
        }
        return null; // Valid
    }

    /**
     * Create a BlockPos from parameters, assuming x, y, z are present.
     *
     * @param params The parameters containing x, y, z
     * @return The BlockPos
     */
    protected BlockPos getBlockPos(JsonObject params) {
        int x = params.get("x").getAsInt();
        int y = params.get("y").getAsInt();
        int z = params.get("z").getAsInt();
        return new BlockPos(x, y, z);
    }

    /**
     * Execute a task on the Minecraft main thread.
     * Use this for operations that must run on the main thread.
     *
     * @param client The Minecraft client
     * @param task The runnable to execute
     */
    protected void executeOnMainThread(MinecraftClient client, Runnable task) {
        if (client != null) {
            client.execute(task);
        } else {
            task.run(); // Fallback for testing without client
        }
    }
}