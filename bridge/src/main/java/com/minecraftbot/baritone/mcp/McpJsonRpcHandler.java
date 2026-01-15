package com.minecraftbot.baritone.mcp;

import com.google.gson.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * JSON-RPC 2.0 message handler for MCP protocol.
 * Parses requests, routes to handlers, and builds responses.
 */
public class McpJsonRpcHandler {

    private static final Logger LOGGER = LoggerFactory.getLogger("mcp-jsonrpc");
    private static final Gson GSON = new GsonBuilder().create();

    private final McpToolRegistry toolRegistry;
    private final McpResourceRegistry resourceRegistry;

    public McpJsonRpcHandler(McpToolRegistry toolRegistry, McpResourceRegistry resourceRegistry) {
        this.toolRegistry = toolRegistry;
        this.resourceRegistry = resourceRegistry;
    }

    /**
     * Handle an incoming JSON-RPC message.
     * Returns null for notifications (no response needed).
     */
    public String handle(String message, McpSession session) {
        JsonElement id = null;

        try {
            JsonObject request = GSON.fromJson(message, JsonObject.class);

            // Validate JSON-RPC 2.0
            if (!request.has("jsonrpc") || !"2.0".equals(request.get("jsonrpc").getAsString())) {
                return errorResponse(null, McpErrorCodes.INVALID_REQUEST,
                        "Missing or invalid jsonrpc version", null);
            }

            String method = request.has("method") ? request.get("method").getAsString() : null;
            id = request.get("id"); // Can be null for notifications
            JsonElement params = request.get("params");

            if (method == null || method.isEmpty()) {
                return errorResponse(id, McpErrorCodes.INVALID_REQUEST, "Missing method", null);
            }

            LOGGER.debug("Handling MCP request: method={}, id={}", method, id);

            // Route to appropriate handler
            Object result = routeRequest(method, params, session);

            // Notifications don't get responses
            if (id == null || id.isJsonNull()) {
                return null;
            }

            return successResponse(id, result);

        } catch (McpException e) {
            LOGGER.warn("MCP error: {} (code {})", e.getMessage(), e.getCode());
            return errorResponse(id, e.getCode(), e.getMessage(), e.getData());
        } catch (JsonSyntaxException e) {
            LOGGER.warn("JSON parse error: {}", e.getMessage());
            return errorResponse(null, McpErrorCodes.PARSE_ERROR, "Parse error", e.getMessage());
        } catch (Exception e) {
            LOGGER.error("Internal error handling MCP request", e);
            return errorResponse(id, McpErrorCodes.INTERNAL_ERROR, "Internal error", e.getMessage());
        }
    }

    /**
     * Route a request to the appropriate handler based on method.
     */
    private Object routeRequest(String method, JsonElement params, McpSession session) {
        return switch (method) {
            // Lifecycle
            case "initialize" -> handleInitialize(params, session);
            case "initialized" -> handleInitialized(session);
            case "notifications/initialized" -> handleInitialized(session); // Alias for newer MCP clients
            case "ping" -> handlePing();
            case "shutdown" -> handleShutdown(session);

            // Tools
            case "tools/list" -> handleToolsList();
            case "tools/call" -> handleToolsCall(params, session);

            // Resources
            case "resources/list" -> handleResourcesList();
            case "resources/read" -> handleResourcesRead(params);
            case "resources/subscribe" -> handleResourcesSubscribe(params, session);
            case "resources/unsubscribe" -> handleResourcesUnsubscribe(params, session);

            // Cancellation
            case "notifications/cancelled" -> handleCancelled(params, session);

            default -> throw new McpException(McpErrorCodes.METHOD_NOT_FOUND,
                    "Method not found: " + method);
        };
    }

    // ==================== Lifecycle Handlers ====================

    private Object handleInitialize(JsonElement params, McpSession session) {
        if (session.isInitialized()) {
            throw new McpException(McpErrorCodes.INVALID_REQUEST, "Already initialized");
        }

        JsonObject clientInfo = null;
        if (params != null && params.isJsonObject()) {
            JsonObject paramsObj = params.getAsJsonObject();
            if (paramsObj.has("clientInfo")) {
                clientInfo = paramsObj.getAsJsonObject("clientInfo");
            }

            // Optional: Validate authentication
            // if (authRequired && !validateAuth(paramsObj)) {
            // throw new McpException(McpErrorCodes.AUTH_FAILED, "Authentication failed");
            // }
        }

        session.setInitialized(clientInfo);
        return McpCapabilities.buildInitializeResult();
    }

    private Object handleInitialized(McpSession session) {
        // Client acknowledges initialization - no action needed
        LOGGER.info("Client acknowledged initialization");
        return null; // Notification, no response
    }

    private Object handlePing() {
        JsonObject pong = new JsonObject();
        pong.addProperty("status", "ok");
        pong.addProperty("timestamp", System.currentTimeMillis());
        return pong;
    }

    private Object handleShutdown(McpSession session) {
        LOGGER.info("Shutdown requested by client");
        session.close();
        JsonObject result = new JsonObject();
        result.addProperty("success", true);
        return result;
    }

    // ==================== Tool Handlers ====================

    private Object handleToolsList() {
        return toolRegistry.listTools();
    }

    private Object handleToolsCall(JsonElement params, McpSession session) {
        if (params == null || !params.isJsonObject()) {
            throw new McpException(McpErrorCodes.INVALID_PARAMS, "Missing params");
        }

        JsonObject paramsObj = params.getAsJsonObject();

        if (!paramsObj.has("name")) {
            throw new McpException(McpErrorCodes.INVALID_PARAMS, "Missing tool name");
        }

        String toolName = paramsObj.get("name").getAsString();
        JsonObject arguments = paramsObj.has("arguments")
                ? paramsObj.getAsJsonObject("arguments")
                : new JsonObject();

        LOGGER.debug("Calling tool: {} with args: {}", toolName, arguments);

        JsonObject toolResult = toolRegistry.invokeTool(toolName, arguments, session);

        // Wrap result in MCP spec-compliant content array format
        // See: https://spec.modelcontextprotocol.io/specification/server/tools/
        JsonObject response = new JsonObject();
        JsonArray content = new JsonArray();

        JsonObject textContent = new JsonObject();
        textContent.addProperty("type", "text");
        textContent.addProperty("text", GSON.toJson(toolResult));
        content.add(textContent);

        response.add("content", content);

        // Add isError flag if tool returned an error
        if (toolResult.has("error") ||
                (toolResult.has("success") && !toolResult.get("success").getAsBoolean())) {
            response.addProperty("isError", true);
        }

        return response;
    }

    // ==================== Resource Handlers ====================

    private Object handleResourcesList() {
        return resourceRegistry.listResources();
    }

    private Object handleResourcesRead(JsonElement params) {
        if (params == null || !params.isJsonObject()) {
            throw new McpException(McpErrorCodes.INVALID_PARAMS, "Missing params");
        }

        JsonObject paramsObj = params.getAsJsonObject();
        if (!paramsObj.has("uri")) {
            throw new McpException(McpErrorCodes.INVALID_PARAMS, "Missing resource URI");
        }

        String uri = paramsObj.get("uri").getAsString();
        return resourceRegistry.readResource(uri);
    }

    private Object handleResourcesSubscribe(JsonElement params, McpSession session) {
        if (params == null || !params.isJsonObject()) {
            throw new McpException(McpErrorCodes.INVALID_PARAMS, "Missing params");
        }

        JsonObject paramsObj = params.getAsJsonObject();
        if (!paramsObj.has("uri")) {
            throw new McpException(McpErrorCodes.INVALID_PARAMS, "Missing resource URI");
        }

        String uri = paramsObj.get("uri").getAsString();
        resourceRegistry.subscribe(uri, session);

        JsonObject result = new JsonObject();
        result.addProperty("success", true);
        return result;
    }

    private Object handleResourcesUnsubscribe(JsonElement params, McpSession session) {
        if (params == null || !params.isJsonObject()) {
            throw new McpException(McpErrorCodes.INVALID_PARAMS, "Missing params");
        }

        JsonObject paramsObj = params.getAsJsonObject();
        if (!paramsObj.has("uri")) {
            throw new McpException(McpErrorCodes.INVALID_PARAMS, "Missing resource URI");
        }

        String uri = paramsObj.get("uri").getAsString();
        resourceRegistry.unsubscribe(uri, session);

        JsonObject result = new JsonObject();
        result.addProperty("success", true);
        return result;
    }

    // ==================== Cancellation Handler ====================

    private Object handleCancelled(JsonElement params, McpSession session) {
        if (params != null && params.isJsonObject()) {
            JsonObject paramsObj = params.getAsJsonObject();
            if (paramsObj.has("requestId")) {
                String requestId = paramsObj.get("requestId").getAsString();
                session.cancelTask(requestId);
            }
        }
        return null; // Notification, no response
    }

    // ==================== Response Builders ====================

    /**
     * Build a success response.
     */
    public String successResponse(JsonElement id, Object result) {
        JsonObject response = new JsonObject();
        response.addProperty("jsonrpc", "2.0");
        response.add("id", id);

        if (result == null) {
            response.add("result", JsonNull.INSTANCE);
        } else if (result instanceof JsonElement) {
            response.add("result", (JsonElement) result);
        } else {
            response.add("result", GSON.toJsonTree(result));
        }

        return response.toString();
    }

    /**
     * Build an error response.
     */
    public String errorResponse(JsonElement id, int code, String message, String data) {
        JsonObject response = new JsonObject();
        response.addProperty("jsonrpc", "2.0");
        response.add("id", id != null ? id : JsonNull.INSTANCE);

        JsonObject error = new JsonObject();
        error.addProperty("code", code);
        error.addProperty("message", message);
        if (data != null) {
            JsonObject dataObj = new JsonObject();
            dataObj.addProperty("details", data);
            error.add("data", dataObj);
        }
        response.add("error", error);

        return response.toString();
    }

    /**
     * Create a notification message (no id, server -> client).
     */
    public String createNotification(String method, Object params) {
        JsonObject notification = new JsonObject();
        notification.addProperty("jsonrpc", "2.0");
        notification.addProperty("method", method);

        if (params != null) {
            if (params instanceof JsonElement) {
                notification.add("params", (JsonElement) params);
            } else {
                notification.add("params", GSON.toJsonTree(params));
            }
        }

        return notification.toString();
    }
}
