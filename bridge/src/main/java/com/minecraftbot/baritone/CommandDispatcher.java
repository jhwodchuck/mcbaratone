package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.Socket;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

/**
 * CommandDispatcher coordinates command execution using the handler pattern.
 * Central command orchestration point that replaces direct command handling logic.
 */
public class CommandDispatcher {
    private static final Logger logger = LoggerFactory.getLogger(CommandDispatcher.class);

    private final MissionController missionController;
    private final LegacyCommandHandler legacyHandler;

    // Rate limiting
    private final Map<Socket, Long> lastRequestTimes = new ConcurrentHashMap<>();
    private final Map<Socket, Integer> requestCounts = new ConcurrentHashMap<>();
    private static final int RATE_LIMIT_REQUESTS = 500; // increased for automation
    private static final long RATE_LIMIT_WINDOW_MS = 10000; // 10 second window

    // Timeout management
    private static final long COMMAND_TIMEOUT_MS = 30000; // 30 seconds

    public CommandDispatcher(MissionController missionController, LegacyCommandHandler legacyHandler) {
        this.missionController = missionController;
        this.legacyHandler = legacyHandler;
    }

    /**
     * Dispatch a command request through the handler pattern.
     *
     * @param request The command request containing command name and parameters
     * @param clientSocket The client socket (may be null for testing)
     * @param client Minecraft client instance
     * @param baritone Baritone API instance
     * @return CommandResult containing the execution outcome
     */
    public CommandResult dispatchCommand(JsonObject request, Socket clientSocket, MinecraftClient client, IBaritone baritone) {
        long startTime = System.currentTimeMillis();

        try {
            // Rate limiting check
            CommandResult rateLimitResult = checkRateLimit(clientSocket);
            if (rateLimitResult != null) {
                return rateLimitResult;
            }

            if (request == null || !request.has("command")) {
                return CommandResult.error("Missing command");
            }

            String command = request.get("command").getAsString();
            JsonObject params = request.has("params") ? request.getAsJsonObject("params") : new JsonObject();

            logger.debug("Dispatching command: {} with params: {}", command, params);

            // Try mission controller first (highest priority)
            if (missionController != null) {
                JsonObject missionData = new JsonObject();
                if (missionController.tryHandle(command, params, missionData, client, baritone, clientSocket)) {
                    logger.debug("Command handled by mission controller: {}", command);
                    return CommandResult.success(missionData);
                }
            }

            // Try command handler factory
            CommandHandler handler = CommandHandlerFactory.getHandler(command);
            if (handler != null) {
                logger.debug("Found handler for command: {}", command);

                // Execute with timeout monitoring
                return executeWithTimeout(handler, params, client, baritone, clientSocket, startTime);
            }

            // Fallback to legacy processing
            logger.debug("No handler found, falling back to legacy processing for: {}", command);
            if (legacyHandler != null) {
                return legacyHandler.handleLegacyCommand(command, params, client, baritone, clientSocket);
            } else {
                return CommandResult.error("Unknown command: " + command + " (no legacy handler configured)");
            }

        } catch (Exception e) {
            logger.error("Command dispatch error for command: " +
                (request != null && request.has("command") ? request.get("command").getAsString() : "unknown"), e);
            return CommandResult.error("Command execution failed: " + e.getMessage());
        }
    }

    /**
     * Check rate limiting for the client socket.
     *
     * @param clientSocket The client socket to check
     * @return CommandResult if rate limited, null if allowed
     */
    private CommandResult checkRateLimit(Socket clientSocket) {
        if (clientSocket == null) return null; // Allow for testing

        long now = System.currentTimeMillis();
        Long lastTime = lastRequestTimes.get(clientSocket);

        if (lastTime == null || (now - lastTime) > RATE_LIMIT_WINDOW_MS) {
            // New window
            lastRequestTimes.put(clientSocket, now);
            requestCounts.put(clientSocket, 1);
            return null;
        }

        int count = requestCounts.getOrDefault(clientSocket, 0) + 1;
        requestCounts.put(clientSocket, count);

        if (count > RATE_LIMIT_REQUESTS) {
            JsonObject data = new JsonObject();
            data.addProperty("retry_after_ms", RATE_LIMIT_WINDOW_MS - (now - lastTime));
            return CommandResult.error("Rate limit exceeded. Maximum " + RATE_LIMIT_REQUESTS +
                " requests per " + (RATE_LIMIT_WINDOW_MS / 1000) + " seconds");
        }

        return null;
    }

    /**
     * Execute a command handler with timeout monitoring.
     *
     * @param handler The command handler to execute
     * @param params Command parameters
     * @param client Minecraft client
     * @param baritone Baritone API instance
     * @param clientSocket Client socket
     * @param startTime Start time for timeout calculation
     * @return CommandResult from handler execution
     */
    private CommandResult executeWithTimeout(CommandHandler handler, JsonObject params,
            MinecraftClient client, IBaritone baritone, Socket clientSocket, long startTime) {

        // For now, execute synchronously with basic timeout check
        // In a more advanced implementation, this could use Future with timeout
        CommandResult result = handler.handle(params, client, baritone, clientSocket);

        long elapsed = System.currentTimeMillis() - startTime;
        if (elapsed > COMMAND_TIMEOUT_MS) {
            logger.warn("Command execution exceeded timeout: {}ms for handler: {}",
                elapsed, handler.getCommandName());
            // Note: We still return the result even if it timed out, as the handler may have partially succeeded
        }

        return result;
    }





    /**
     * Clear rate limiting data for a specific socket (useful for testing or cleanup).
     *
     * @param clientSocket The socket to clear
     */
    public void clearRateLimit(Socket clientSocket) {
        lastRequestTimes.remove(clientSocket);
        requestCounts.remove(clientSocket);
    }
}