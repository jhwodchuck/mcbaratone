package com.minecraftbot.baritone.mcp;

import com.minecraftbot.baritone.CommandDispatcher;
import com.minecraftbot.baritone.EventManager;
import org.java_websocket.WebSocket;
import org.java_websocket.handshake.ClientHandshake;
import org.java_websocket.server.WebSocketServer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.InetSocketAddress;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * MCP WebSocket server implementing Model Context Protocol over JSON-RPC 2.0.
 * 
 * <p>This server runs on port 5557 (configurable via system property mcp.port)
 * and provides direct access to Baritone functionality via MCP tools and resources.
 * 
 * <p>Usage:
 * <pre>
 *   McpServer server = new McpServer(5557, toolRegistry, resourceRegistry);
 *   server.start();
 * </pre>
 */
public class McpServer extends WebSocketServer {

    private static final Logger LOGGER = LoggerFactory.getLogger("mcp-server");
    
    private final McpJsonRpcHandler rpcHandler;
    private final McpToolRegistry toolRegistry;
    private final McpResourceRegistry resourceRegistry;
    private final EventManager eventManager;
    private final Map<WebSocket, McpSession> sessions = new ConcurrentHashMap<>();
    
    // Configuration
    private final int maxConnections;
    private final String authToken;
    private final boolean authRequired;

    /**
     * Create a new MCP server.
     */
    public McpServer(int port, McpToolRegistry toolRegistry, McpResourceRegistry resourceRegistry) {
        super(new InetSocketAddress(port));
        
        this.toolRegistry = toolRegistry;
        this.resourceRegistry = resourceRegistry;
        this.eventManager = toolRegistry.getEventManager();
        this.rpcHandler = new McpJsonRpcHandler(toolRegistry, resourceRegistry);
        
        // Load configuration
        this.maxConnections = Integer.parseInt(System.getProperty("mcp.max.connections", "10"));
        this.authToken = System.getProperty("mcp.auth.token");
        this.authRequired = Boolean.parseBoolean(System.getProperty("mcp.auth.enabled", "false"));
        
        // Set connection timeout
        setConnectionLostTimeout(30);
        
        LOGGER.info("MCP Server configured: port={}, maxConnections={}, authRequired={}", 
            port, maxConnections, authRequired);
    }

    /**
     * Create a server with CommandDispatcher for automatic registry creation.
     */
    public static McpServer create(int port, CommandDispatcher commandDispatcher) {
        return create(port, commandDispatcher, new EventManager());
    }

    public static McpServer create(int port, CommandDispatcher commandDispatcher,
                                   EventManager eventManager) {
        McpToolRegistry toolRegistry = new McpToolRegistry(commandDispatcher, eventManager);
        McpResourceRegistry resourceRegistry = new McpResourceRegistry(commandDispatcher);
        return new McpServer(port, toolRegistry, resourceRegistry);
    }

    @Override
    public void onOpen(WebSocket conn, ClientHandshake handshake) {
        // Check connection limit
        if (sessions.size() >= maxConnections) {
            LOGGER.warn("Rejecting connection: max connections ({}) reached", maxConnections);
            conn.close(1013, "Server overloaded");
            return;
        }

        McpSession session = new McpSession(conn, this);
        sessions.put(conn, session);
        
        LOGGER.info("MCP client connected: {} (total: {})", 
            conn.getRemoteSocketAddress(), sessions.size());
    }

    @Override
    public void onMessage(WebSocket conn, String message) {
        McpSession session = sessions.get(conn);
        if (session == null) {
            LOGGER.warn("Message from unknown connection: {}", conn.getRemoteSocketAddress());
            return;
        }

        LOGGER.debug("Received: {}", message.length() > 200 ? 
            message.substring(0, 200) + "..." : message);

        try {
            String response = rpcHandler.handle(message, session);
            
            // Null response means it was a notification (no reply needed)
            if (response != null) {
                conn.send(response);
                LOGGER.debug("Sent: {}", response.length() > 200 ? 
                    response.substring(0, 200) + "..." : response);
            }
        } catch (Exception e) {
            LOGGER.error("Error handling MCP message", e);
            String errorResponse = rpcHandler.errorResponse(null, 
                McpErrorCodes.INTERNAL_ERROR, "Internal error", e.getMessage());
            conn.send(errorResponse);
        }
    }

    @Override
    public void onClose(WebSocket conn, int code, String reason, boolean remote) {
        McpSession session = sessions.remove(conn);
        if (session != null) {
            eventManager.unsubscribe(session);
            session.close();
            resourceRegistry.removeSession(session);
        }
        
        LOGGER.info("MCP client disconnected: {} (code={}, reason={}, total: {})", 
            conn.getRemoteSocketAddress(), code, reason, sessions.size());
    }

    @Override
    public void onError(WebSocket conn, Exception ex) {
        LOGGER.error("MCP WebSocket error: {}", 
            conn != null ? conn.getRemoteSocketAddress() : "server", ex);
        
        if (conn != null) {
            McpSession session = sessions.remove(conn);
            if (session != null) {
                eventManager.unsubscribe(session);
                session.close();
                resourceRegistry.removeSession(session);
            }
        }
    }

    @Override
    public void onStart() {
        LOGGER.info("MCP server started on ws://localhost:{}", getPort());
    }

    /**
     * Get the tool registry.
     */
    public McpToolRegistry getToolRegistry() {
        return toolRegistry;
    }

    /**
     * Get the resource registry.
     */
    public McpResourceRegistry getResourceRegistry() {
        return resourceRegistry;
    }

    /**
     * Get the number of active connections.
     */
    public int getConnectionCount() {
        return sessions.size();
    }

    /**
     * Verify that this server is attached to the supplied EventManager.
     *
     * @deprecated The EventManager is injected when the server is created, so
     *             no later listener registration is necessary.
     */
    @Deprecated
    public void registerWithEventManager(EventManager eventManager) {
        if (this.eventManager != eventManager) {
            throw new IllegalArgumentException(
                "MCP server is already attached to a different EventManager");
        }
    }

    /**
     * Broadcast a notification to all connected clients.
     */
    public void broadcastNotification(String method, Object params) {
        String notification = rpcHandler.createNotification(method, params);
        
        for (WebSocket conn : sessions.keySet()) {
            try {
                if (conn.isOpen()) {
                    conn.send(notification);
                }
            } catch (Exception e) {
                LOGGER.warn("Failed to send notification to {}: {}", 
                    conn.getRemoteSocketAddress(), e.getMessage());
            }
        }
    }

    /**
     * Notify all clients that a resource has been updated.
     */
    public void notifyResourceUpdated(String uri) {
        resourceRegistry.notifyResourceUpdated(uri);
    }

    /**
     * Stop the server gracefully.
     */
    public void shutdown() {
        LOGGER.info("Shutting down MCP server...");
        
        // Close all sessions
        for (McpSession session : sessions.values()) {
            eventManager.unsubscribe(session);
            session.close();
        }
        sessions.clear();
        
        try {
            stop(1000);
            LOGGER.info("MCP server stopped");
        } catch (InterruptedException e) {
            LOGGER.warn("MCP server shutdown interrupted", e);
            Thread.currentThread().interrupt();
        }
    }
}
