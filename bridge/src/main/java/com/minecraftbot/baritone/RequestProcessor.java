package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.minecraftbot.baritone.BaritoneAPIBridge.IPlayerContext;
import com.google.gson.Gson;
import com.google.gson.JsonObject;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.Socket;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Supplier;
import net.minecraft.client.Minecraft;

/**
 * RequestProcessor handles JSON request parsing and response formatting.
 * Responsible for:
 * - Parsing JSON requests from clients
 * - Creating standardized response envelopes (seq, timestamp, id, status)
 * - Error response formatting
 * - Delegating command execution to CommandDispatcher
 */
public class RequestProcessor {
    
    private static final Logger LOGGER = LoggerFactory.getLogger("request-processor");
    private static final Gson GSON = new Gson();
    
    private final AtomicLong sequenceNumber = new AtomicLong(0);
    private final CommandDispatcher commandDispatcher;
    private final Supplier<IBaritone> baritoneSupplier;
    private final Supplier<Minecraft> clientSupplier;
    private final IPlayerContext playerContext;
    private final EventManager eventManager;
    
    /**
     * Callback interface for emitting tick events during command processing.
     */
    @FunctionalInterface
    public interface TickEventEmitter {
        void emit(IBaritone baritone);
    }
    
    private final TickEventEmitter tickEventEmitter;
    
    /**
     * Callback interface for checking if a command can run without player.
     */
    @FunctionalInterface
    public interface OfflineCommandChecker {
        boolean isOffline(String command);
    }
    
    private final OfflineCommandChecker offlineChecker;
    
    /**
     * Create a new RequestProcessor.
     * 
     * @param commandDispatcher The command dispatcher for routing commands
     * @param baritoneSupplier Supplier for getting the Baritone instance
     * @param clientSupplier Supplier for getting the MinecraftClient instance
     * @param playerContext The player context for state checks
     * @param eventManager The event manager for tick events
     * @param tickEventEmitter Callback for emitting tick events
     * @param offlineChecker Callback for checking offline-safe commands
     */
    public RequestProcessor(
            CommandDispatcher commandDispatcher,
            Supplier<IBaritone> baritoneSupplier,
            Supplier<Minecraft> clientSupplier,
            IPlayerContext playerContext,
            EventManager eventManager,
            TickEventEmitter tickEventEmitter,
            OfflineCommandChecker offlineChecker) {
        this.commandDispatcher = commandDispatcher;
        this.baritoneSupplier = baritoneSupplier;
        this.clientSupplier = clientSupplier;
        this.playerContext = playerContext;
        this.eventManager = eventManager;
        this.tickEventEmitter = tickEventEmitter;
        this.offlineChecker = offlineChecker;
    }
    
    /**
     * Process a JSON request line and return a response.
     * 
     * @param jsonLine The raw JSON string from the client
     * @param clientSocket The client socket (for command context)
     * @return A JsonObject response with standard envelope
     */
    public JsonObject processRequest(String jsonLine, Socket clientSocket) {
        JsonObject request = null;
        String requestId = null;
        
        try {
            request = GSON.fromJson(jsonLine, JsonObject.class);
            requestId = request != null && request.has("id") ? request.get("id").getAsString() : null;
            return processCommand(request, clientSocket);
        } catch (Exception e) {
            LOGGER.error("Error parsing request", e);
            return createErrorResponse("Failed to parse request: " + e.getMessage(), requestId);
        }
    }
    
    /**
     * Process a parsed command request.
     * 
     * @param request The parsed JSON request object
     * @param clientSocket The client socket
     * @return A JsonObject response
     */
    public JsonObject processCommand(JsonObject request, Socket clientSocket) {
        JsonObject response = createResponseEnvelope(request);
        
        // Basic validation
        if (request == null || !request.has("command")) {
            response.addProperty("status", "error");
            response.addProperty("error", "Missing command");
            return response;
        }
        
        try {
            String command = request.get("command").getAsString();
            
            // Handle debug command
            if ("debug_reset_circuit".equals(command)) {
                commandDispatcher.resetCircuitBreaker();
                response.addProperty("status", "ok");
                JsonObject data = new JsonObject();
                data.addProperty("reset", true);
                response.add("data", data);
                return response;
            }
            
            Minecraft client = clientSupplier.get();
            IBaritone baritone = baritoneSupplier.get();
            
            // Baritone availability check
            if (baritone == null) {
                response.addProperty("status", "error");
                response.addProperty("error", "Baritone not available. Make sure Baritone mod is installed and loaded.");
                return response;
            }
            
            // Player availability check (some commands work offline)
            if (playerContext.isPlayerNull() && !offlineChecker.isOffline(command)) {
                response.addProperty("status", "error");
                response.addProperty("error", "Player not available");
                return response;
            }
            
            // Emit tick event before command processing
            if (tickEventEmitter != null && baritone != null) {
                tickEventEmitter.emit(baritone);
            }
            
            // Dispatch through the command dispatcher
            CommandResult result = commandDispatcher.dispatchCommand(request, clientSocket, client, baritone);
            
            // Convert CommandResult to JsonObject response
            if (result.isSuccess()) {
                response.addProperty("status", "ok");
                response.add("data", result.getData());
            } else {
                response.addProperty("status", "error");
                response.addProperty("error", result.getErrorMessage());
            }
            
        } catch (Exception e) {
            response.addProperty("status", "error");
            response.addProperty("error", e.getMessage());
            LOGGER.error("Command processing error", e);
        }
        
        return response;
    }
    
    /**
     * Create a response envelope with standard fields.
     * 
     * @param request The original request (for extracting ID)
     * @return A JsonObject with seq, timestamp, and id fields
     */
    private JsonObject createResponseEnvelope(JsonObject request) {
        JsonObject response = new JsonObject();
        response.addProperty("seq", sequenceNumber.incrementAndGet());
        response.addProperty("timestamp", System.currentTimeMillis());
        
        String id = request != null && request.has("id") ? request.get("id").getAsString() : null;
        response.addProperty("id", id);
        
        return response;
    }
    
    /**
     * Create an error response with standard envelope.
     * 
     * @param error The error message
     * @param requestId The original request ID (may be null)
     * @return A JsonObject error response
     */
    public JsonObject createErrorResponse(String error, String requestId) {
        JsonObject response = new JsonObject();
        response.addProperty("seq", sequenceNumber.incrementAndGet());
        response.addProperty("timestamp", System.currentTimeMillis());
        response.addProperty("id", requestId);
        response.addProperty("status", "error");
        response.addProperty("error", error);
        return response;
    }
    
    /**
     * Create a success response with standard envelope.
     * 
     * @param data The response data
     * @param requestId The original request ID (may be null)
     * @return A JsonObject success response
     */
    public JsonObject createSuccessResponse(JsonObject data, String requestId) {
        JsonObject response = new JsonObject();
        response.addProperty("seq", sequenceNumber.incrementAndGet());
        response.addProperty("timestamp", System.currentTimeMillis());
        response.addProperty("id", requestId);
        response.addProperty("status", "ok");
        response.add("data", data);
        return response;
    }
    
    /**
     * Get the current sequence number.
     * 
     * @return The current sequence number
     */
    public long getSequenceNumber() {
        return sequenceNumber.get();
    }
    
    /**
     * Convert a response to JSON string.
     * 
     * @param response The response object
     * @return JSON string representation
     */
    public String toJson(JsonObject response) {
        return GSON.toJson(response);
    }
    
    /**
     * Get the Gson instance for JSON operations.
     * 
     * @return The Gson instance
     */
    public static Gson getGson() {
        return GSON;
    }
}
