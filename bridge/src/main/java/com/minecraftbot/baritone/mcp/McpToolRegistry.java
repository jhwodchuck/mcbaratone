package com.minecraftbot.baritone.mcp;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.minecraftbot.baritone.CommandDispatcher;
import com.minecraftbot.baritone.CommandResult;
import com.minecraftbot.baritone.EventManager;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;
import java.util.EnumSet;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.function.BiFunction;
import net.minecraft.client.Minecraft;

/**
 * Registry of MCP tools with their schemas and handlers.
 * Manages tool invocation with proper thread marshaling.
 */
public class McpToolRegistry {

    private static final Logger LOGGER = LoggerFactory.getLogger("mcp-tools");
    private static final long TOOL_TIMEOUT_SECONDS = 30;

    private final CommandDispatcher commandDispatcher;
    private final Minecraft client;
    private final EventManager eventManager;
    
    // Tool definitions: name -> ToolDefinition
    private final Map<String, ToolDefinition> tools = new LinkedHashMap<>();

    public McpToolRegistry(CommandDispatcher commandDispatcher) {
        this(commandDispatcher, new EventManager());
    }

    public McpToolRegistry(CommandDispatcher commandDispatcher, EventManager eventManager) {
        this.commandDispatcher = commandDispatcher;
        this.eventManager = eventManager;
        this.client = Minecraft.getInstance();
        registerAllTools();
    }

    EventManager getEventManager() {
        return eventManager;
    }

    /**
     * Annotation for MCP tools to declare thread requirements.
     */
    @Retention(RetentionPolicy.RUNTIME)
    @Target(ElementType.METHOD)
    public @interface McpTool {
        String name();
        String description();
        boolean requiresMainThread() default true;
    }

    /**
     * Tool definition with schema and handler.
     */
    public static class ToolDefinition {
        public final String name;
        public final String description;
        public final JsonObject inputSchema;
        public final boolean requiresMainThread;
        public final BiFunction<JsonObject, McpSession, JsonObject> handler;

        public ToolDefinition(String name, String description, JsonObject inputSchema,
                              boolean requiresMainThread,
                              BiFunction<JsonObject, McpSession, JsonObject> handler) {
            this.name = name;
            this.description = description;
            this.inputSchema = inputSchema;
            this.requiresMainThread = requiresMainThread;
            this.handler = handler;
        }

        public JsonObject toJson() {
            JsonObject json = new JsonObject();
            json.addProperty("name", name);
            json.addProperty("description", description);
            json.add("inputSchema", inputSchema);
            return json;
        }
    }

    /**
     * List all registered tools.
     */
    public JsonObject listTools() {
        JsonObject result = new JsonObject();
        JsonArray toolsArray = new JsonArray();
        
        for (ToolDefinition tool : tools.values()) {
            toolsArray.add(tool.toJson());
        }
        
        result.add("tools", toolsArray);
        return result;
    }

    /**
     * Invoke a tool by name.
     */
    public JsonObject invokeTool(String name, JsonObject arguments, McpSession session) {
        ToolDefinition tool = tools.get(name);
        if (tool == null) {
            throw new McpException(McpErrorCodes.METHOD_NOT_FOUND, "Unknown tool: " + name);
        }

        try {
            if (tool.requiresMainThread && !client.isSameThread()) {
                // Marshal to main thread
                return invokeOnMainThread(tool, arguments, session);
            } else {
                // Execute directly
                return tool.handler.apply(arguments, session);
            }
        } catch (McpException e) {
            throw e;
        } catch (Exception e) {
            LOGGER.error("Tool execution failed: {}", name, e);
            throw new McpException(McpErrorCodes.TOOL_EXECUTION_FAILED, 
                "Tool execution failed: " + e.getMessage(), e);
        }
    }

    private JsonObject invokeOnMainThread(ToolDefinition tool, JsonObject arguments, McpSession session) {
        CompletableFuture<JsonObject> future = new CompletableFuture<>();
        
        client.execute(() -> {
            try {
                JsonObject result = tool.handler.apply(arguments, session);
                future.complete(result);
            } catch (Exception e) {
                future.completeExceptionally(e);
            }
        });

        try {
            return future.get(TOOL_TIMEOUT_SECONDS, TimeUnit.SECONDS);
        } catch (Exception e) {
            if (e.getCause() instanceof McpException) {
                throw (McpException) e.getCause();
            }
            throw new McpException(McpErrorCodes.TIMEOUT, 
                "Tool timed out after " + TOOL_TIMEOUT_SECONDS + "s");
        }
    }

    /**
     * Register all MCP tools.
     */
    private void registerAllTools() {
        // Core Commands (5)
        registerRunCommand();
        registerBatchCommand();
        registerCancelCommand();
        registerSnapshot();
        registerReadFile();
        
        // Movement & Goals (14)
        registerGotoCommand();
        registerGoalCommand();
        registerClearGoal();
        registerThisway();
        registerPath();
        registerGotoBlockType();
        registerFollowEntity();
        registerFollowPlayer();
        registerCome();
        registerInvert();
        registerBlacklist();
        registerExplore();
        registerAxis();
        registerSurface();
        
        // Mining & Building (6)
        registerMineCommand();
        registerStopMining();
        registerMineQuantity();
        registerBuildCommand();
        registerTunnelCommand();
        registerFarm();
        
        // Mission Tools (6)
        registerMissionMacro();
        registerMissionQueue();
        registerMissionCheckpoint();
        registerSubscribeMissionUpdates();
        registerUnsubscribeMissionUpdates();
        registerBroadcastMissionState();
        
        // Cache Tools (3)
        registerGetCachedBlock();
        registerCacheBlock();
        registerClearCache();
        
        // Event Tools (3)
        registerPollEvents();
        registerSubscribeEvents();
        registerUnsubscribeEvents();
        
        // Utility & Info (9)
        registerEta();
        registerProc();
        registerFind();
        registerVersionCommand();
        registerGc();
        registerRepack();
        registerRender();
        registerReloadAll();
        registerSaveAll();
        
        // Waypoint Tools (4)
        registerWpSave();
        registerWpDelete();
        registerWpList();
        registerWpGoal();
        
        // Settings Tools (4)
        registerSetSetting();
        registerResetSetting();
        registerResetAllSettings();
        registerGetModifiedSettings();
        
        // Miscellaneous (4)
        registerClick();
        registerBuildOpenSchematic();
        registerHelpCommand();
        registerPingCommand();
        
        LOGGER.info("Registered {} MCP tools", tools.size());
    }

    // ==================== Tool Implementations ====================

    private void registerRunCommand() {
        JsonObject schema = schemaBuilder()
            .addProperty("command", "string", "The Baritone command to execute", true)
            .build();

        register("run_command", "Execute a raw Baritone/mission command string", schema, true,
            (args, session) -> {
                String command = getRequiredString(args, "command");
                CommandResult result = commandDispatcher.dispatch("chat", createChatArgs("#" + command));
                return commandResultToJson(result);
            });
    }

    private void registerCancelCommand() {
        JsonObject schema = schemaBuilder().build();

        register("cancel_command", "Cancel the active Baritone task", schema, true,
            (args, session) -> {
                CommandResult result = commandDispatcher.dispatch("cancel", new JsonObject());
                return commandResultToJson(result);
            });
    }

    private void registerSnapshot() {
        JsonObject schema = schemaBuilder()
            .addProperty("include_state", "boolean", "Include player state", false)
            .addProperty("include_inventory", "boolean", "Include inventory", false)
            .addProperty("include_mission", "boolean", "Include mission status", false)
            .build();

        register("snapshot", "Return a combined snapshot of bridge telemetry", schema, true,
            (args, session) -> {
                JsonObject snapshot = new JsonObject();
                
                boolean includeState = args.has("include_state") ? 
                    args.get("include_state").getAsBoolean() : true;
                boolean includeInventory = args.has("include_inventory") ? 
                    args.get("include_inventory").getAsBoolean() : true;
                
                if (includeState) {
                    CommandResult stateResult = commandDispatcher.dispatch("get_state", new JsonObject());
                    snapshot.add("state", stateResult.isSuccess() ? 
                        stateResult.getData() : errorData(stateResult));
                }
                
                if (includeInventory) {
                    CommandResult invResult = commandDispatcher.dispatch("get_inventory", new JsonObject());
                    snapshot.add("inventory", invResult.isSuccess() ? 
                        invResult.getData() : errorData(invResult));
                }
                
                return snapshot;
            });
    }

    private void registerGotoCommand() {
        JsonObject schema = schemaBuilder()
            .addProperty("x", "integer", "X coordinate", true)
            .addProperty("y", "integer", "Y coordinate", true)
            .addProperty("z", "integer", "Z coordinate", true)
            .build();

        register("set_goal_block", "Apply a block-level navigation goal", schema, true,
            (args, session) -> {
                int x = getRequiredInt(args, "x");
                int y = getRequiredInt(args, "y");
                int z = getRequiredInt(args, "z");
                
                JsonObject gotoArgs = new JsonObject();
                gotoArgs.addProperty("x", x);
                gotoArgs.addProperty("y", y);
                gotoArgs.addProperty("z", z);
                
                CommandResult result = commandDispatcher.dispatch("goto", gotoArgs);
                return commandResultToJson(result);
            });
    }

    private void registerGoalCommand() {
        JsonObject schema = schemaBuilder()
            .addProperty("x", "integer", "X coordinate", false)
            .addProperty("y", "integer", "Y coordinate", false)
            .addProperty("z", "integer", "Z coordinate", false)
            .build();

        register("goal_coordinates", "Set a goal to the specified coordinates", schema, true,
            (args, session) -> {
                StringBuilder cmd = new StringBuilder("goal");
                if (args.has("x")) cmd.append(" ").append(args.get("x").getAsInt());
                if (args.has("y")) cmd.append(" ").append(args.get("y").getAsInt());
                if (args.has("z")) cmd.append(" ").append(args.get("z").getAsInt());
                
                CommandResult result = commandDispatcher.dispatch("chat", createChatArgs("#" + cmd));
                return commandResultToJson(result);
            });
    }

    private void registerClearGoal() {
        JsonObject schema = schemaBuilder().build();

        register("clear_goal", "Clear the current pathing goal", schema, true,
            (args, session) -> {
                CommandResult result = commandDispatcher.dispatch("chat", createChatArgs("#goal clear"));
                return commandResultToJson(result);
            });
    }

    private void registerMineCommand() {
        JsonObject schema = schemaBuilder()
            .addProperty("target_block", "string", "Block type to mine", true)
            .addProperty("quantity", "integer", "Number of blocks to mine", false)
            .build();

        register("start_mining", "Start the mining process for a given target block", schema, true,
            (args, session) -> {
                String block = getRequiredString(args, "target_block");
                JsonObject mineArgs = new JsonObject();
                mineArgs.addProperty("block", block);
                if (args.has("quantity")) {
                    mineArgs.addProperty("quantity", args.get("quantity").getAsInt());
                }
                
                CommandResult result = commandDispatcher.dispatch("mine", mineArgs);
                return commandResultToJson(result);
            });
    }

    private void registerBuildCommand() {
        JsonObject schema = schemaBuilder()
            .addProperty("name", "string", "Schematic name", true)
            .addProperty("x", "integer", "Origin X coordinate", false)
            .addProperty("y", "integer", "Origin Y coordinate", false)
            .addProperty("z", "integer", "Origin Z coordinate", false)
            .build();

        register("build_schematic", "Build a schematic", schema, true,
            (args, session) -> {
                String name = getRequiredString(args, "name");
                JsonObject buildArgs = new JsonObject();
                buildArgs.addProperty("schematic", name);
                if (args.has("x") && args.has("y") && args.has("z")) {
                    buildArgs.addProperty("x", args.get("x").getAsInt());
                    buildArgs.addProperty("y", args.get("y").getAsInt());
                    buildArgs.addProperty("z", args.get("z").getAsInt());
                }
                
                CommandResult result = commandDispatcher.dispatch("build", buildArgs);
                return commandResultToJson(result);
            });
    }

    private void registerTunnelCommand() {
        JsonObject schema = schemaBuilder()
            .addProperty("height", "integer", "Tunnel height", true)
            .addProperty("width", "integer", "Tunnel width", true)
            .addProperty("depth", "integer", "Tunnel depth", true)
            .build();

        register("tunnel", "Dig a tunnel with specified dimensions", schema, true,
            (args, session) -> {
                int height = getRequiredInt(args, "height");
                int width = getRequiredInt(args, "width");
                int depth = getRequiredInt(args, "depth");
                
                JsonObject tunnelArgs = new JsonObject();
                tunnelArgs.addProperty("height", height);
                tunnelArgs.addProperty("width", width);
                tunnelArgs.addProperty("depth", depth);
                
                CommandResult result = commandDispatcher.dispatch("tunnel", tunnelArgs);
                return commandResultToJson(result);
            });
    }

    private void registerVersionCommand() {
        JsonObject schema = schemaBuilder().build();

        register("version", "Get the version of Baritone", schema, false,
            (args, session) -> {
                CommandResult result = commandDispatcher.dispatch("get_version", new JsonObject());
                return commandResultToJson(result);
            });
    }

    private void registerPingCommand() {
        JsonObject schema = schemaBuilder().build();

        register("ping", "Ping the server", schema, false,
            (args, session) -> {
                JsonObject result = new JsonObject();
                result.addProperty("status", "ok");
                result.addProperty("timestamp", System.currentTimeMillis());
                return result;
            });
    }

    // ==================== Additional Core Tools ====================

    private void registerBatchCommand() {
        JsonObject schema = schemaBuilder()
            .addProperty("commands", "array", "List of commands to execute", true)
            .addProperty("stop_on_error", "boolean", "Stop on first error", false)
            .build();

        register("batch_command", "Execute multiple Baritone commands sequentially", schema, true,
            (args, session) -> {
                JsonArray commands = args.getAsJsonArray("commands");
                boolean stopOnError = args.has("stop_on_error") ? args.get("stop_on_error").getAsBoolean() : true;
                
                JsonArray results = new JsonArray();
                int executed = 0;
                
                for (int i = 0; i < commands.size(); i++) {
                    String cmd = commands.get(i).getAsString();
                    CommandResult result = commandDispatcher.dispatch("chat", createChatArgs("#" + cmd));
                    
                    JsonObject cmdResult = new JsonObject();
                    cmdResult.addProperty("command", cmd);
                    cmdResult.addProperty("success", result.isSuccess());
                    if (!result.isSuccess()) {
                        cmdResult.addProperty("error", result.getErrorMessage());
                    }
                    results.add(cmdResult);
                    executed++;
                    
                    if (!result.isSuccess() && stopOnError) {
                        break;
                    }
                }
                
                JsonObject response = new JsonObject();
                response.add("batch_results", results);
                response.addProperty("total_commands", commands.size());
                response.addProperty("executed_commands", executed);
                return response;
            });
    }

    private void registerReadFile() {
        JsonObject schema = schemaBuilder()
            .addProperty("file_path", "string", "Path to file to read", true)
            .build();

        register("read_file", "Read the contents of a file", schema, false,
            (args, session) -> {
                String filePath = getRequiredString(args, "file_path");
                JsonObject result = new JsonObject();
                try {
                    String content = java.nio.file.Files.readString(java.nio.file.Path.of(filePath));
                    result.addProperty("success", true);
                    result.addProperty("content", content);
                } catch (Exception e) {
                    result.addProperty("success", false);
                    result.addProperty("error", e.getMessage());
                }
                return result;
            });
    }

    // ==================== Movement Tools ====================

    private void registerThisway() {
        JsonObject schema = schemaBuilder()
            .addProperty("blocks", "integer", "Number of blocks to move", true)
            .build();

        register("thisway", "Go in the direction you are facing", schema, true,
            (args, session) -> {
                int blocks = getRequiredInt(args, "blocks");
                return commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#thisway " + blocks)));
            });
    }

    private void registerPath() {
        register("path", "Start pathing to the current goal", schemaBuilder().build(), true,
            (args, session) -> commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#path"))));
    }

    private void registerGotoBlockType() {
        JsonObject schema = schemaBuilder()
            .addProperty("block_type", "string", "Block type to go to", true)
            .build();

        register("goto_block_type", "Go to a block of a specific type", schema, true,
            (args, session) -> {
                String blockType = getRequiredString(args, "block_type");
                return commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#goto " + blockType)));
            });
    }

    private void registerFollowEntity() {
        JsonObject schema = schemaBuilder()
            .addProperty("entity_type", "string", "Entity type to follow", true)
            .build();

        register("follow_entity", "Follow entities of a specific type", schema, true,
            (args, session) -> {
                String entityType = getRequiredString(args, "entity_type");
                return commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#follow entity " + entityType)));
            });
    }

    private void registerFollowPlayer() {
        JsonObject schema = schemaBuilder()
            .addProperty("player_name", "string", "Player name to follow", true)
            .build();

        register("follow_player", "Follow a specific player", schema, true,
            (args, session) -> {
                String playerName = getRequiredString(args, "player_name");
                return commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#follow player " + playerName)));
            });
    }

    private void registerCome() {
        register("come", "Head towards your camera", schemaBuilder().build(), true,
            (args, session) -> commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#come"))));
    }

    private void registerInvert() {
        register("invert", "Invert the current goal (run away)", schemaBuilder().build(), true,
            (args, session) -> commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#invert"))));
    }

    private void registerBlacklist() {
        register("blacklist", "Blacklist the closest block", schemaBuilder().build(), true,
            (args, session) -> commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#blacklist"))));
    }

    private void registerExplore() {
        JsonObject schema = schemaBuilder()
            .addProperty("x", "integer", "Origin X coordinate", false)
            .addProperty("z", "integer", "Origin Z coordinate", false)
            .build();

        register("explore", "Explore the world", schema, true,
            (args, session) -> {
                StringBuilder cmd = new StringBuilder("#explore");
                if (args.has("x") && args.has("z")) {
                    cmd.append(" ").append(args.get("x").getAsInt())
                       .append(" ").append(args.get("z").getAsInt());
                }
                return commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs(cmd.toString())));
            });
    }

    private void registerAxis() {
        register("axis", "Go to an axis or diagonal", schemaBuilder().build(), true,
            (args, session) -> commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#axis"))));
    }

    private void registerSurface() {
        register("surface", "Head towards the closest surface", schemaBuilder().build(), true,
            (args, session) -> commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#surface"))));
    }

    // ==================== Mining Tools ====================

    private void registerStopMining() {
        register("stop_mining", "Stop the mining process", schemaBuilder().build(), true,
            (args, session) -> commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#mine stop"))));
    }

    private void registerMineQuantity() {
        JsonObject schema = schemaBuilder()
            .addProperty("block_type", "string", "Block type to mine", true)
            .addProperty("quantity", "integer", "Number of blocks to mine", true)
            .build();

        register("mine_quantity", "Mine a specific quantity of a block", schema, true,
            (args, session) -> {
                String blockType = getRequiredString(args, "block_type");
                int quantity = getRequiredInt(args, "quantity");
                return commandResultToJson(commandDispatcher.dispatch("chat", 
                    createChatArgs("#mine " + quantity + " " + blockType)));
            });
    }

    private void registerFarm() {
        JsonObject schema = schemaBuilder()
            .addProperty("range", "integer", "Farm range", false)
            .addProperty("waypoint", "string", "Waypoint name", false)
            .build();

        register("farm", "Automatically harvest, replant, or bone meal crops", schema, true,
            (args, session) -> {
                StringBuilder cmd = new StringBuilder("#farm");
                if (args.has("range")) {
                    cmd.append(" ").append(args.get("range").getAsInt());
                    if (args.has("waypoint")) {
                        cmd.append(" ").append(args.get("waypoint").getAsString());
                    }
                }
                return commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs(cmd.toString())));
            });
    }

    // ==================== Mission Tools ====================

    private void registerMissionMacro() {
        JsonObject schema = schemaBuilder()
            .addProperty("name", "string", "Macro name", false)
            .addProperty("dequeue", "boolean", "Consume queued macros", false)
            .build();

        register("mission_macro", "Invoke a mission macro", schema, true,
            (args, session) -> {
                JsonObject missionArgs = new JsonObject();
                if (args.has("name")) missionArgs.addProperty("name", args.get("name").getAsString());
                if (args.has("dequeue")) missionArgs.addProperty("dequeue", args.get("dequeue").getAsBoolean());
                return commandResultToJson(commandDispatcher.dispatch("mission_macro", missionArgs));
            });
    }

    private void registerMissionQueue() {
        JsonObject schema = schemaBuilder()
            .addProperty("actions", "array", "List of macro names to queue", true)
            .addProperty("clear", "boolean", "Clear existing queue first", false)
            .build();

        register("mission_queue", "Queue mission macros", schema, true,
            (args, session) -> {
                JsonObject missionArgs = new JsonObject();
                missionArgs.add("actions", args.getAsJsonArray("actions"));
                if (args.has("clear")) missionArgs.addProperty("clear", args.get("clear").getAsBoolean());
                return commandResultToJson(commandDispatcher.dispatch("mission_queue", missionArgs));
            });
    }

    private void registerMissionCheckpoint() {
        JsonObject schema = schemaBuilder()
            .addProperty("phase", "string", "Phase name", true)
            .addProperty("note", "string", "Optional note", false)
            .build();

        register("mission_checkpoint", "Persist the active phase/checkpoint", schema, true,
            (args, session) -> {
                JsonObject missionArgs = new JsonObject();
                missionArgs.addProperty("phase", getRequiredString(args, "phase"));
                if (args.has("note")) missionArgs.addProperty("note", args.get("note").getAsString());
                return commandResultToJson(commandDispatcher.dispatch("mission_checkpoint", missionArgs));
            });
    }

    private void registerSubscribeMissionUpdates() {
        register("subscribe_mission_updates", "Subscribe to mission state updates", schemaBuilder().build(), false,
            (args, session) -> {
                requireSession(session);
                Set<EventManager.EventType> eventTypes = EnumSet.of(EventManager.EventType.MISSION);
                session.subscribeToEvents(eventTypes, session.getPriorityThreshold());
                eventManager.subscribe(eventTypes, session);
                JsonObject result = new JsonObject();
                result.addProperty("success", true);
                result.addProperty("subscribed", true);
                result.add("event_types", eventTypesToJson(session.getSubscribedEvents()));
                return result;
            });
    }

    private void registerUnsubscribeMissionUpdates() {
        register("unsubscribe_mission_updates", "Unsubscribe from mission updates", schemaBuilder().build(), false,
            (args, session) -> {
                requireSession(session);
                Set<EventManager.EventType> eventTypes = EnumSet.of(EventManager.EventType.MISSION);
                eventManager.unsubscribe(eventTypes, session);
                session.unsubscribeFromEvents(eventTypes);
                JsonObject result = new JsonObject();
                result.addProperty("success", true);
                result.addProperty("subscribed", false);
                result.add("event_types", eventTypesToJson(session.getSubscribedEvents()));
                return result;
            });
    }

    private void registerBroadcastMissionState() {
        JsonObject schema = schemaBuilder()
            .addProperty("mission_data", "object", "Mission data to broadcast", true)
            .build();

        register("broadcast_mission_state", "Broadcast mission state update", schema, false,
            (args, session) -> {
                if (!args.has("mission_data") || !args.get("mission_data").isJsonObject()) {
                    throw new McpException(McpErrorCodes.INVALID_PARAMS,
                        "mission_data must be a JSON object");
                }
                eventManager.publishEvent(
                    EventManager.EventType.MISSION,
                    args.getAsJsonObject("mission_data"),
                    EventManager.Priority.NORMAL,
                    "mcp");
                JsonObject result = new JsonObject();
                result.addProperty("success", true);
                result.addProperty("published", true);
                return result;
            });
    }

    // ==================== Cache Tools ====================

    private void registerGetCachedBlock() {
        JsonObject schema = schemaBuilder()
            .addProperty("x", "integer", "X coordinate", true)
            .addProperty("y", "integer", "Y coordinate", true)
            .addProperty("z", "integer", "Z coordinate", true)
            .build();

        register("get_cached_block", "Get cached block info at coordinates", schema, false,
            (args, session) -> {
                JsonObject cacheArgs = new JsonObject();
                cacheArgs.addProperty("x", getRequiredInt(args, "x"));
                cacheArgs.addProperty("y", getRequiredInt(args, "y"));
                cacheArgs.addProperty("z", getRequiredInt(args, "z"));
                return commandResultToJson(commandDispatcher.dispatch("get_cached_block", cacheArgs));
            });
    }

    private void registerCacheBlock() {
        JsonObject schema = schemaBuilder()
            .addProperty("x", "integer", "X coordinate", true)
            .addProperty("y", "integer", "Y coordinate", true)
            .addProperty("z", "integer", "Z coordinate", true)
            .addProperty("block_state", "string", "Block state to cache", true)
            .build();

        register("cache_block", "Cache block state information", schema, false,
            (args, session) -> {
                JsonObject cacheArgs = new JsonObject();
                cacheArgs.addProperty("x", getRequiredInt(args, "x"));
                cacheArgs.addProperty("y", getRequiredInt(args, "y"));
                cacheArgs.addProperty("z", getRequiredInt(args, "z"));
                cacheArgs.addProperty("block_state", getRequiredString(args, "block_state"));
                return commandResultToJson(commandDispatcher.dispatch("cache_block", cacheArgs));
            });
    }

    private void registerClearCache() {
        register("clear_cache", "Clear all cached data", schemaBuilder().build(), false,
            (args, session) -> commandResultToJson(commandDispatcher.dispatch("clear_cache", new JsonObject())));
    }

    // ==================== Event Tools ====================

    private void registerPollEvents() {
        JsonObject schema = schemaBuilder()
            .addProperty("event_types", "array", "Event types to filter", false)
            .addProperty("max_events", "integer", "Maximum events to return", false)
            .build();

        register("poll_events", "Poll events from the buffer", schema, false,
            (args, session) -> {
                JsonObject eventArgs = new JsonObject();
                if (args.has("event_types")) eventArgs.add("event_types", args.getAsJsonArray("event_types"));
                if (args.has("max_events")) eventArgs.addProperty("max_events", args.get("max_events").getAsInt());
                return commandResultToJson(commandDispatcher.dispatch("poll_events", eventArgs));
            });
    }

    private void registerSubscribeEvents() {
        JsonObject schema = schemaBuilder()
            .addProperty("event_types", "array", "Event types to subscribe to", true)
            .addProperty("priority_threshold", "integer", "Minimum priority level", false)
            .build();

        register("subscribe_events", "Subscribe to real-time event streaming", schema, false,
            (args, session) -> {
                requireSession(session);
                Set<EventManager.EventType> eventTypes = parseEventTypes(args, "event_types");
                int priorityThreshold = args.has("priority_threshold")
                    ? args.get("priority_threshold").getAsInt()
                    : session.getPriorityThreshold();
                if (priorityThreshold < EventManager.Priority.LOW.getValue()
                        || priorityThreshold > EventManager.Priority.CRITICAL.getValue()) {
                    throw new McpException(McpErrorCodes.INVALID_PARAMS,
                        "priority_threshold must be between 0 and 3");
                }
                session.subscribeToEvents(eventTypes, priorityThreshold);
                eventManager.subscribe(eventTypes, session);
                JsonObject result = new JsonObject();
                result.addProperty("success", true);
                result.add("event_types", eventTypesToJson(session.getSubscribedEvents()));
                result.addProperty("priority_threshold", priorityThreshold);
                return result;
            });
    }

    private void registerUnsubscribeEvents() {
        JsonObject schema = schemaBuilder()
            .addProperty("event_types", "array", "Event types to unsubscribe from", true)
            .build();

        register("unsubscribe_events", "Unsubscribe from event streaming", schema, false,
            (args, session) -> {
                requireSession(session);
                Set<EventManager.EventType> eventTypes = parseEventTypes(args, "event_types");
                eventManager.unsubscribe(eventTypes, session);
                session.unsubscribeFromEvents(eventTypes);
                JsonObject result = new JsonObject();
                result.addProperty("success", true);
                result.add("event_types", eventTypesToJson(session.getSubscribedEvents()));
                return result;
            });
    }

    // ==================== Utility Tools ====================

    private void registerEta() {
        register("eta", "Get ETA information", schemaBuilder().build(), true,
            (args, session) -> commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#eta"))));
    }

    private void registerProc() {
        register("proc", "View process information", schemaBuilder().build(), true,
            (args, session) -> commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#proc"))));
    }

    private void registerFind() {
        JsonObject schema = schemaBuilder()
            .addProperty("block_type", "string", "Block type to find", true)
            .build();

        register("find", "Search cache for block location", schema, true,
            (args, session) -> {
                String blockType = getRequiredString(args, "block_type");
                return commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#find " + blockType)));
            });
    }

    private void registerGc() {
        register("gc", "Call System.gc() to free memory", schemaBuilder().build(), false,
            (args, session) -> commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#gc"))));
    }

    private void registerRepack() {
        register("repack", "Re-cache chunks around you", schemaBuilder().build(), true,
            (args, session) -> commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#repack"))));
    }

    private void registerRender() {
        register("render", "Fix glitched chunk rendering", schemaBuilder().build(), true,
            (args, session) -> commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#render"))));
    }

    private void registerReloadAll() {
        register("reload_all", "Reload world cache", schemaBuilder().build(), true,
            (args, session) -> commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#reloadall"))));
    }

    private void registerSaveAll() {
        register("save_all", "Save world cache", schemaBuilder().build(), true,
            (args, session) -> commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#saveall"))));
    }

    // ==================== Waypoint Tools ====================

    private void registerWpSave() {
        JsonObject schema = schemaBuilder()
            .addProperty("name", "string", "Waypoint name", true)
            .addProperty("x", "integer", "X coordinate", false)
            .addProperty("y", "integer", "Y coordinate", false)
            .addProperty("z", "integer", "Z coordinate", false)
            .build();

        register("wp_save", "Save a waypoint", schema, true,
            (args, session) -> {
                StringBuilder cmd = new StringBuilder("#wp save ").append(getRequiredString(args, "name"));
                if (args.has("x") && args.has("y") && args.has("z")) {
                    cmd.append(" ").append(args.get("x").getAsInt())
                       .append(" ").append(args.get("y").getAsInt())
                       .append(" ").append(args.get("z").getAsInt());
                }
                return commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs(cmd.toString())));
            });
    }

    private void registerWpDelete() {
        JsonObject schema = schemaBuilder()
            .addProperty("name", "string", "Waypoint name", true)
            .build();

        register("wp_delete", "Delete a waypoint", schema, true,
            (args, session) -> {
                String name = getRequiredString(args, "name");
                return commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#wp delete " + name)));
            });
    }

    private void registerWpList() {
        JsonObject schema = schemaBuilder()
            .addProperty("tag", "string", "Waypoint tag to filter", true)
            .build();

        register("wp_list", "List waypoints with a tag", schema, true,
            (args, session) -> {
                String tag = getRequiredString(args, "tag");
                return commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#wp list " + tag)));
            });
    }

    private void registerWpGoal() {
        JsonObject schema = schemaBuilder()
            .addProperty("name", "string", "Waypoint name", true)
            .build();

        register("wp_goal", "Set a goal to a waypoint", schema, true,
            (args, session) -> {
                String name = getRequiredString(args, "name");
                return commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#wp goal " + name)));
            });
    }

    // ==================== Settings Tools ====================

    private void registerSetSetting() {
        JsonObject schema = schemaBuilder()
            .addProperty("name", "string", "Setting name", true)
            .addProperty("value", "string", "Setting value", true)
            .build();

        register("set_setting", "Set a Baritone setting", schema, true,
            (args, session) -> {
                String name = getRequiredString(args, "name");
                String value = getRequiredString(args, "value");
                return commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#" + name + " " + value)));
            });
    }

    private void registerResetSetting() {
        JsonObject schema = schemaBuilder()
            .addProperty("name", "string", "Setting name", true)
            .build();

        register("reset_setting", "Reset a setting to default", schema, true,
            (args, session) -> {
                String name = getRequiredString(args, "name");
                return commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#" + name + " reset")));
            });
    }

    private void registerResetAllSettings() {
        register("reset_all_settings", "Reset all settings to defaults", schemaBuilder().build(), true,
            (args, session) -> commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#reset"))));
    }

    private void registerGetModifiedSettings() {
        register("get_modified_settings", "See all modified settings", schemaBuilder().build(), true,
            (args, session) -> commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#modified"))));
    }

    // ==================== Miscellaneous Tools ====================

    private void registerClick() {
        register("click", "Click the destination on screen", schemaBuilder().build(), true,
            (args, session) -> commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#click"))));
    }

    private void registerBuildOpenSchematic() {
        register("build_open_schematic", "Build the schematic open in Schematica", schemaBuilder().build(), true,
            (args, session) -> commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs("#schematica"))));
    }

    private void registerHelpCommand() {
        JsonObject schema = schemaBuilder()
            .addProperty("query", "string", "Help topic or command", false)
            .build();

        register("help_command", "Get help for Baritone commands", schema, true,
            (args, session) -> {
                StringBuilder cmd = new StringBuilder("#help");
                if (args.has("query")) {
                    cmd.append(" ").append(args.get("query").getAsString());
                }
                return commandResultToJson(commandDispatcher.dispatch("chat", createChatArgs(cmd.toString())));
            });
    }

    // ==================== Helper Methods ====================

    private void register(String name, String description, JsonObject schema, 
                         boolean requiresMainThread,
                         BiFunction<JsonObject, McpSession, JsonObject> handler) {
        tools.put(name, new ToolDefinition(name, description, schema, requiresMainThread, handler));
    }

    private String getRequiredString(JsonObject args, String key) {
        if (!args.has(key) || args.get(key).isJsonNull()) {
            throw new McpException(McpErrorCodes.INVALID_PARAMS, "Missing required parameter: " + key);
        }
        return args.get(key).getAsString();
    }

    private int getRequiredInt(JsonObject args, String key) {
        if (!args.has(key) || args.get(key).isJsonNull()) {
            throw new McpException(McpErrorCodes.INVALID_PARAMS, "Missing required parameter: " + key);
        }
        return args.get(key).getAsInt();
    }

    private void requireSession(McpSession session) {
        if (session == null) {
            throw new McpException(McpErrorCodes.INVALID_REQUEST,
                "This tool requires an active MCP session");
        }
    }

    private Set<EventManager.EventType> parseEventTypes(JsonObject args, String key) {
        if (!args.has(key) || !args.get(key).isJsonArray()) {
            throw new McpException(McpErrorCodes.INVALID_PARAMS,
                key + " must be a non-empty array");
        }

        JsonArray values = args.getAsJsonArray(key);
        if (values.isEmpty()) {
            throw new McpException(McpErrorCodes.INVALID_PARAMS,
                key + " must be a non-empty array");
        }

        Set<EventManager.EventType> eventTypes = EnumSet.noneOf(EventManager.EventType.class);
        for (JsonElement value : values) {
            if (!value.isJsonPrimitive() || !value.getAsJsonPrimitive().isString()) {
                throw new McpException(McpErrorCodes.INVALID_PARAMS,
                    key + " entries must be strings");
            }
            String name = value.getAsString().trim().toUpperCase(Locale.ROOT);
            try {
                eventTypes.add(EventManager.EventType.valueOf(name));
            } catch (IllegalArgumentException e) {
                throw new McpException(McpErrorCodes.INVALID_PARAMS,
                    "Unknown event type: " + value.getAsString());
            }
        }
        return eventTypes;
    }

    private JsonArray eventTypesToJson(Set<EventManager.EventType> eventTypes) {
        JsonArray result = new JsonArray();
        eventTypes.stream()
            .sorted()
            .forEach(type -> result.add(type.name().toLowerCase(Locale.ROOT)));
        return result;
    }

    private JsonObject commandResultToJson(CommandResult result) {
        JsonObject json = new JsonObject();
        json.addProperty("success", result.isSuccess());
        if (result.isSuccess()) {
            json.add("data", result.getData());
        } else {
            json.addProperty("error", result.getErrorMessage());
        }
        return json;
    }

    private JsonObject errorData(CommandResult result) {
        JsonObject error = new JsonObject();
        error.addProperty("error", result.getErrorMessage());
        return error;
    }

    private SchemaBuilder schemaBuilder() {
        return new SchemaBuilder();
    }

    /**
     * Helper to create chat command arguments.
     */
    private JsonObject createChatArgs(String message) {
        JsonObject args = new JsonObject();
        args.addProperty("message", message);
        return args;
    }

    /**
     * Builder for JSON Schema objects.
     */
    private static class SchemaBuilder {
        private final JsonObject properties = new JsonObject();
        private final JsonArray required = new JsonArray();

        public SchemaBuilder addProperty(String name, String type, String description, boolean isRequired) {
            JsonObject prop = new JsonObject();
            prop.addProperty("type", type);
            prop.addProperty("description", description);
            properties.add(name, prop);
            
            if (isRequired) {
                required.add(name);
            }
            return this;
        }

        public JsonObject build() {
            JsonObject schema = new JsonObject();
            schema.addProperty("type", "object");
            schema.add("properties", properties);
            if (required.size() > 0) {
                schema.add("required", required);
            }
            return schema;
        }
    }
}
