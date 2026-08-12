package com.minecraftbot.baritone;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.net.ServerSocket;
import java.net.Socket;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * NetworkServer manages the TCP server lifecycle.
 * Responsible for:
 * - Starting and stopping the server socket
 * - Accepting client connections
 * - Connection limiting
 * - Socket timeout configuration
 * - Delegating connection handling to ConnectionHandler
 */
public class NetworkServer implements INetworkServer {
    
    private static final Logger LOGGER = LoggerFactory.getLogger("network-server");
    
    private static final int DEFAULT_MAX_CONNECTIONS = 10;
    private static final int DEFAULT_SOCKET_TIMEOUT_MS = 30000; // 30 seconds
    
    private final ConnectionHandler connectionHandler;
    private final Set<Socket> activeConnections;
    private final int maxConnections;
    private final int socketTimeoutMs;
    
    private ServerSocket serverSocket;
    private ExecutorService executor;
    private final AtomicBoolean running = new AtomicBoolean(false);
    private int port = -1;
    
    /**
     * Create a NetworkServer with default settings.
     * 
     * @param connectionHandler The handler for client connections
     */
    public NetworkServer(ConnectionHandler connectionHandler) {
        this(connectionHandler, ConcurrentHashMap.newKeySet(), DEFAULT_MAX_CONNECTIONS, DEFAULT_SOCKET_TIMEOUT_MS);
    }
    
    /**
     * Create a NetworkServer with custom settings.
     * 
     * @param connectionHandler The handler for client connections
     * @param activeConnections Shared set for tracking active connections
     * @param maxConnections Maximum concurrent connections
     * @param socketTimeoutMs Socket timeout in milliseconds
     */
    public NetworkServer(
            ConnectionHandler connectionHandler,
            Set<Socket> activeConnections,
            int maxConnections,
            int socketTimeoutMs) {
        this.connectionHandler = connectionHandler;
        this.activeConnections = activeConnections;
        this.maxConnections = maxConnections;
        this.socketTimeoutMs = socketTimeoutMs;
        
        // Set up connection listener to track active connections
        connectionHandler.setConnectionListener(new ConnectionHandler.ConnectionListener() {
            @Override
            public void onConnect(Socket clientSocket) {
                // Connection already tracked in accept loop
            }
            
            @Override
            public void onDisconnect(Socket clientSocket) {
                activeConnections.remove(clientSocket);
            }
        });
    }
    
    @Override
    public void start(int port, ExecutorService executor) throws IOException {
        if (running.get()) {
            throw new IllegalStateException("Server is already running");
        }
        
        this.port = port;
        this.executor = executor;

        // Bind before returning so callers never advertise a listener that
        // failed asynchronously because the port was already occupied.
        this.serverSocket = new ServerSocket(port);
        running.set(true);
        try {
            executor.submit(this::acceptLoop);
        } catch (RuntimeException e) {
            running.set(false);
            serverSocket.close();
            throw e;
        }
    }
    
    /**
     * The main accept loop that listens for client connections.
     */
    private void acceptLoop() {
        LOGGER.info("Baritone API server listening on port {}", port);

        try {
            while (running.get()) {
                try {
                    Socket clientSocket = serverSocket.accept();
                    
                    // Check connection limit
                    if (activeConnections.size() >= maxConnections) {
                        LOGGER.warn("Connection limit reached, rejecting client: {}", 
                            clientSocket.getRemoteSocketAddress());
                        closeSocketQuietly(clientSocket);
                        continue;
                    }
                    
                    // Configure socket
                    clientSocket.setSoTimeout(socketTimeoutMs);
                    
                    // Track connection
                    activeConnections.add(clientSocket);
                    
                    // Handle in background thread
                    executor.submit(() -> connectionHandler.handleClient(clientSocket));
                    
                } catch (IOException e) {
                    if (running.get()) {
                        LOGGER.error("Error accepting client connection", e);
                    }
                }
            }
        } finally {
            running.set(false);
        }
    }
    
    @Override
    public void stop() throws IOException {
        running.set(false);
        
        // Close all active connections
        for (Socket socket : activeConnections) {
            closeSocketQuietly(socket);
        }
        activeConnections.clear();
        
        // Close server socket
        if (serverSocket != null && !serverSocket.isClosed()) {
            serverSocket.close();
        }
        
        LOGGER.info("Network server stopped");
    }
    
    @Override
    public boolean isRunning() {
        return running.get();
    }
    
    @Override
    public int getActiveConnections() {
        return activeConnections.size();
    }
    
    @Override
    public int getPort() {
        return running.get() ? port : -1;
    }
    
    /**
     * Get the set of active connections.
     * This is primarily for backward compatibility and testing.
     * 
     * @return The set of active client sockets
     */
    public Set<Socket> getActiveConnectionSet() {
        return activeConnections;
    }
    
    /**
     * Close a socket quietly, ignoring any errors.
     * 
     * @param socket The socket to close
     */
    private void closeSocketQuietly(Socket socket) {
        try {
            if (socket != null && !socket.isClosed()) {
                socket.close();
            }
        } catch (IOException e) {
            LOGGER.debug("Error closing socket", e);
        }
    }
}
