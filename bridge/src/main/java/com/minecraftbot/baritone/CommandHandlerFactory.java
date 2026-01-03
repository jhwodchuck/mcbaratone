package com.minecraftbot.baritone;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Factory for creating command handler instances based on command names.
 * Provides a registry for command handlers and manages their instantiation.
 */
public class CommandHandlerFactory {
    private static final Logger logger = LoggerFactory.getLogger(CommandHandlerFactory.class);

    private static final Map<String, Class<? extends CommandHandler>> handlerRegistry = new ConcurrentHashMap<>();
    private static final Map<String, CommandHandler> handlerInstances = new ConcurrentHashMap<>();

    static {
        // Register command handlers here as they are implemented
        // This will be expanded as individual command handlers are created

        // Movement commands
        registerHandler("goto", GotoCommandHandler.class);
        registerHandler("come", GotoCommandHandler.class);
        registerHandler("follow", GotoCommandHandler.class);

        // Mining commands
        registerHandler("mine", MineCommandHandler.class);

        // Inventory commands
        // Inventory commands
        registerHandler("get_inventory", GetInventoryCommandHandler.class);
        registerHandler("inventory_click", InventoryClickCommandHandler.class);

        // State commands
        registerHandler("get_state", StateCommandHandler.class);
        registerHandler("get_entities", StateCommandHandler.class);

        // Build commands
        registerHandler("build", BuildCommandHandler.class);
        registerHandler("sel", BuildCommandHandler.class);

        // Mission commands
        registerHandler("mission", MissionCommandHandler.class);
        
        // Explicit fixes for commands previously relying on legacy fallback
        registerHandler("place_block", PlaceBlockCommandHandler.class);
        registerHandler("get_block", GetBlockCommandHandler.class);
        registerHandler("chat", ChatCommandHandler.class);
        registerHandler("select_slot", SelectSlotCommandHandler.class);
        registerHandler("respawn", RespawnCommandHandler.class);
        registerHandler("get_recipes", RecipeCommandHandler.class);
        registerHandler("craft", CraftCommandHandler.class);
        registerHandler("find_blocks", FindBlocksCommandHandler.class);
        registerHandler("interact_block", InteractBlockCommandHandler.class);
        registerHandler("look_at", LookAtCommandHandler.class);
        registerHandler("cancel", CancelCommandHandler.class);
        registerHandler("use_item", UseItemCommandHandler.class);
        registerHandler("attack_entity", AttackEntityCommandHandler.class);
        registerHandler("attack_block", AttackBlockCommandHandler.class);
        registerHandler("get_view", GetViewCommandHandler.class);
        registerHandler("close_screen", CloseScreenCommandHandler.class);
    }

    /**
     * Register a command handler class for a specific command name.
     *
     * @param commandName The command name
     * @param handlerClass The handler class
     */
    public static void registerHandler(String commandName, Class<? extends CommandHandler> handlerClass) {
        handlerRegistry.put(commandName, handlerClass);
        logger.debug("Registered command handler: {} -> {}", commandName, handlerClass.getSimpleName());
    }

    /**
     * Get a command handler instance for the given command name.
     * Handlers are cached as singletons for performance.
     *
     * @param commandName The command name
     * @return The command handler instance, or null if not found
     */
    public static CommandHandler getHandler(String commandName) {
        if (commandName == null) {
            return null;
        }

        // Check if we already have an instance
        CommandHandler handler = handlerInstances.get(commandName);
        if (handler != null) {
            return handler;
        }

        // Try to create a new instance
        Class<? extends CommandHandler> handlerClass = handlerRegistry.get(commandName);
        if (handlerClass != null) {
            try {
                handler = handlerClass.getDeclaredConstructor().newInstance();
                handlerInstances.put(commandName, handler);
                logger.debug("Created command handler instance: {} -> {}", commandName, handler.getClass().getSimpleName());
                return handler;
            } catch (Exception e) {
                logger.error("Failed to create command handler instance for: " + commandName, e);
                return null;
            }
        }

        logger.warn("No handler registered for command: {}", commandName);
        return null;
    }

    /**
     * Check if a command handler is registered for the given command name.
     *
     * @param commandName The command name
     * @return true if a handler is registered, false otherwise
     */
    public static boolean hasHandler(String commandName) {
        return commandName != null && handlerRegistry.containsKey(commandName);
    }

    /**
     * Get all registered command names.
     *
     * @return Array of command names
     */
    public static String[] getRegisteredCommands() {
        return handlerRegistry.keySet().toArray(new String[0]);
    }

    /**
     * Clear all registered handlers and instances.
     * Useful for testing or reconfiguration.
     */
    public static void clearRegistry() {
        handlerRegistry.clear();
        handlerInstances.clear();
        logger.info("Cleared command handler registry");
    }

    /**
     * Get the number of registered handlers.
     *
     * @return The number of registered handlers
     */
    public static int getRegisteredHandlerCount() {
        return handlerRegistry.size();
    }
}