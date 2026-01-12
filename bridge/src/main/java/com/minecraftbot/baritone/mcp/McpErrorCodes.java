package com.minecraftbot.baritone.mcp;

/**
 * JSON-RPC 2.0 error codes for MCP server.
 * Includes standard codes and custom Baritone-specific codes.
 */
public final class McpErrorCodes {

    private McpErrorCodes() {}

    // Standard JSON-RPC 2.0 errors
    public static final int PARSE_ERROR = -32700;
    public static final int INVALID_REQUEST = -32600;
    public static final int METHOD_NOT_FOUND = -32601;
    public static final int INVALID_PARAMS = -32602;
    public static final int INTERNAL_ERROR = -32603;

    // Custom Baritone MCP errors (-32000 to -32099 reserved for implementation)
    public static final int RATE_LIMITED = -32001;
    public static final int AUTH_FAILED = -32002;
    public static final int PLAYER_NOT_IN_WORLD = -32003;
    public static final int TOOL_EXECUTION_FAILED = -32004;
    public static final int RESOURCE_NOT_FOUND = -32005;
    public static final int BARITONE_NOT_AVAILABLE = -32006;
    public static final int OPERATION_CANCELLED = -32007;
    public static final int TIMEOUT = -32008;

    /**
     * Get a human-readable message for an error code.
     */
    public static String getMessage(int code) {
        return switch (code) {
            case PARSE_ERROR -> "Parse error";
            case INVALID_REQUEST -> "Invalid Request";
            case METHOD_NOT_FOUND -> "Method not found";
            case INVALID_PARAMS -> "Invalid params";
            case INTERNAL_ERROR -> "Internal error";
            case RATE_LIMITED -> "Rate limited";
            case AUTH_FAILED -> "Authentication failed";
            case PLAYER_NOT_IN_WORLD -> "Player not in world";
            case TOOL_EXECUTION_FAILED -> "Tool execution failed";
            case RESOURCE_NOT_FOUND -> "Resource not found";
            case BARITONE_NOT_AVAILABLE -> "Baritone not available";
            case OPERATION_CANCELLED -> "Operation cancelled";
            case TIMEOUT -> "Operation timed out";
            default -> "Unknown error";
        };
    }
}
