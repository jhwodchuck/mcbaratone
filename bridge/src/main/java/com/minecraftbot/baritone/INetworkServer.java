package com.minecraftbot.baritone;

import java.io.IOException;
import java.util.concurrent.ExecutorService;

/**
 * Interface for network server abstraction.
 * Enables testing with mock servers and supports different transport implementations.
 */
public interface INetworkServer {
    
    /**
     * Start the network server on the specified port.
     * 
     * @param port The port to listen on
     * @param executor The executor service for handling connections
     * @throws IOException If the server cannot be started
     */
    void start(int port, ExecutorService executor) throws IOException;
    
    /**
     * Stop the network server and close all connections.
     * 
     * @throws IOException If an error occurs while stopping
     */
    void stop() throws IOException;
    
    /**
     * Check if the server is currently running.
     * 
     * @return true if running, false otherwise
     */
    boolean isRunning();
    
    /**
     * Get the current number of active connections.
     * 
     * @return The number of active connections
     */
    int getActiveConnections();
    
    /**
     * Get the port the server is listening on.
     * 
     * @return The port number, or -1 if not running
     */
    int getPort();
}
