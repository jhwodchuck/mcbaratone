package com.minecraftbot.baritone;

import com.google.gson.JsonObject;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.concurrent.CompletableFuture;
import java.util.function.Supplier;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;

/**
 * Base class for command handlers providing shared utility methods.
 * Does not dictate execution model (Sync vs Async).
 */
public abstract class AbstractBaseCommandHandler implements CommandHandler {
    protected final Logger logger = LoggerFactory.getLogger(getClass());

    /**
     * Validate command parameters using the enhanced validation framework.
     *
     * @param params The parameters to validate
     * @param validations Map of parameter names to their validators
     * @return CommandResult with validation errors if any, null if valid
     */
    protected CommandResult validateParameters(JsonObject params, java.util.Map<String, ParameterValidator.Validator> validations) {
        java.util.List<CommandError> errors = ParameterValidator.validateAll(params, validations);
        return ParameterValidator.createResult(errors);
    }

    /**
     * Validate that required coordinates (x, y, z) are present in parameters.
     *
     * @param params The parameters to validate
     * @return CommandResult with error if validation fails, null if valid
     */
    protected CommandResult validateCoordinates(JsonObject params) {
        ParameterValidator.ValidationResult result = ParameterValidator.coordinates().validate(params, "coordinates");
        return result.isValid() ? null : result.getError().toCommandResult();
    }

    /**
     * Validate that a block ID is present and properly formatted.
     *
     * @param params The parameters to validate
     * @param paramName The parameter name containing the block ID
     * @return CommandResult with error if validation fails, null if valid
     */
    protected CommandResult validateBlockId(JsonObject params, String paramName) {
        ParameterValidator.ValidationResult result = ParameterValidator.blockId().validate(params, paramName);
        return result.isValid() ? null : result.getError().toCommandResult();
    }

    /**
     * Validate a file path for security (no path traversal).
     *
     * @param path The file path to validate
     * @return CommandResult with error if validation fails, null if valid
     */
    protected CommandResult validateFilePath(String path) {
        if (path == null) {
            return CommandResult.error(ErrorCode.INVALID_PARAMETER_VALUE, ErrorSeverity.ERROR,
                "File path cannot be null", "path", null, "valid file path", null);
        }
        if (path.contains("..") || path.contains("/") || path.contains("\\")) {
            return CommandResult.error(ErrorCode.INVALID_PARAMETER_VALUE, ErrorSeverity.ERROR,
                "Invalid file path - path traversal not allowed", "path", path, "safe file path without traversal", null);
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
     *
     * @param client The Minecraft client
     * @param task The runnable to execute
     */
    protected void executeOnMainThread(Minecraft client, Runnable task) {
        if (client != null) {
            client.execute(task);
        } else {
            task.run(); // Fallback for testing without client
        }
    }
    
    /**
     * Execute a task on the Minecraft main thread and return a CompletableFuture.
     *
     * @param client The Minecraft client
     * @param task The supplier to execute
     * @return CompletableFuture completing with the result
     */
    protected CompletableFuture<CommandResult> executeOnMainThread(Minecraft client, Supplier<CommandResult> task) {
        CompletableFuture<CommandResult> future = new CompletableFuture<>();
        if (client != null) {
            client.execute(() -> {
                try {
                    future.complete(task.get());
                } catch (Exception e) {
                    future.completeExceptionally(e);
                }
            });
        } else {
            try {
                future.complete(task.get());
            } catch (Exception e) {
                future.completeExceptionally(e);
            }
        }
        return future;
    }
}
