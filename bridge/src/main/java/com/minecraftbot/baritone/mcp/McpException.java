package com.minecraftbot.baritone.mcp;

/**
 * Exception thrown during MCP request processing.
 * Carries JSON-RPC error code for proper error responses.
 */
public class McpException extends RuntimeException {

    private final int code;
    private final String data;

    public McpException(int code, String message) {
        super(message);
        this.code = code;
        this.data = null;
    }

    public McpException(int code, String message, String data) {
        super(message);
        this.code = code;
        this.data = data;
    }

    public McpException(int code, String message, Throwable cause) {
        super(message, cause);
        this.code = code;
        this.data = cause != null ? cause.getMessage() : null;
    }

    public int getCode() {
        return code;
    }

    public String getData() {
        return data;
    }
}
