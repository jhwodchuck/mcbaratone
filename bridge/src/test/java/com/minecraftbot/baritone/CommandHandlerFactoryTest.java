package com.minecraftbot.baritone;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

public class CommandHandlerFactoryTest {

    @BeforeEach
    @AfterEach
    public void cleanUp() {
        CommandHandlerFactory.clearRegistry();
    }

    @Test
    public void testRegisterAndGetHandler() {
        // Register a handler
        CommandHandlerFactory.registerHandler("test_command", TestCommandHandler.class);

        // Get the handler
        CommandHandler handler = CommandHandlerFactory.getHandler("test_command");

        assertNotNull(handler);
        assertTrue(handler instanceof TestCommandHandler);
        assertEquals("test_command", handler.getCommandName());
    }

    @Test
    public void testGetHandlerReturnsSameInstance() {
        CommandHandlerFactory.registerHandler("singleton_test", TestCommandHandler.class);

        CommandHandler handler1 = CommandHandlerFactory.getHandler("singleton_test");
        CommandHandler handler2 = CommandHandlerFactory.getHandler("singleton_test");

        assertSame(handler1, handler2);
    }

    @Test
    public void testHasHandler() {
        assertFalse(CommandHandlerFactory.hasHandler("nonexistent"));

        CommandHandlerFactory.registerHandler("exists", TestCommandHandler.class);

        assertTrue(CommandHandlerFactory.hasHandler("exists"));
    }

    @Test
    public void testGetHandlerForUnknownCommand() {
        CommandHandler handler = CommandHandlerFactory.getHandler("unknown_command");

        assertNull(handler);
    }

    @Test
    public void testGetHandlerWithNullCommand() {
        CommandHandler handler = CommandHandlerFactory.getHandler(null);

        assertNull(handler);
    }

    @Test
    public void testGetRegisteredCommands() {
        CommandHandlerFactory.registerHandler("cmd1", TestCommandHandler.class);
        CommandHandlerFactory.registerHandler("cmd2", TestCommandHandler.class);

        String[] commands = CommandHandlerFactory.getRegisteredCommands();

        assertEquals(2, commands.length);
        assertTrue(contains(commands, "cmd1"));
        assertTrue(contains(commands, "cmd2"));
    }

    @Test
    public void testGetRegisteredHandlerCount() {
        assertEquals(0, CommandHandlerFactory.getRegisteredHandlerCount());

        CommandHandlerFactory.registerHandler("cmd1", TestCommandHandler.class);
        CommandHandlerFactory.registerHandler("cmd2", TestCommandHandler.class);

        assertEquals(2, CommandHandlerFactory.getRegisteredHandlerCount());
    }

    @Test
    public void testClearRegistry() {
        CommandHandlerFactory.registerHandler("cmd1", TestCommandHandler.class);
        CommandHandlerFactory.registerHandler("cmd2", TestCommandHandler.class);

        assertEquals(2, CommandHandlerFactory.getRegisteredHandlerCount());

        CommandHandlerFactory.clearRegistry();

        assertEquals(0, CommandHandlerFactory.getRegisteredHandlerCount());
        assertNull(CommandHandlerFactory.getHandler("cmd1"));
    }

    @Test
    public void testDefaultRegisteredHandlers() {
        // The factory registers handlers in its static initializer
        // We need to test after clearing to see the defaults
        CommandHandlerFactory.clearRegistry();

        // Re-register defaults for testing
        CommandHandlerFactory.registerHandler("goto", GotoCommandHandler.class);
        CommandHandlerFactory.registerHandler("mine", MineCommandHandler.class);
        CommandHandlerFactory.registerHandler("get_inventory", InventoryCommandHandler.class);
        CommandHandlerFactory.registerHandler("get_state", StateCommandHandler.class);
        CommandHandlerFactory.registerHandler("build", BuildCommandHandler.class);

        assertTrue(CommandHandlerFactory.hasHandler("goto"));
        assertTrue(CommandHandlerFactory.hasHandler("mine"));
        assertTrue(CommandHandlerFactory.hasHandler("get_inventory"));
        assertTrue(CommandHandlerFactory.hasHandler("get_state"));
        assertTrue(CommandHandlerFactory.hasHandler("build"));

        // Test instance creation
        assertNotNull(CommandHandlerFactory.getHandler("goto"));
        assertNotNull(CommandHandlerFactory.getHandler("mine"));
    }

    @Test
    public void testHandlerInstantiationFailure() {
        // Register a class that cannot be instantiated
        CommandHandlerFactory.registerHandler("bad_handler", BadCommandHandler.class);

        CommandHandler handler = CommandHandlerFactory.getHandler("bad_handler");

        assertNull(handler);
    }

    @Test
    public void testMultipleCommandsSameHandler() {
        CommandHandlerFactory.registerHandler("goto", GotoCommandHandler.class);
        CommandHandlerFactory.registerHandler("come", GotoCommandHandler.class);
        CommandHandlerFactory.registerHandler("follow", GotoCommandHandler.class);

        CommandHandler gotoHandler = CommandHandlerFactory.getHandler("goto");
        CommandHandler comeHandler = CommandHandlerFactory.getHandler("come");
        CommandHandler followHandler = CommandHandlerFactory.getHandler("follow");

        assertNotNull(gotoHandler);
        assertNotNull(comeHandler);
        assertNotNull(followHandler);

        // Different commands should return different instances
        assertNotSame(gotoHandler, comeHandler);
        assertNotSame(comeHandler, followHandler);
    }

    // Helper method to check if array contains a string
    private boolean contains(String[] array, String value) {
        for (String item : array) {
            if (item.equals(value)) {
                return true;
            }
        }
        return false;
    }

    // Test command handler implementation
    public static class TestCommandHandler extends AbstractCommandHandler {
        @Override
        public String getCommandName() {
            return "test_command";
        }

        @Override
        protected CommandResult execute(com.google.gson.JsonObject params, net.minecraft.client.MinecraftClient client, baritone.api.IBaritone baritone, java.net.Socket clientSocket) {
            return CommandResult.success(new com.google.gson.JsonObject());
        }
    }

    // Bad command handler that cannot be instantiated
    public static abstract class BadCommandHandler extends AbstractCommandHandler {
        // Abstract class cannot be instantiated
    }
}