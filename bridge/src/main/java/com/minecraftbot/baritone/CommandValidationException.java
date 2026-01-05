package com.minecraftbot.baritone;

import java.util.List;

/**
 * Exception thrown when command validation fails.
 * Contains detailed validation errors with context preservation.
 */
public class CommandValidationException extends Exception {
    private final String commandName;
    private final List<CommandError> validationErrors;

    public CommandValidationException(String commandName, List<CommandError> validationErrors) {
        super("Command validation failed for '" + commandName + "': " + validationErrors.size() + " error(s)");
        this.commandName = commandName;
        this.validationErrors = validationErrors;
    }

    public CommandValidationException(String commandName, String message) {
        super("Command validation failed for '" + commandName + "': " + message);
        this.commandName = commandName;
        this.validationErrors = null;
    }

    public String getCommandName() {
        return commandName;
    }

    public List<CommandError> getValidationErrors() {
        return validationErrors;
    }

    /**
     * Convert this exception to a CommandResult for consistent error handling.
     */
    public CommandResult toCommandResult() {
        if (validationErrors != null && !validationErrors.isEmpty()) {
            return CommandResult.errors(validationErrors);
        } else {
            return CommandResult.error(ErrorCode.COMMAND_EXECUTION_FAILED, ErrorSeverity.ERROR, getMessage());
        }
    }
}