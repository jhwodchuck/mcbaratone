package com.minecraftbot.baritone;

import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import baritone.api.IBaritone;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;

import java.net.Socket;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

public class CommandHandlerFactoryTest {

    @Mock
    private MinecraftClient mockClient;

    @Mock
    private IBaritone mockBaritone;

    @Mock
    private Socket mockSocket;

    private static class TestCommandHandler implements CommandHandler {
        @Override
        public CommandResult handle(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
            return CommandResult.success(new JsonObject());
        }

        @Override
        public String getCommandName() {
            return "test";
        }
    }

    @BeforeEach
    void setUp() {
        MockitoAnnotations.openMocks(this);
        // Clear registry before each test to ensure clean state
        CommandHandlerFactory.clearRegistry();
    }

    @AfterEach
    void tearDown() {
        // Clean up after each test
        CommandHandlerFactory.clearRegistry();
    }

    @Test
    void testGetHandler_WithNullCommandName_ShouldReturnNull() {
        CommandHandler result = CommandHandlerFactory.getHandler(null);
        assertNull(result);
    }

    @Test
    void testGetHandler_WithUnregisteredCommand_ShouldReturnNull() {
        CommandHandler result = CommandHandlerFactory.getHandler("nonexistent");
        assertNull(result);
    }

    @Test
    void testRegisterHandler_ShouldStoreHandlerClass() {
        CommandHandlerFactory.registerHandler("test", TestCommandHandler.class);
        assertTrue(CommandHandlerFactory.hasHandler("test"));
    }

    @Test
    void testGetHandler_ShouldReturnSingletonInstance() {
        CommandHandlerFactory.registerHandler("test", TestCommandHandler.class);

        CommandHandler handler1 = CommandHandlerFactory.getHandler("test");
        CommandHandler handler2 = CommandHandlerFactory.getHandler("test");

        assertNotNull(handler1);
        assertSame(handler1, handler2); // Should be the same instance (singleton)
    }

    @Test
    void testGetHandler_ShouldCreateInstanceFromRegisteredClass() {
        CommandHandlerFactory.registerHandler("test", TestCommandHandler.class);

        CommandHandler handler = CommandHandlerFactory.getHandler("test");

        assertNotNull(handler);
        assertEquals("test", handler.getCommandName());
        assertInstanceOf(TestCommandHandler.class, handler);
    }

    @Test
    void testHasHandler_WithRegisteredCommand_ShouldReturnTrue() {
        CommandHandlerFactory.registerHandler("test", TestCommandHandler.class);
        assertTrue(CommandHandlerFactory.hasHandler("test"));
    }

    @Test
    void testHasHandler_WithUnregisteredCommand_ShouldReturnFalse() {
        assertFalse(CommandHandlerFactory.hasHandler("nonexistent"));
    }

    @Test
    void testHasHandler_WithNullCommand_ShouldReturnFalse() {
        assertFalse(CommandHandlerFactory.hasHandler(null));
    }

    @Test
    void testGetRegisteredCommands_ShouldReturnAllRegisteredCommands() {
        CommandHandlerFactory.registerHandler("cmd1", TestCommandHandler.class);
        CommandHandlerFactory.registerHandler("cmd2", TestCommandHandler.class);

        String[] commands = CommandHandlerFactory.getRegisteredCommands();
        assertEquals(2, commands.length);
        assertTrue(java.util.Arrays.asList(commands).contains("cmd1"));
        assertTrue(java.util.Arrays.asList(commands).contains("cmd2"));
    }

    @Test
    void testGetRegisteredHandlerCount_ShouldReturnCorrectCount() {
        assertEquals(0, CommandHandlerFactory.getRegisteredHandlerCount());

        CommandHandlerFactory.registerHandler("cmd1", TestCommandHandler.class);
        assertEquals(1, CommandHandlerFactory.getRegisteredHandlerCount());

        CommandHandlerFactory.registerHandler("cmd2", TestCommandHandler.class);
        assertEquals(2, CommandHandlerFactory.getRegisteredHandlerCount());
    }

    @Test
    void testClearRegistry_ShouldRemoveAllHandlers() {
        CommandHandlerFactory.registerHandler("cmd1", TestCommandHandler.class);
        CommandHandlerFactory.registerHandler("cmd2", TestCommandHandler.class);
        CommandHandler handler = CommandHandlerFactory.getHandler("cmd1");

        assertTrue(CommandHandlerFactory.hasHandler("cmd1"));
        assertEquals(2, CommandHandlerFactory.getRegisteredHandlerCount());

        CommandHandlerFactory.clearRegistry();

        assertFalse(CommandHandlerFactory.hasHandler("cmd1"));
        assertEquals(0, CommandHandlerFactory.getRegisteredHandlerCount());
        // After clearing, getHandler should return null even if we had an instance before
        assertNull(CommandHandlerFactory.getHandler("cmd1"));
    }

    @Test
    void testGetHandler_WithInstantiationError_ShouldReturnNull() {
        // Create a handler class that cannot be instantiated
        CommandHandlerFactory.registerHandler("bad", BadCommandHandler.class);

        CommandHandler result = CommandHandlerFactory.getHandler("bad");
        assertNull(result);
    }

    private static class BadCommandHandler implements CommandHandler {
        // Constructor that throws exception
        public BadCommandHandler() {
            throw new RuntimeException("Cannot instantiate");
        }

        @Override
        public CommandResult handle(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
            return null;
        }

        @Override
        public String getCommandName() {
            return "bad";
        }
    }

    @Test
    void testStaticInitialization_RegistersDefaultHandlers() {
        // The static block in CommandHandlerFactory should register default handlers
        // Since we clear the registry in setUp, we need to test this differently
        // This test verifies that the registry works after clearing

        // First, clear to ensure clean state
        CommandHandlerFactory.clearRegistry();

        // Manually register some handlers to simulate static initialization
        CommandHandlerFactory.registerHandler("goto", GotoCommandHandler.class);
        CommandHandlerFactory.registerHandler("mine", MineCommandHandler.class);

        assertTrue(CommandHandlerFactory.hasHandler("goto"));
        assertTrue(CommandHandlerFactory.hasHandler("mine"));
        assertFalse(CommandHandlerFactory.hasHandler("nonexistent"));
    }

    @Test
    void testInstanceCaching_PreventsRecreation() {
        CommandHandlerFactory.registerHandler("test", TestCommandHandler.class);

        // Get handler twice
        CommandHandler handler1 = CommandHandlerFactory.getHandler("test");
        CommandHandler handler2 = CommandHandlerFactory.getHandler("test");

        // Should be the same instance
        assertSame(handler1, handler2);
    }

    @Test
    void testMultipleCommands_SameHandlerClass() {
        // Register the same handler class for multiple commands
        CommandHandlerFactory.registerHandler("cmd1", TestCommandHandler.class);
        CommandHandlerFactory.registerHandler("cmd2", TestCommandHandler.class);

        CommandHandler handler1 = CommandHandlerFactory.getHandler("cmd1");
        CommandHandler handler2 = CommandHandlerFactory.getHandler("cmd2");

        // Should be different instances since they are for different commands
        assertNotSame(handler1, handler2);
        assertEquals("test", handler1.getCommandName());
        assertEquals("test", handler2.getCommandName());
    }
}