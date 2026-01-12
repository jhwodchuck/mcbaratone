package com.minecraftbot.baritone.mcp;

import com.google.gson.JsonObject;

/**
 * MCP server capabilities and protocol negotiation.
 * Handles the initialize handshake and capability advertisement.
 */
public class McpCapabilities {

    public static final String PROTOCOL_VERSION = "2024-11-05";
    public static final String SERVER_NAME = "baritone-bridge-mcp";
    public static final String SERVER_VERSION = "1.0.0";

    /**
     * Build the server capabilities response for the initialize handshake.
     */
    public static JsonObject buildInitializeResult() {
        JsonObject result = new JsonObject();
        result.addProperty("protocolVersion", PROTOCOL_VERSION);

        // Capabilities
        JsonObject capabilities = new JsonObject();
        
        // Tools capability (empty object = supported with defaults)
        capabilities.add("tools", new JsonObject());
        
        // Resources capability with subscription support
        JsonObject resources = new JsonObject();
        resources.addProperty("subscribe", true);
        capabilities.add("resources", resources);
        
        // Logging capability
        capabilities.add("logging", new JsonObject());
        
        result.add("capabilities", capabilities);

        // Server info
        JsonObject serverInfo = new JsonObject();
        serverInfo.addProperty("name", SERVER_NAME);
        serverInfo.addProperty("version", SERVER_VERSION);
        result.add("serverInfo", serverInfo);

        return result;
    }

    /**
     * Validate client capabilities from initialize request.
     * Currently permissive - accepts any valid client.
     */
    public static boolean validateClientCapabilities(JsonObject clientCapabilities) {
        // For now, accept any client
        return true;
    }

    /**
     * Build the instructions text for the server.
     */
    public static String getInstructions() {
        return "Expose key Baritone bridge controls (state, inventory, missions, goals, processes) " +
               "as MCP tools and resources. Use these endpoints to orchestrate End-to-End Minecraft runs.";
    }
}
