package com.minecraftbot.baritone;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import java.util.ArrayList;
import java.util.List;

/**
 * Error severity levels for command validation and execution.
 */
enum ErrorSeverity {
    WARNING,    // Non-critical issue that doesn't prevent execution
    ERROR,      // Critical issue that prevents execution
    FATAL       // System-level issue that requires immediate attention
}

/**
 * Standardized error codes for command operations.
 */
enum ErrorCode {
    // Parameter validation errors
    MISSING_PARAMETER("MISSING_PARAMETER"),
    INVALID_PARAMETER_TYPE("INVALID_PARAMETER_TYPE"),
    INVALID_PARAMETER_VALUE("INVALID_PARAMETER_VALUE"),
    PARAMETER_OUT_OF_RANGE("PARAMETER_OUT_OF_RANGE"),

    // Coordinate/item validation errors
    INVALID_COORDINATES("INVALID_COORDINATES"),
    COORDINATES_OUT_OF_BOUNDS("COORDINATES_OUT_OF_BOUNDS"),
    INVALID_BLOCK_ID("INVALID_BLOCK_ID"),
    INVALID_ITEM_NAME("INVALID_ITEM_NAME"),

    // Execution errors
    COMMAND_EXECUTION_FAILED("COMMAND_EXECUTION_FAILED"),
    TIMEOUT_ERROR("TIMEOUT_ERROR"),
    RESOURCE_UNAVAILABLE("RESOURCE_UNAVAILABLE"),

    // System errors
    INTERNAL_ERROR("INTERNAL_ERROR"),
    SERVICE_UNAVAILABLE("SERVICE_UNAVAILABLE");

    private final String code;

    ErrorCode(String code) {
        this.code = code;
    }

    public String getCode() {
        return code;
    }
}

/**
 * Structured error information for detailed error reporting.
 */
class CommandError {
    private final ErrorCode code;
    private final ErrorSeverity severity;
    private final String message;
    private final String parameterName;
    private final String providedValue;
    private final String expectedFormat;
    private final String suggestion;

    public CommandError(ErrorCode code, ErrorSeverity severity, String message) {
        this(code, severity, message, null, null, null, null);
    }

    public CommandError(ErrorCode code, ErrorSeverity severity, String message,
                       String parameterName, String providedValue, String expectedFormat, String suggestion) {
        this.code = code;
        this.severity = severity;
        this.message = message;
        this.parameterName = parameterName;
        this.providedValue = providedValue;
        this.expectedFormat = expectedFormat;
        this.suggestion = suggestion;
    }

    public ErrorSeverity getSeverity() { return severity; }

    /**
     * Convert this CommandError to a CommandResult.
     */
    public CommandResult toCommandResult() {
        return CommandResult.error(code, severity, message, parameterName, providedValue, expectedFormat, suggestion);
    }

    public JsonObject toJson() {
        JsonObject error = new JsonObject();
        error.addProperty("code", code.getCode());
        error.addProperty("severity", severity.name().toLowerCase());
        error.addProperty("message", message);
        if (parameterName != null) error.addProperty("parameter", parameterName);
        if (providedValue != null) error.addProperty("provided_value", providedValue);
        if (expectedFormat != null) error.addProperty("expected_format", expectedFormat);
        if (suggestion != null) error.addProperty("suggestion", suggestion);
        return error;
    }
}

/**
 * CommandResult represents the standardized response from command handlers.
 * Provides a consistent interface for success/error responses with optional data payload and detailed error information.
 */
public class CommandResult {
    private final boolean success;
    private final JsonObject data;
    private final List<CommandError> errors;
    private final String legacyErrorMessage; // For backward compatibility

    /**
     * Create a successful command result with data.
     *
     * @param data The response data payload
     */
    public CommandResult(JsonObject data) {
        this.success = true;
        this.data = data != null ? data : new JsonObject();
        this.errors = new ArrayList<>();
        this.legacyErrorMessage = null;
    }

    /**
     * Create an error command result with legacy error message.
     *
     * @param errorMessage The error description
     */
    public CommandResult(String errorMessage) {
        this.success = false;
        this.data = new JsonObject();
        this.errors = new ArrayList<>();
        this.legacyErrorMessage = errorMessage;
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
        this.errors = new ArrayList<>();
        this.legacyErrorMessage = success ? null : errorMessage;
    }

    /**
     * Create a command result with structured errors.
     *
     * @param success Whether the command succeeded
     * @param data The response data payload
     * @param errors List of structured errors
     */
    public CommandResult(boolean success, JsonObject data, List<CommandError> errors) {
        this.success = success;
        this.data = data != null ? data : new JsonObject();
        this.errors = errors != null ? errors : new ArrayList<>();
        this.legacyErrorMessage = null;
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
     * Get the error message if the command failed (legacy method for backward compatibility).
     *
     * @return Error message or null if successful
     */
    public String getErrorMessage() {
        return legacyErrorMessage;
    }

    /**
     * Get the list of structured errors.
     *
     * @return List of CommandError objects
     */
    public List<CommandError> getErrors() {
        return errors;
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

        if (!success) {
            if (!errors.isEmpty()) {
                // Include structured errors
                JsonArray errorArray = new JsonArray();
                for (CommandError error : errors) {
                    errorArray.add(error.toJson());
                }
                result.add("errors", errorArray);
            } else if (legacyErrorMessage != null) {
                // Fallback to legacy error message for backward compatibility
                result.addProperty("error", legacyErrorMessage);
            }
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

    /**
     * Create an error result with structured error information.
     *
     * @param errorCode The error code
     * @param severity The error severity
     * @param message The error message
     * @return Error CommandResult
     */
    public static CommandResult error(ErrorCode errorCode, ErrorSeverity severity, String message) {
        List<CommandError> errors = new ArrayList<>();
        errors.add(new CommandError(errorCode, severity, message));
        return new CommandResult(false, new JsonObject(), errors);
    }

    /**
     * Create an error result with detailed structured error information.
     *
     * @param errorCode The error code
     * @param severity The error severity
     * @param message The error message
     * @param parameterName The parameter that caused the error
     * @param providedValue The value that was provided
     * @param expectedFormat The expected format
     * @param suggestion A helpful suggestion
     * @return Error CommandResult
     */
    public static CommandResult error(ErrorCode errorCode, ErrorSeverity severity, String message,
                                    String parameterName, String providedValue, String expectedFormat, String suggestion) {
        List<CommandError> errors = new ArrayList<>();
        errors.add(new CommandError(errorCode, severity, message, parameterName, providedValue, expectedFormat, suggestion));
        return new CommandResult(false, new JsonObject(), errors);
    }

    /**
     * Create an error result with multiple structured errors.
     *
     * @param errors List of structured errors
     * @return Error CommandResult
     */
    public static CommandResult errors(List<CommandError> errors) {
        return new CommandResult(false, new JsonObject(), errors);
    }
}