package com.minecraftbot.baritone;

import com.google.gson.JsonObject;

import java.util.*;
import java.util.function.Function;
import java.util.regex.Pattern;

/**
 * Reusable parameter validation framework for command handlers.
 * Provides validators for common parameter types with detailed error reporting.
 */
public class ParameterValidator {

    /**
     * Result of a validation operation.
     */
    public static class ValidationResult {
        private final boolean valid;
        private final CommandError error;

        private ValidationResult(boolean valid, CommandError error) {
            this.valid = valid;
            this.error = error;
        }

        public boolean isValid() { return valid; }
        public CommandError getError() { return error; }

        public static ValidationResult valid() {
            return new ValidationResult(true, null);
        }

        public static ValidationResult invalid(CommandError error) {
            return new ValidationResult(false, error);
        }
    }

    /**
     * Base interface for parameter validators.
     */
    public interface Validator {
        ValidationResult validate(JsonObject params, String paramName);
    }

    /**
     * Validates that a required parameter is present.
     */
    public static Validator required() {
        return (params, paramName) -> {
            if (!params.has(paramName)) {
                return ValidationResult.invalid(new CommandError(
                    ErrorCode.MISSING_PARAMETER, ErrorSeverity.ERROR,
                    "Required parameter '" + paramName + "' is missing",
                    paramName, null, null, null
                ));
            }
            return ValidationResult.valid();
        };
    }

    /**
     * Validates integer parameters with optional range checking.
     */
    public static Validator integer(Integer min, Integer max) {
        return (params, paramName) -> {
            if (!params.has(paramName)) {
                return ValidationResult.valid(); // Optional parameter
            }

            try {
                int value = params.get(paramName).getAsInt();

                if (min != null && value < min) {
                    return ValidationResult.invalid(new CommandError(
                        ErrorCode.PARAMETER_OUT_OF_RANGE, ErrorSeverity.ERROR,
                        "Parameter '" + paramName + "' value " + value + " is below minimum " + min,
                        paramName, String.valueOf(value), "integer >= " + min, null
                    ));
                }

                if (max != null && value > max) {
                    return ValidationResult.invalid(new CommandError(
                        ErrorCode.PARAMETER_OUT_OF_RANGE, ErrorSeverity.ERROR,
                        "Parameter '" + paramName + "' value " + value + " exceeds maximum " + max,
                        paramName, String.valueOf(value), "integer <= " + max, null
                    ));
                }

                return ValidationResult.valid();
            } catch (Exception e) {
                return ValidationResult.invalid(new CommandError(
                    ErrorCode.INVALID_PARAMETER_TYPE, ErrorSeverity.ERROR,
                    "Parameter '" + paramName + "' must be an integer",
                    paramName, params.get(paramName).toString(), "integer", null
                ));
            }
        };
    }

    /**
     * Validates number parameters (integers or decimals) with optional range checking.
     */
    public static Validator number(Double min, Double max) {
        return (params, paramName) -> {
            if (!params.has(paramName)) {
                return ValidationResult.valid(); // Optional parameter
            }

            try {
                double value = params.get(paramName).getAsDouble();

                if (min != null && value < min) {
                    return ValidationResult.invalid(new CommandError(
                        ErrorCode.PARAMETER_OUT_OF_RANGE, ErrorSeverity.ERROR,
                        "Parameter '" + paramName + "' value " + value + " is below minimum " + min,
                        paramName, String.valueOf(value), "number >= " + min, null
                    ));
                }

                if (max != null && value > max) {
                    return ValidationResult.invalid(new CommandError(
                        ErrorCode.PARAMETER_OUT_OF_RANGE, ErrorSeverity.ERROR,
                        "Parameter '" + paramName + "' value " + value + " exceeds maximum " + max,
                        paramName, String.valueOf(value), "number <= " + max, null
                    ));
                }

                return ValidationResult.valid();
            } catch (Exception e) {
                return ValidationResult.invalid(new CommandError(
                    ErrorCode.INVALID_PARAMETER_TYPE, ErrorSeverity.ERROR,
                    "Parameter '" + paramName + "' must be a number",
                    paramName, params.get(paramName).toString(), "number", null
                ));
            }
        };
    }

    /**
     * Validates string parameters with optional regex pattern and length constraints.
     */
    public static Validator string(Pattern pattern, Integer minLength, Integer maxLength) {
        return (params, paramName) -> {
            if (!params.has(paramName)) {
                return ValidationResult.valid(); // Optional parameter
            }

            String value = params.get(paramName).getAsString();

            if (minLength != null && value.length() < minLength) {
                return ValidationResult.invalid(new CommandError(
                    ErrorCode.INVALID_PARAMETER_VALUE, ErrorSeverity.ERROR,
                    "Parameter '" + paramName + "' is too short (minimum " + minLength + " characters)",
                    paramName, value, "string with length >= " + minLength, null
                ));
            }

            if (maxLength != null && value.length() > maxLength) {
                return ValidationResult.invalid(new CommandError(
                    ErrorCode.INVALID_PARAMETER_VALUE, ErrorSeverity.ERROR,
                    "Parameter '" + paramName + "' is too long (maximum " + maxLength + " characters)",
                    paramName, value, "string with length <= " + maxLength, null
                ));
            }

            if (pattern != null && !pattern.matcher(value).matches()) {
                return ValidationResult.invalid(new CommandError(
                    ErrorCode.INVALID_PARAMETER_VALUE, ErrorSeverity.ERROR,
                    "Parameter '" + paramName + "' does not match required pattern",
                    paramName, value, pattern.pattern(), null
                ));
            }

            return ValidationResult.valid();
        };
    }

    /**
     * Validates enum parameters against a set of allowed values.
     */
    public static Validator oneOf(String... allowedValues) {
        Set<String> allowed = new HashSet<>(Arrays.asList(allowedValues));
        return (params, paramName) -> {
            if (!params.has(paramName)) {
                return ValidationResult.valid(); // Optional parameter
            }

            String value = params.get(paramName).getAsString();
            if (!allowed.contains(value)) {
                return ValidationResult.invalid(new CommandError(
                    ErrorCode.INVALID_PARAMETER_VALUE, ErrorSeverity.ERROR,
                    "Parameter '" + paramName + "' must be one of: " + String.join(", ", allowedValues),
                    paramName, value, String.join(" | ", allowedValues), null
                ));
            }

            return ValidationResult.valid();
        };
    }

    /**
     * Validates Minecraft block coordinates (x, y, z).
     */
    public static Validator coordinates() {
        return (params, paramName) -> {
            // Check if all coordinate parameters are present
            boolean hasX = params.has("x");
            boolean hasY = params.has("y");
            boolean hasZ = params.has("z");

            if (!hasX || !hasY || !hasZ) {
                List<String> missing = new ArrayList<>();
                if (!hasX) missing.add("x");
                if (!hasY) missing.add("y");
                if (!hasZ) missing.add("z");

                return ValidationResult.invalid(new CommandError(
                    ErrorCode.INVALID_COORDINATES, ErrorSeverity.ERROR,
                    "Missing coordinate parameters: " + String.join(", ", missing),
                    paramName, null, "x, y, z (all required)", null
                ));
            }

            try {
                int x = params.get("x").getAsInt();
                int y = params.get("y").getAsInt();
                int z = params.get("z").getAsInt();

                // Minecraft world boundaries
                if (x < -30000000 || x > 30000000 || z < -30000000 || z > 30000000) {
                    return ValidationResult.invalid(new CommandError(
                        ErrorCode.COORDINATES_OUT_OF_BOUNDS, ErrorSeverity.ERROR,
                        "Coordinates (" + x + ", " + y + ", " + z + ") are outside valid Minecraft world boundaries",
                        paramName, x + "," + y + "," + z, "x,z between -30M and 30M", null
                    ));
                }

                if (y < -64 || y > 320) {
                    return ValidationResult.invalid(new CommandError(
                        ErrorCode.COORDINATES_OUT_OF_BOUNDS, ErrorSeverity.ERROR,
                        "Y coordinate " + y + " is outside valid Minecraft height range",
                        "y", String.valueOf(y), "y between -64 and 320", null
                    ));
                }

                return ValidationResult.valid();
            } catch (Exception e) {
                return ValidationResult.invalid(new CommandError(
                    ErrorCode.INVALID_COORDINATES, ErrorSeverity.ERROR,
                    "Coordinate parameters must be integers",
                    paramName, params.get("x") + "," + params.get("y") + "," + params.get("z"), "integer coordinates", null
                ));
            }
        };
    }

    /**
     * Validates Minecraft block/item IDs.
     */
    public static Validator blockId() {
        return (params, paramName) -> {
            if (!params.has(paramName)) {
                return ValidationResult.valid(); // Optional parameter
            }

            String blockId = params.get(paramName).getAsString();
            if (blockId == null || blockId.trim().isEmpty()) {
                return ValidationResult.invalid(new CommandError(
                    ErrorCode.INVALID_BLOCK_ID, ErrorSeverity.ERROR,
                    "Block ID cannot be null or empty",
                    paramName, blockId, "valid block identifier", null
                ));
            }

            // Basic validation - ensure it looks like a block ID
            if (!blockId.contains(":") && !blockId.startsWith("minecraft:")) {
                // Auto-prefix with minecraft namespace
                params.addProperty(paramName, "minecraft:" + blockId);
            }

            // Could add more sophisticated validation here (check against known blocks)
            // For now, just ensure it has namespace:format
            String finalBlockId = params.get(paramName).getAsString();
            if (!finalBlockId.matches("^[a-z0-9_]+:[a-z0-9_/]+$")) {
                return ValidationResult.invalid(new CommandError(
                    ErrorCode.INVALID_BLOCK_ID, ErrorSeverity.ERROR,
                    "Block ID format is invalid",
                    paramName, finalBlockId, "namespace:block_name (e.g., minecraft:stone)", "Did you mean 'minecraft:" + blockId + "'?"
                ));
            }

            return ValidationResult.valid();
        };
    }

    /**
     * Validates Minecraft item names (similar to block IDs).
     */
    public static Validator itemName() {
        return (params, paramName) -> {
            if (!params.has(paramName)) {
                return ValidationResult.valid(); // Optional parameter
            }

            String itemName = params.get(paramName).getAsString();
            if (itemName == null || itemName.trim().isEmpty()) {
                return ValidationResult.invalid(new CommandError(
                    ErrorCode.INVALID_ITEM_NAME, ErrorSeverity.ERROR,
                    "Item name cannot be null or empty",
                    paramName, itemName, "valid item identifier", null
                ));
            }

            // Similar to block validation but could be extended for item-specific rules
            if (!itemName.contains(":") && !itemName.startsWith("minecraft:")) {
                params.addProperty(paramName, "minecraft:" + itemName);
            }

            String finalItemName = params.get(paramName).getAsString();
            if (!finalItemName.matches("^[a-z0-9_]+:[a-z0-9_/]+$")) {
                return ValidationResult.invalid(new CommandError(
                    ErrorCode.INVALID_ITEM_NAME, ErrorSeverity.ERROR,
                    "Item name format is invalid",
                    paramName, finalItemName, "namespace:item_name (e.g., minecraft:diamond)", "Did you mean 'minecraft:" + itemName + "'?"
                ));
            }

            return ValidationResult.valid();
        };
    }

    /**
     * Combines multiple validators - all must pass.
     */
    public static Validator allOf(Validator... validators) {
        return (params, paramName) -> {
            for (Validator validator : validators) {
                ValidationResult result = validator.validate(params, paramName);
                if (!result.isValid()) {
                    return result;
                }
            }
            return ValidationResult.valid();
        };
    }

    /**
     * Validates parameters using multiple validators and collects all errors.
     *
     * @param params The parameters to validate
     * @param validations Map of parameter names to their validators
     * @return List of validation errors (empty if all valid)
     */
    public static List<CommandError> validateAll(JsonObject params, Map<String, Validator> validations) {
        List<CommandError> errors = new ArrayList<>();

        for (Map.Entry<String, Validator> entry : validations.entrySet()) {
            String paramName = entry.getKey();
            Validator validator = entry.getValue();

            ValidationResult result = validator.validate(params, paramName);
            if (!result.isValid()) {
                errors.add(result.getError());
            }
        }

        return errors;
    }

    /**
     * Creates a CommandResult from validation errors.
     *
     * @param errors List of validation errors
     * @return CommandResult with errors if any, or null if valid
     */
    public static CommandResult createResult(List<CommandError> errors) {
        if (errors.isEmpty()) {
            return null; // Valid
        }

        // Check if any errors are fatal
        boolean hasFatal = errors.stream().anyMatch(e -> e.getSeverity() == ErrorSeverity.FATAL);
        if (hasFatal) {
            return CommandResult.errors(errors);
        }

        // Check if any errors prevent execution
        boolean hasErrors = errors.stream().anyMatch(e -> e.getSeverity() == ErrorSeverity.ERROR);
        if (hasErrors) {
            return CommandResult.errors(errors);
        }

        // Only warnings - could still proceed but with warnings
        return CommandResult.errors(errors);
    }
}