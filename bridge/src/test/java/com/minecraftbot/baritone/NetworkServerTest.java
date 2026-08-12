package com.minecraftbot.baritone;

import java.io.IOException;
import java.net.ServerSocket;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;

class NetworkServerTest {

    @Test
    void occupiedPortFailsSynchronouslyWithoutAdvertisingRunning() throws Exception {
        ExecutorService executor = Executors.newSingleThreadExecutor();
        NetworkServer server = new NetworkServer(mock(ConnectionHandler.class));

        try (ServerSocket occupied = new ServerSocket(0)) {
            int port = occupied.getLocalPort();

            assertThrows(IOException.class, () -> server.start(port, executor));
            assertFalse(server.isRunning());
            assertEquals(-1, server.getPort());
        } finally {
            executor.shutdownNow();
        }
    }
}
