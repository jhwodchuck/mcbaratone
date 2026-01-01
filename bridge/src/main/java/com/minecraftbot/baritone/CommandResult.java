package com.minecraftbot.baritone;

import com.google.gson.JsonObject;

/**
 * CommandResult represents the standardized response from command handlers.
 * Provides a consistent interface for success/error responses with optional data payload.
 */
public class CommandResult {
    private final boolean success;
    private final JsonObject data;
    private final String errorMessage;

    /**
     * Create a successful command result with data.
     *
     * @param data The response data payload
     */
    public CommandResult(JsonObject data) {
        this.success = true;
        this.data = data != null ? data : new JsonObject();
        this.errorMessage = null;
    }

    /**
     * Create an error command result with error message.
     *
     * @param errorMessage The error description
     */
    public CommandResult(String errorMessage) {
        this.success = false;
        this.data = new JsonObject();
        this.errorMessage = errorMessage;
    }

    /**
     * Create a command result with explicit success flag and data.
     *
     * @param success Whether the command succeeded
     * @param data The response data payload
     * @param errorMessage Error message (ignored if success is true)
     */
    public CommandResult(boolean success, JsonObject data, String errorMessage) {
        this.success = success;
        this.data = data != null ? data : new JsonObject();
        this.errorMessage = success ? null : errorMessage;
    }

    /**
     * Check if the command result indicates success.
     *
     * @return true if successful, false otherwise
     */
    public boolean isSuccess() {
        return success;
    }

    /**
     * Get the response data payload.
     *
     * @return The data JsonObject (never null)
     */
    public JsonObject getData() {
        return data;
    }

    /**
     * Get the error message if the command failed.
     *
     * @return Error message or null if successful
     */
    public String getErrorMessage() {
        return errorMessage;
    }

    /**
     * Convert this CommandResult to a JsonObject suitable for network transmission.
     *
     * @return JsonObject with status, data, and optional error fields
     */
    public JsonObject toJson() {
        JsonObject result = new JsonObject();
        result.addProperty("status", success ? "ok" : "error");
        result.add("data", data);

        if (!success && errorMessage != null) {
            result.addProperty("error", errorMessage);
        }

        return result;
    }

    /**
     * Create a successful result with no data.
     *
     * @return Success CommandResult
     */
    public static CommandResult success() {
        return new CommandResult(new JsonObject());
    }

    /**
     * Create a successful result with data.
     *
     * @param data The response data
     * @return Success CommandResult
     */
    public static CommandResult success(JsonObject data) {
        return new CommandResult(data);
    }

    /**
     * Create an error result.
     *
     * @param errorMessage The error message
     * @return Error CommandResult
     */
    public static CommandResult error(String errorMessage) {
        return new CommandResult(errorMessage);
    }
}