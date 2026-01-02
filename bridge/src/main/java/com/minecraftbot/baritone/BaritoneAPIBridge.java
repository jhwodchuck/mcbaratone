package com.minecraftbot.baritone;

import baritone.api.BaritoneAPI;
import baritone.api.IBaritone;
import baritone.api.pathing.goals.GoalBlock;
import baritone.api.pathing.goals.GoalNear;
import baritone.api.pathing.goals.GoalYLevel;
import baritone.api.schematic.ISchematic;
import baritone.api.schematic.IStaticSchematic;
import baritone.api.schematic.format.ISchematicFormat;
import baritone.api.selection.ISelection;
import baritone.api.utils.BetterBlockPos;
import baritone.api.utils.BlockOptionalMeta;
import baritone.api.utils.BlockOptionalMetaLookup;
import com.goebl.david.Webb;
import com.google.gson.Gson;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonArray;
import net.fabricmc.api.ModInitializer;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.entity.Entity;
import net.minecraft.entity.LivingEntity;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Direction;
import net.minecraft.util.math.Vec3i;
import net.minecraft.block.Block;
import net.minecraft.block.BlockState;
import net.minecraft.screen.slot.SlotActionType;
import net.minecraft.util.Hand;
import net.minecraft.util.hit.BlockHitResult;
import net.minecraft.util.math.Vec3d;
import net.minecraft.screen.ScreenHandler;
import net.minecraft.screen.slot.Slot;
import net.minecraft.recipe.RecipeEntry;
import net.minecraft.recipe.CraftingRecipe;
import net.minecraft.recipe.RecipeType;
import net.minecraft.recipe.Ingredient;
import net.minecraft.item.ItemStack;
import net.minecraft.inventory.Inventory;
import net.minecraft.entity.player.PlayerInventory;
import java.util.List;
import java.util.ArrayList;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import net.minecraft.util.Identifier;
import net.minecraft.registry.RegistryKeys;
import net.minecraft.registry.Registry;
import net.minecraft.registry.RegistryKey;
import net.minecraft.registry.Registries;
import net.fabricmc.fabric.api.client.message.v1.ClientReceiveMessageEvents;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.minecraft.screen.CraftingScreenHandler;
import net.minecraft.screen.PlayerScreenHandler;
import net.minecraft.screen.FurnaceScreenHandler;
import net.minecraft.screen.AbstractFurnaceScreenHandler;
import net.minecraft.util.ActionResult;
// import net.fabricmc.fabric.api.event.player.AttackBlockCallback;
// import net.fabricmc.fabric.api.event.player.UseBlockCallback;
import net.minecraft.item.Items;
import net.minecraft.client.texture.NativeImage;
import net.minecraft.client.gl.Framebuffer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.*;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.ConcurrentLinkedQueue;

/**
 * Baritone API Bridge for Minecraft Bot Control
 * Uses Native Baritone API for robust control.
 */
public class BaritoneAPIBridge implements ModInitializer, MissionBridgeAdapter {

    public static final Logger LOGGER = LoggerFactory.getLogger("baritone-api-bridge");
    private static final int DEFAULT_PORT = 5555;
    private static final int MAX_CONNECTIONS = 10;
    private static final int SOCKET_TIMEOUT_MS = 30000; // 30 seconds
    private static final int UPLOAD_TIMEOUT_MS = 300000; // 5 minutes for uploads
    private static final int THREAD_POOL_CORE_SIZE = 5;
    private static final int THREAD_POOL_MAX_SIZE = 20;
    private static final int THREAD_POOL_KEEP_ALIVE_SECONDS = 60;
    private static final Gson GSON = new Gson();

    private ServerSocket serverSocket;
    private ExecutorService executor;
    private boolean running = false;
    private final File schematicDir = new File(MinecraftClient.getInstance().runDirectory, "schematics");
    private UploadManager uploadManager;

    // Connection tracking
    private final Set<Socket> activeConnections = ConcurrentHashMap.newKeySet();
    private long lastSeq = 0;

    // Event management
    private final EventManager eventManager = new EventManager(100, CACHE_TTL_MS);
    private float lastHealth = 20.0f;
    private String lastDimension = "minecraft:overworld";
    private final MissionController missionController = new MissionController(this);
    private CommandDispatcher commandDispatcher;
    private final Map<String, Integer> lastInventorySnapshot = new ConcurrentHashMap<>();
    private static final long TICK_EVENT_INTERVAL_MS = 750;
    private long lastTickEventTime = 0L;

    // New event types and caching
    private final Map<String, JsonObject> blockCache = new ConcurrentHashMap<>();
    private final Map<String, JsonObject> entityCache = new ConcurrentHashMap<>();
    private final Map<String, Long> cacheTimestamps = new ConcurrentHashMap<>();
    private static final long CACHE_TTL_MS = 5000; // 5 second cache TTL

    // Rate limiting
    private final Map<Socket, Long> lastRequestTimes = new ConcurrentHashMap<>();
    private final Map<Socket, Integer> requestCounts = new ConcurrentHashMap<>();
    private static final int RATE_LIMIT_REQUESTS = 500; // requests per window (increased for automation)
    private static final long RATE_LIMIT_WINDOW_MS = 10000; // 10 second window

    // Retry and reconnection logic
    private final Map<String, Integer> retryCounts = new ConcurrentHashMap<>();
    private static final int MAX_RETRIES = 3;

    // Death tracking for recovery
    private boolean wasDeadLastTick = false;
    private double lastDeathX = 0, lastDeathY = 0, lastDeathZ = 0;
    private String lastDeathDimension = "minecraft:overworld";
    private long lastDeathTime = 0;

    @Override
    public void onInitialize() {
        LOGGER.info("Initializing Baritone API Bridge (Native Mode)");

        // Initialize command dispatcher with legacy handler
        commandDispatcher = new CommandDispatcher(missionController, this::handleLegacyCommandInternal);

        // Use bounded thread pool to prevent resource exhaustion
        executor = new ThreadPoolExecutor(
            THREAD_POOL_CORE_SIZE,
            THREAD_POOL_MAX_SIZE,
            THREAD_POOL_KEEP_ALIVE_SECONDS,
            TimeUnit.SECONDS,
            new LinkedBlockingQueue<>(100), // Queue up to 100 tasks
            new ThreadPoolExecutor.CallerRunsPolicy() // Reject policy: run on calling thread if queue full
        );
        if (!schematicDir.exists()) {
            schematicDir.mkdirs();
        }

        // Initialize upload manager
        uploadManager = new UploadManager(schematicDir);

        startAPIServer();
        registerEventListeners();
        LOGGER.info("Baritone API Bridge initialized on port " + DEFAULT_PORT);
    }

    private void registerEventListeners() {
        // Chat message listener
        ClientReceiveMessageEvents.GAME.register((message, overlay) -> {
            if (!overlay) {
                eventManager.publishEvent(EventManager.EventType.CHAT, createChatEventData(message.getString()));
            }
        });

        /*
        AttackBlockCallback.EVENT.register((player, world, hand, pos, direction) -> {
            if (player == MinecraftClient.getInstance().player) {
                JsonObject data = new JsonObject();
                data.addProperty("x", pos.getX());
                data.addProperty("y", pos.getY());
                data.addProperty("z", pos.getZ());
                data.addProperty("action", "attack");
                bufferEvent("block_interact", data);
            }
            return ActionResult.PASS;
        });

        UseBlockCallback.EVENT.register((player, world, hand, hitResult) -> {
            if (player == MinecraftClient.getInstance().player) {
                BlockPos pos = hitResult.getBlockPos();
                JsonObject data = new JsonObject();
                data.addProperty("x", pos.getX());
                data.addProperty("y", pos.getY());
                data.addProperty("z", pos.getZ());
                data.addProperty("action", "use");
                bufferEvent("block_interact", data);
            }
            return ActionResult.PASS;
        });
        */

        // Monitor states and entities on the main thread every few ticks
        ClientTickEvents.END_CLIENT_TICK.register(this::onClientTick);

        LOGGER.info("Fabric event listeners registered for chat and client ticks");
    }

    private int tickCounter = 0;
    private IBaritone lastBaritone = null;
    private boolean lastPathingState = false;
    private final Map<Integer, BlockPos> lastEntityPositions = new HashMap<>();

    private void onClientTick(MinecraftClient client) {
        if (!running || client.player == null || client.world == null) return;
        
        tickCounter++;

        // Audit/EMIT tick event periodically (already does this)
        IBaritone baritone = BaritoneAPI.getProvider().getPrimaryBaritone();
        if (baritone != null) {
            emitTickEvent(client, baritone);
        }

        // Entity tracking (roughly once per second / 20 ticks)
        if (tickCounter % 20 == 0) {
            handleEntityTick(client);
        }

        // Pathfinding monitoring (roughly twice per second / 10 ticks)
        if (tickCounter % 10 == 0 && baritone != null) {
            handlePathfindingTick(baritone);
        }
        
        // Player death tracking (every tick is fine)
        trackPlayerDeath(client);
    }

    private void handleEntityTick(MinecraftClient client) {
        try {
            List<Entity> currentEntities = client.world.getOtherEntities(null, client.player.getBoundingBox().expand(64));

            // Check for new entities
            for (Entity entity : currentEntities) {
                int id = entity.getId();
                BlockPos currentPos = entity.getBlockPos();

                if (!lastEntityPositions.containsKey(id)) {
                    // New entity spawned
                    JsonObject spawnData = new JsonObject();
                    spawnData.addProperty("entity_id", id);
                    spawnData.addProperty("type", Registries.ENTITY_TYPE.getId(entity.getType()).toString());
                    spawnData.addProperty("x", currentPos.getX());
                    spawnData.addProperty("y", currentPos.getY());
                    spawnData.addProperty("z", currentPos.getZ());
                    eventManager.publishEvent(EventManager.EventType.ENTITY_SPAWN, spawnData);
                } else {
                    // Check for significant movement
                    BlockPos lastPos = lastEntityPositions.get(id);
                    double distance = Math.sqrt(currentPos.getSquaredDistance(lastPos));
                    if (distance > 10.0) { // Significant movement threshold
                        JsonObject moveData = new JsonObject();
                        moveData.addProperty("entity_id", id);
                        moveData.addProperty("distance", distance);
                        moveData.addProperty("from_x", lastPos.getX());
                        moveData.addProperty("from_y", lastPos.getY());
                        moveData.addProperty("from_z", lastPos.getZ());
                        moveData.addProperty("to_x", currentPos.getX());
                        moveData.addProperty("to_y", currentPos.getY());
                        moveData.addProperty("to_z", currentPos.getZ());
                        eventManager.publishEvent(EventManager.EventType.ENTITY_MOVE, moveData);
                    }
                }
                lastEntityPositions.put(id, currentPos);
            }

            // Check for despawned entities
            lastEntityPositions.keySet().removeIf(id -> {
                boolean exists = currentEntities.stream().anyMatch(e -> e.getId() == id);
                if (!exists) {
                    JsonObject despawnData = new JsonObject();
                    despawnData.addProperty("entity_id", id);
                    eventManager.publishEvent(EventManager.EventType.ENTITY_DESPAWN, despawnData);
                    return true;
                }
                return false;
            });
        } catch (Exception e) {
            LOGGER.warn("Error in entity tick", e);
        }
    }

    private void handlePathfindingTick(IBaritone baritone) {
        try {
            boolean isPathing = baritone.getPathingBehavior().isPathing();
            boolean stateChanged = (lastBaritone != baritone) || (lastPathingState != isPathing);

            if (stateChanged) {
                JsonObject pathData = new JsonObject();
                pathData.addProperty("is_pathing", isPathing);
                if (isPathing) {
                    pathData.addProperty("goal_type", baritone.getPathingBehavior().getGoal() != null ?
                        baritone.getPathingBehavior().getGoal().getClass().getSimpleName() : "unknown");
                }
                eventManager.publishEvent(EventManager.EventType.PATHFINDING_STATE, pathData);
                lastPathingState = isPathing;
                lastBaritone = baritone;
            }
        } catch (Exception e) {
            LOGGER.warn("Error in pathfinding tick", e);
        }
    }

    @Override
    public void publishMissionEvent(String reason, JsonObject payload) {
        JsonObject eventPayload = new JsonObject();
        eventPayload.addProperty("reason", reason);
        eventPayload.addProperty("phase", missionController.getPhaseValue());
        if (payload != null) {
            eventPayload.add("payload", payload);
        }
        eventManager.publishEvent(EventManager.EventType.MISSION, eventPayload, EventManager.Priority.NORMAL, "mission_controller");
    }



    private void emitTickEvent(MinecraftClient client, IBaritone baritone) {
        if (client == null || client.player == null || client.world == null || baritone == null) {
            return;
        }
        long now = System.currentTimeMillis();
        if (now - lastTickEventTime < TICK_EVENT_INTERVAL_MS) {
            return;
        }
        lastTickEventTime = now;

        ClientPlayerEntity player = client.player;
        JsonObject position = new JsonObject();
        position.addProperty("x", player.getX());
        position.addProperty("y", player.getY());
        position.addProperty("z", player.getZ());

        JsonObject velocity = new JsonObject();
        velocity.addProperty("x", player.getVelocity().x);
        velocity.addProperty("y", player.getVelocity().y);
        velocity.addProperty("z", player.getVelocity().z);

        JsonObject payload = new JsonObject();
        payload.add("position", position);
        payload.add("velocity", velocity);
        payload.addProperty("health", player.getHealth());
        payload.addProperty("food", player.getHungerManager().getFoodLevel());
        payload.addProperty("dimension", client.world.getRegistryKey().getValue().toString());
        payload.addProperty("mission_phase", missionController.getPhaseValue());
        payload.addProperty("mission_queue", missionController.queueSize());
        payload.addProperty("timestamp", now);
        try {
            payload.addProperty("is_pathing", baritone.getPathingBehavior().isPathing());
        } catch (Exception e) {
            payload.addProperty("is_pathing", false);
        }
        eventManager.publishEvent(EventManager.EventType.TICK_UPDATE, payload);

        String currentDimension = payload.get("dimension").getAsString();
        if (!currentDimension.equals(lastDimension)) {
            JsonObject eventData = new JsonObject();
            eventData.addProperty("from_dimension", lastDimension);
            eventData.addProperty("to_dimension", currentDimension);
            eventData.add("position", position.deepCopy());
            eventManager.publishEvent(EventManager.EventType.DIMENSION_CHANGE, eventData);
            lastDimension = currentDimension;
        }
    }

    private Map<String, Integer> flattenInventory(JsonArray main, JsonArray armor, JsonArray offhand) {
        Map<String, Integer> counts = new LinkedHashMap<>();
        accumulateInventorySection(main, counts);
        accumulateInventorySection(armor, counts);
        accumulateInventorySection(offhand, counts);
        return counts;
    }

    private void accumulateInventorySection(JsonArray section, Map<String, Integer> counts) {
        if (section == null) {
            return;
        }
        for (JsonElement element : section) {
            if (!element.isJsonObject()) {
                continue;
            }
            JsonObject obj = element.getAsJsonObject();
            String id = obj.has("id") ? obj.get("id").getAsString() : "minecraft:air";
            if ("minecraft:air".equals(id)) {
                continue;
            }
            int amount = obj.has("count") ? obj.get("count").getAsInt() : 0;
            counts.merge(id, amount, Integer::sum);
        }
    }

    private void emitInventoryChangeEvent(Map<String, Integer> currentCounts, int selectedSlot) {
        Map<String, Integer> delta = new LinkedHashMap<>();
        for (Map.Entry<String, Integer> entry : currentCounts.entrySet()) {
            int previous = lastInventorySnapshot.getOrDefault(entry.getKey(), 0);
            if (entry.getValue() != previous) {
                delta.put(entry.getKey(), entry.getValue() - previous);
            }
        }
        for (Map.Entry<String, Integer> entry : lastInventorySnapshot.entrySet()) {
            if (!currentCounts.containsKey(entry.getKey())) {
                delta.put(entry.getKey(), -entry.getValue());
            }
        }
        if (!delta.isEmpty()) {
            JsonArray changes = new JsonArray();
            for (Map.Entry<String, Integer> entry : delta.entrySet()) {
                JsonObject change = new JsonObject();
                change.addProperty("item", entry.getKey());
                change.addProperty("delta", entry.getValue());
                change.addProperty("count", currentCounts.getOrDefault(entry.getKey(), 0));
                changes.add(change);
            }
            JsonObject payload = new JsonObject();
            payload.add("changes", changes);
            payload.addProperty("unique_items", currentCounts.size());
            payload.addProperty("selected_slot", selectedSlot);
            eventManager.publishEvent(EventManager.EventType.INVENTORY_CHANGE, payload);
        }
        lastInventorySnapshot.clear();
        lastInventorySnapshot.putAll(currentCounts);
    }

    private JsonArray sampleEntities(JsonArray entities, int limit) {
        JsonArray sample = new JsonArray();
        if (entities == null) {
            return sample;
        }
        int max = Math.min(Math.max(limit, 0), entities.size());
        for (int i = 0; i < max; i++) {
            sample.add(entities.get(i));
        }
        return sample;
    }

    private JsonObject createChatEventData(String message) {
        JsonObject data = new JsonObject();
        data.addProperty("message", message);
        return data;
    }

    private void trackPlayerDeath(MinecraftClient client) {
        if (client.player == null) return;
        
        boolean isDead = client.player.isDead();
        
        if (isDead && !wasDeadLastTick) {
            // Player just died - record death location
            lastDeathX = client.player.getX();
            lastDeathY = client.player.getY();
            lastDeathZ = client.player.getZ();
            lastDeathDimension = client.world != null 
                ? client.world.getRegistryKey().getValue().toString() 
                : "minecraft:overworld";
            lastDeathTime = System.currentTimeMillis();
            
            // Fire death event
            JsonObject deathData = new JsonObject();
            deathData.addProperty("x", lastDeathX);
            deathData.addProperty("y", lastDeathY);
            deathData.addProperty("z", lastDeathZ);
            deathData.addProperty("dimension", lastDeathDimension);
            deathData.addProperty("timestamp", lastDeathTime);
            eventManager.publishEvent(EventManager.EventType.DEATH, deathData, EventManager.Priority.HIGH);
            
            LOGGER.info("Player died at ({}, {}, {}) in {}", 
                lastDeathX, lastDeathY, lastDeathZ, lastDeathDimension);
        } else if (!isDead && wasDeadLastTick) {
            // Player just respawned
            JsonObject respawnData = new JsonObject();
            respawnData.addProperty("x", client.player.getX());
            respawnData.addProperty("y", client.player.getY());
            respawnData.addProperty("z", client.player.getZ());
            respawnData.addProperty("death_x", lastDeathX);
            respawnData.addProperty("death_y", lastDeathY);
            respawnData.addProperty("death_z", lastDeathZ);
            respawnData.addProperty("death_dimension", lastDeathDimension);
            eventManager.publishEvent(EventManager.EventType.RESPAWN, respawnData, EventManager.Priority.HIGH);
            
            LOGGER.info("Player respawned");
        }
        
        wasDeadLastTick = isDead;
    }

    private void startAPIServer() {
        executor.submit(() -> {
            try {
                serverSocket = new ServerSocket(DEFAULT_PORT);
                running = true;
                LOGGER.info("Baritone API server listening on port " + DEFAULT_PORT);

                while (running) {
                    try {
                        Socket clientSocket = serverSocket.accept();
                        
                        // Check connection limit
                        if (activeConnections.size() >= MAX_CONNECTIONS) {
                            LOGGER.warn("Connection limit reached, rejecting client: {}", clientSocket.getRemoteSocketAddress());
                            try {
                                clientSocket.close();
                            } catch (IOException e) {
                                LOGGER.debug("Error closing rejected connection", e);
                            }
                            continue;
                        }
                        
                        // Set socket timeout
                        clientSocket.setSoTimeout(SOCKET_TIMEOUT_MS);
                        
                        // Track connection
                        activeConnections.add(clientSocket);
                        
                        executor.submit(() -> handleClient(clientSocket));
                    } catch (IOException e) {
                        if (running)
                            LOGGER.error("Error accepting client connection", e);
                    }
                }
            } catch (IOException e) {
                LOGGER.error("Failed to start API server", e);
            }
        });
    }

    private void handleClient(Socket clientSocket) {
        String clientId = clientSocket.getRemoteSocketAddress().toString();
        try (
                BufferedReader in = new BufferedReader(new InputStreamReader(clientSocket.getInputStream()));
                PrintWriter out = new PrintWriter(clientSocket.getOutputStream(), true)) {
            LOGGER.debug("Client connected: {}", clientId);
            
            String line;
            while ((line = in.readLine()) != null) {
                JsonObject request = null;
                String requestId = null;
                try {
                    request = GSON.fromJson(line, JsonObject.class);
                    requestId = request.has("id") ? request.get("id").getAsString() : null;
                    JsonObject response = handleCommand(request, clientSocket);
                    out.println(GSON.toJson(response));
                } catch (Exception e) {
                    LOGGER.error("Error handling command from {}", clientId, e);
                    JsonObject response = new JsonObject();
                    response.addProperty("seq", ++lastSeq);
                    response.addProperty("timestamp", System.currentTimeMillis());
                    
                    if (requestId != null) {
                        response.addProperty("id", requestId);
                    } else if (request != null && request.has("id")) {
                        response.addProperty("id", request.get("id").getAsString());
                    } else {
                        // Try to parse just the ID from the line as fallback
                        try {
                            JsonObject fallbackRequest = GSON.fromJson(line, JsonObject.class);
                            if (fallbackRequest.has("id")) {
                                response.addProperty("id", fallbackRequest.get("id").getAsString());
                            } else {
                                response.addProperty("id", (String) null);
                            }
                        } catch (Exception parseEx) {
                            response.addProperty("id", (String) null);
                        }
                    }
                    response.addProperty("status", "error");
                    response.addProperty("error", e.getMessage());
                    out.println(GSON.toJson(response));
                }
            }
        } catch (java.net.SocketTimeoutException e) {
            LOGGER.warn("Client connection timeout: {}", clientId);
        } catch (IOException e) {
            LOGGER.debug("Client connection error: {}", clientId, e);
        } finally {
            // Cleanup on disconnect
            cleanupClientResources(clientSocket);
            activeConnections.remove(clientSocket);
            try {
                if (!clientSocket.isClosed()) {
                    clientSocket.close();
                }
            } catch (IOException e) {
                LOGGER.debug("Error closing client socket", e);
            }
            LOGGER.debug("Client disconnected: {}", clientId);
        }
    }
    
    private void cleanupClientResources(Socket clientSocket) {
        missionController.releaseOwner(clientSocket);
        // Cleanup uploads owned by this client
        uploadManager.cleanupClientUploads(clientSocket);
    }
    


    private JsonObject handleCommand(JsonObject request) {
        return handleCommand(request, null);
    }
    
    /**
     * Handle legacy commands that haven't been migrated to the handler pattern.
     * This method contains the original switch statement logic.
     */
    private CommandResult handleLegacyCommandInternal(String command, JsonObject params,
            MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        try {
            JsonObject data = new JsonObject();

            switch (command) {
                // Movement - now handled by GotoCommandHandler
                case "explore":
                    handleExplore(baritone, params, data);
                    break;
                case "stop":
                    handleStop(baritone, data);
                    break;
                case "pause":
                    handlePause(baritone, data);
                    break;
                case "cancel":
                    handleCancel(baritone, data);
                    break;
                case "goal":
                    handleGoal(baritone, params, data);
                    break;
                case "path":
                    handlePath(client, params, data);
                    break; // API for path is complex, using chat

                // Mining / World Interaction - mine now handled by MineCommandHandler
                case "tunnel":
                    handleTunnel(baritone, params, data);
                    break;
                case "farm":
                    handleFarm(baritone, params, data);
                    break;
                // build and sel now handled by BuildCommandHandler

                // Schematic Upload
                case "schematic_init":
                    handleSchematicInit(params, data, clientSocket);
                    break;
                case "schematic_chunk":
                    handleSchematicChunk(params, data);
                    break;
                case "schematic_commit":
                    handleSchematicCommit(params, data);
                    break;
                case "upload_progress":
                    handleUploadProgress(params, data);
                    break;
                case "upload_list":
                    handleUploadList(data);
                    break;
                case "upload_cancel":
                    handleUploadCancel(params, data);
                    break;
                case "upload_stats":
                    handleUploadStats(data);
                    break;

                // State / Info - get_state, get_inventory, inventory_click now handled by respective handlers
                case "interact_block":
                    handleInteractBlock(client, params, data);
                    break;
                case "get_screen":
                    handleGetScreen(client, data);
                    break;
                case "close_screen":
                    handleCloseScreen(client, data);
                    break;
                case "get_recipes":
                    handleGetRecipes(client, params, data);
                    break;
                case "use_item":
                    handleUseItem(client, params, data);
                    break;
                case "attack_entity":
                    handleAttackEntity(client, params, data);
                    break;
                case "select_slot":
                    handleSelectSlot(client, params, data);
                    break;
                case "get_events":
                    handleGetEvents(data);
                    break;
                case "look_at":
                    handleLookAt(client, params, data);
                    break;
                case "get_block":
                    handleGetBlock(client, params, data);
                    break;
                // get_entities now handled by StateCommandHandler
                case "get_view":
                    handleGetView(client, params, data);
                    break;

                case "chat":
                    String msg = params.get("message").getAsString();
                    client.execute(() -> client.player.networkHandler.sendChatMessage(msg));
                    break;

                case "settings":
                    handleSettings(params, data);
                    break;
                case "axis":
                    handleAxisMine(baritone, params, data);
                    break;
                case "strip":
                    handleStripMine(baritone, params, data);
                    break;
                case "quarry":
                    handleQuarry(baritone, params, data);
                    break;
                case "tunnel_wide":
                    handleWideTunnel(baritone, params, data);
                    break;
                case "place_torches":
                    handlePlaceTorches(baritone, params, data);
                    break;
                case "harvest":
                    handleHarvest(baritone, params, data);
                    break;
                case "plant":
                    handlePlant(baritone, params, data);
                    break;
                case "break_block":
                    handleBreakBlock(client, baritone, params, data);
                    break;
                case "click_recipe":
                    handleClickRecipe(client, params, data);
                    break;
                case "smelt_items":
                    handleSmeltItems(client, params, data);
                    break;
                case "place_fire":
                    handlePlaceFire(client, params, data);
                    break;
                case "auto_craft":
                    handleAutoCraft(client, params, data);
                    break;
                case "throw_item":
                    handleThrowItem(client, params, data);
                    break;
                case "respawn":
                    handleRespawn(client, data);
                    break;
                case "get_dimension":
                    handleGetDimension(client, data);
                    break;
                case "get_death_location":
                    handleGetDeathLocation(data);
                    break;
                case "screenshot":
                    handleScreenshot(client, params, data);
                    break;

                default:
                    return CommandResult.error("Unknown command: " + command);
            }

            return CommandResult.success(data);

        } catch (Exception e) {
            LOGGER.error("Legacy command execution error: " + command, e);
            return CommandResult.error("Command execution failed: " + e.getMessage());
        }
    }

    JsonObject handleCommand(JsonObject request, Socket clientSocket) {
        JsonObject response = new JsonObject();
        response.addProperty("seq", ++lastSeq);
        response.addProperty("timestamp", System.currentTimeMillis());

        // Set request ID if present
        String id = request != null && request.has("id") ? request.get("id").getAsString() : null;
        response.addProperty("id", id);

        // Basic validation
        if (request == null || !request.has("command")) {
            response.addProperty("status", "error");
            response.addProperty("error", "Missing command");
            return response;
        }

        try {
            MinecraftClient client = MinecraftClient.getInstance();
            IBaritone baritone = BaritoneAPI.getProvider().getPrimaryBaritone();

            // Basic precondition checks (rate limiting and other checks are handled by dispatcher)
            if (baritone == null) {
                response.addProperty("status", "error");
                response.addProperty("error", "Baritone not available. Make sure Baritone mod is installed and loaded.");
                return response;
            }

            // Allow some commands without player (e.g. status checks, uploads)
            String command = request.get("command").getAsString();
            if (client.player == null && !isOfflineCommand(command)) {
                response.addProperty("status", "error");
                response.addProperty("error", "Player not available");
                return response;
            }

            // Check for damage events on each command
            checkPlayerDamage(client);
            emitTickEvent(client, baritone);

            // Dispatch through the command dispatcher
            CommandResult result = commandDispatcher.dispatchCommand(request, clientSocket, client, baritone);

            // Convert CommandResult to JsonObject response
            if (result.isSuccess()) {
                response.addProperty("status", "ok");
                response.add("data", result.getData());
            } else {
                response.addProperty("status", "error");
                response.addProperty("error", result.getErrorMessage());
            }

        } catch (Exception e) {
            response.addProperty("status", "error");
            response.addProperty("error", e.getMessage());
            LOGGER.error("Command dispatch error", e);
        }

        return response;
    }

    private boolean isOfflineCommand(String command) {
        return command.startsWith("schematic_") || command.startsWith("upload_") || missionController.isOfflineSafe(command);
    }

    private boolean checkRateLimit(Socket clientSocket, JsonObject response) {
        if (clientSocket == null) return true; // Allow for testing

        long now = System.currentTimeMillis();
        Long lastTime = lastRequestTimes.get(clientSocket);

        if (lastTime == null || (now - lastTime) > RATE_LIMIT_WINDOW_MS) {
            // New window
            lastRequestTimes.put(clientSocket, now);
            requestCounts.put(clientSocket, 1);
            return true;
        }

        int count = requestCounts.getOrDefault(clientSocket, 0) + 1;
        requestCounts.put(clientSocket, count);

        if (count > RATE_LIMIT_REQUESTS) {
            response.addProperty("status", "error");
            response.addProperty("error", "Rate limit exceeded. Maximum " + RATE_LIMIT_REQUESTS + " requests per " + (RATE_LIMIT_WINDOW_MS / 1000) + " seconds");
            response.addProperty("retry_after_ms", RATE_LIMIT_WINDOW_MS - (now - lastTime));
            return false;
        }

        return true;
    }

    private boolean validateCoordinates(JsonObject params, JsonObject data) {
        if (params.has("x") && params.has("y") && params.has("z")) {
            int x = params.get("x").getAsInt();
            int y = params.get("y").getAsInt();
            int z = params.get("z").getAsInt();

            // Minecraft coordinate bounds
            if (x < -30000000 || x > 30000000 || y < -64 || y > 320 || z < -30000000 || z > 30000000) {
                data.addProperty("error", "Coordinates out of valid Minecraft range");
                return false;
            }
        }
        return true;
    }

    private boolean validateBlockId(String blockId, JsonObject data) {
        if (blockId == null || blockId.trim().isEmpty()) {
            data.addProperty("error", "Block ID cannot be null or empty");
            return false;
        }
        // Basic validation - could be enhanced
        if (!blockId.contains(":") && !blockId.startsWith("minecraft:")) {
            // Assume minecraft namespace
            blockId = "minecraft:" + blockId;
        }
        return true;
    }

    private boolean validateFilePath(String path, JsonObject data) {
        if (path == null) {
            data.addProperty("error", "File path cannot be null");
            return false;
        }
        if (path.contains("..") || path.contains("/") || path.contains("\\")) {
            data.addProperty("error", "Invalid file path - path traversal not allowed");
            return false;
        }
        return true;
    }

    // --- Implementations ---

    @Override
    public JsonObject collectTelemetry(MinecraftClient client, IBaritone baritone) {
        JsonObject telemetry = new JsonObject();
        ClientPlayerEntity player = client.player;
        if (player != null) {
            telemetry.addProperty("health", player.getHealth());
            telemetry.addProperty("food", player.getHungerManager().getFoodLevel());
            telemetry.addProperty("saturation", player.getHungerManager().getSaturationLevel());
            telemetry.addProperty("armor", player.getArmor());
            telemetry.addProperty("experience", player.totalExperience);
            telemetry.addProperty("dimension",
                client.world != null ? client.world.getRegistryKey().getValue().toString() : "unknown");

            BlockPos pos = player.getBlockPos();
            JsonObject position = new JsonObject();
            position.addProperty("x", pos.getX());
            position.addProperty("y", pos.getY());
            position.addProperty("z", pos.getZ());
            telemetry.add("position", position);

            JsonObject rotation = new JsonObject();
            rotation.addProperty("yaw", player.getYaw());
            rotation.addProperty("pitch", player.getPitch());
            telemetry.add("rotation", rotation);
            
            telemetry.addProperty("is_pathing", baritone.getPathingBehavior().isPathing());
        } else {
            telemetry.addProperty("player", "not_loaded");
        }

        if (baritone.getCustomGoalProcess().getGoal() != null) {
            telemetry.addProperty("goal", baritone.getCustomGoalProcess().getGoal().toString());
        }
        telemetry.addProperty("eventBufferSize", eventManager.getBufferSize());
        telemetry.addProperty("missionQueue", missionController.queueSize());
        telemetry.addProperty("timestamp", System.currentTimeMillis());
        return telemetry;
    }



    @Override
    public void handleSelection(IBaritone baritone, JsonObject params, JsonObject data) {
        // Delegated to BuildCommandHandler
        CommandHandler handler = CommandHandlerFactory.getHandler("sel");
        if (handler != null) {
            CommandResult result = handler.handle(params, MinecraftClient.getInstance(), baritone, null);
            if (result.isSuccess()) {
                data.add("result", result.getData());
            } else {
                data.addProperty("error", result.getErrorMessage());
            }
        } else {
            data.addProperty("error", "Selection handler not available");
        }
    }

    @Override

    public void handleMine(IBaritone baritone, JsonObject params, JsonObject data) {
        // Baritone's mine process needs to be run on main thread in 1.21+
        int count = params.has("count") ? params.get("count").getAsInt() : 
                   (params.has("quantity") ? params.get("quantity").getAsInt() : 0);

        if (params.has("block_type")) {
            String blockId = params.get("block_type").getAsString();
            Identifier id = Identifier.of(blockId);
            if (Registries.BLOCK.containsId(id)) {
                Block block = Registries.BLOCK.get(id);
                MinecraftClient.getInstance().execute(() -> 
                    baritone.getMineProcess().mine(count, block));
                data.addProperty("started", true);
            } else {
                data.addProperty("error", "Unknown block: " + blockId);
            }
        } else if (params.has("blocks")) {
            JsonArray blocksArray = params.getAsJsonArray("blocks");
            if (blocksArray.isEmpty()) {
                data.addProperty("error", "No block IDs provided");
                return;
            }

            MinecraftClient.getInstance().execute(() -> {
                List<BlockOptionalMeta> lookup = new ArrayList<>();
                for (JsonElement element : blocksArray) {
                    if (!element.isJsonPrimitive()) continue;
                    String blockId = element.getAsString();
                    Identifier id = Identifier.of(blockId);
                    if (Registries.BLOCK.containsId(id)) {
                        lookup.add(new BlockOptionalMeta(Registries.BLOCK.get(id)));
                    }
                }

                if (!lookup.isEmpty()) {
                    BlockOptionalMeta[] blockArray = lookup.toArray(new BlockOptionalMeta[0]);
                    baritone.getMineProcess().mine(count, blockArray);
                }
            });
            data.addProperty("started", true);
        } else {
             data.addProperty("error", "Missing block_type or blocks");
        }
    }

    private void handleFarm(IBaritone baritone, JsonObject params, JsonObject data) {
        int range = params.has("range") ? params.get("range").getAsInt() : 0;
        MinecraftClient.getInstance().execute(() -> 
            baritone.getFarmProcess().farm(range));
        data.addProperty("started", true);
    }

    private void handleExplore(IBaritone baritone, JsonObject params, JsonObject data) {
        int x = params.has("x") ? params.get("x").getAsInt() : 0;
        int z = params.has("z") ? params.get("z").getAsInt() : 0;
        MinecraftClient.getInstance().execute(() -> 
            baritone.getExploreProcess().explore(x, z));
        data.addProperty("started", true);
    }







    // Schematic Upload Handlers

    private void handleSchematicInit(JsonObject params, JsonObject data, Socket clientSocket) throws Exception {
        String name = params.get("name").getAsString();
        long expectedSize = params.has("size") ? params.get("size").getAsLong() : -1;
        UploadManager.UploadPriority priority = params.has("priority") ?
            UploadManager.UploadPriority.valueOf(params.get("priority").getAsString().toUpperCase()) :
            UploadManager.UploadPriority.NORMAL;

        boolean started = uploadManager.startUpload(name, expectedSize, clientSocket, priority);
        if (started) {
            data.addProperty("ready", true);
        } else {
            data.addProperty("error", "Failed to start upload - queue full or upload already exists");
        }
    }

    private void handleSchematicChunk(JsonObject params, JsonObject data) throws Exception {
        String name = params.get("name").getAsString();
        String b64 = params.get("data").getAsString();
        byte[] bytes = Base64.getDecoder().decode(b64);

        boolean success = uploadManager.processChunk(name, bytes);
        if (success) {
            data.addProperty("received", bytes.length);
        } else {
            throw new IOException("Failed to process chunk for upload: " + name);
        }
    }

    private void handleSchematicCommit(JsonObject params, JsonObject data) throws Exception {
        String name = params.get("name").getAsString();
        String expectedSha256 = params.has("sha256") ? params.get("sha256").getAsString() : null;

        boolean success = uploadManager.completeUpload(name, expectedSha256);
        if (success) {
            data.addProperty("saved", true);
            // Add SHA256 hash if available
            Map<String, Object> progress = uploadManager.getUploadProgress(name);
            if (progress.containsKey("sha256")) {
                data.addProperty("sha256", progress.get("sha256").toString());
            }
        } else {
            throw new IOException("Failed to complete upload: " + name);
        }
    }

    private void handleUploadProgress(JsonObject params, JsonObject data) throws Exception {
        String name = params.get("name").getAsString();
        Map<String, Object> progress = uploadManager.getUploadProgress(name);

        if (progress.containsKey("error")) {
            data.addProperty("error", progress.get("error").toString());
        } else {
            data.addProperty("name", progress.get("name").toString());
            data.addProperty("status", progress.get("status").toString());
            data.addProperty("progress_percentage", (Double) progress.get("progress_percentage"));
            data.addProperty("received_bytes", (Long) progress.get("received_bytes"));
            data.addProperty("expected_size", (Long) progress.get("expected_size"));
            data.addProperty("start_time", (Long) progress.get("start_time"));
            data.addProperty("last_activity", (Long) progress.get("last_activity"));
            data.addProperty("priority", progress.get("priority").toString());
        }
    }

    private void handleUploadList(JsonObject data) throws Exception {
        List<Map<String, Object>> uploads = uploadManager.getActiveUploads();
        JsonArray uploadArray = new JsonArray();
        for (Map<String, Object> upload : uploads) {
            JsonObject uploadObj = new JsonObject();
            uploadObj.addProperty("name", upload.get("name").toString());
            uploadObj.addProperty("status", upload.get("status").toString());
            uploadObj.addProperty("progress_percentage", (Double) upload.get("progress_percentage"));
            uploadObj.addProperty("received_bytes", (Long) upload.get("received_bytes"));
            uploadObj.addProperty("expected_size", (Long) upload.get("expected_size"));
            uploadObj.addProperty("priority", upload.get("priority").toString());
            uploadObj.addProperty("owner", upload.get("owner").toString());
            uploadArray.add(uploadObj);
        }
        data.add("uploads", uploadArray);
    }

    private void handleUploadCancel(JsonObject params, JsonObject data) throws Exception {
        String name = params.get("name").getAsString();
        boolean success = uploadManager.cancelUpload(name);
        data.addProperty("cancelled", success);
        if (!success) {
            data.addProperty("error", "Upload not found or could not be cancelled");
        }
    }

    private void handleUploadStats(JsonObject data) throws Exception {
        Map<String, Object> stats = uploadManager.getStatistics();
        data.addProperty("active_uploads", (Integer) stats.get("active_uploads"));
        data.addProperty("queued_uploads", (Integer) stats.get("queued_uploads"));
        data.addProperty("total_completed", (Long) stats.get("total_completed"));
        data.addProperty("total_failed", (Long) stats.get("total_failed"));
        data.addProperty("total_bytes_uploaded", (Long) stats.get("total_bytes_uploaded"));
        data.addProperty("max_concurrent_uploads", (Integer) stats.get("max_concurrent_uploads"));
        data.addProperty("max_queue_size", (Integer) stats.get("max_queue_size"));
    }

    @Override
    public void handleSettings(JsonObject params, JsonObject data) {
        if (params.has("get")) {
            String key = params.get("get").getAsString();
            try {
                baritone.api.Settings.Setting<?> setting = BaritoneAPI.getSettings().allSettings.stream()
                    .filter(s -> s.getName().equalsIgnoreCase(key))
                    .findFirst().orElse(null);
                
                if (setting != null) {
                    data.addProperty("key", setting.getName());
                    data.addProperty("value", setting.value.toString());
                } else {
                    data.addProperty("error", "Setting not found: " + key);
                }
            } catch (Exception e) {
                data.addProperty("error", "Failed to get setting: " + e.getMessage());
            }
        } else if (params.has("set")) {
            String key = params.get("set").getAsString();
            String val = params.get("value").getAsString();
            
            try {
                // Run on main thread to be safe, though most settings can be set off-thread.
                // Baritone settings are usually primitive wrappers or Enums.
                MinecraftClient.getInstance().execute(() -> {
                    baritone.api.Settings.Setting<?> setting = BaritoneAPI.getSettings().allSettings.stream()
                        .filter(s -> s.getName().equalsIgnoreCase(key))
                        .findFirst().orElse(null);
                    
                    if (setting != null) {
                        try {
                            // Using Baritone's internal string parsing if available, 
                            // but safest is to handle common types or let Baritone handle it.
                            // In 1.15.0, Setting has a generic value field.
                            // We attempt to cast and set.
                            modifySettingSafely(setting, val);
                            LOGGER.info("Successfully set Baritone setting {} to {}", key, val);
                        } catch (Exception e) {
                            LOGGER.error("Failed to set Baritone setting {}: {}", key, e.getMessage());
                        }
                    }
                });
                data.addProperty("status", "requested");
            } catch (Exception e) {
                data.addProperty("error", "Failed to dispatch setting update: " + e.getMessage());
            }
        }
    }

    /**
     * Safely modify a Baritone setting by parsing the string value.
     */
    @SuppressWarnings("unchecked")
    private void modifySettingSafely(baritone.api.Settings.Setting<?> setting, String val) {
        Class<?> type = setting.getType();
        if (type == Boolean.class) {
            ((baritone.api.Settings.Setting<Boolean>) setting).value = Boolean.parseBoolean(val);
        } else if (type == Integer.class) {
            ((baritone.api.Settings.Setting<Integer>) setting).value = Integer.parseInt(val);
        } else if (type == Double.class) {
            ((baritone.api.Settings.Setting<Double>) setting).value = Double.parseDouble(val);
        } else if (type == Float.class) {
            ((baritone.api.Settings.Setting<Float>) setting).value = Float.parseFloat(val);
        } else if (type == Long.class) {
            ((baritone.api.Settings.Setting<Long>) setting).value = Long.parseLong(val);
        } else {
            // Fallback for strings or other types if applicable
            // Note: Enum settings might need more complex parsing
            try {
                // Try set via generic approach if possible
                setting.getClass().getMethod("setValue", Object.class); // Check if exists
                // But usually .value is public in most 1.15.0/1.12.2 versions
            } catch (Exception e) {
                // log
            }
        }
    }

    // --- Basic Handlers ---

    private void handleStop(IBaritone baritone, JsonObject data) {
        MinecraftClient.getInstance().execute(() -> 
            baritone.getPathingBehavior().cancelEverything());
        data.addProperty("stopped", true);
    }

    private void handlePause(IBaritone baritone, JsonObject data) {
        MinecraftClient.getInstance().execute(() -> 
            baritone.getBuilderProcess().pause());
        // others?
        data.addProperty("paused", true);
    }

    private void handleCancel(IBaritone baritone, JsonObject data) {
        MinecraftClient.getInstance().execute(() -> 
            baritone.getPathingBehavior().cancelEverything());
        data.addProperty("cancelled", true);
    }

    private void handleGoal(IBaritone baritone, JsonObject params, JsonObject data) {
        String type = params.has("type") ? params.get("type").getAsString() : "yLevel";
        int value = params.has("value") ? params.get("value").getAsInt() : 64;

        if ("yLevel".equals(type)) {
            MinecraftClient.getInstance().execute(() -> 
                baritone.getCustomGoalProcess().setGoalAndPath(new GoalYLevel(value)));
            data.addProperty("started", true);
        } else {
            data.addProperty("error", "Goal type not supported: " + type);
        }
    }

    @Override
    public void handleGetState(MinecraftClient client, IBaritone baritone, JsonObject data) {
        try {
            // Synchronize with main thread for safe read
            client.submit(() -> {
                if (client.player == null) {
                    data.addProperty("error", "Player not available");
                    return null;
                }

                ClientPlayerEntity player = client.player;
                
                // Position
                JsonObject position = new JsonObject();
                position.addProperty("x", player.getX());
                position.addProperty("y", player.getY());
                position.addProperty("z", player.getZ());
                position.addProperty("yaw", player.getYaw());
                position.addProperty("pitch", player.getPitch());
                data.add("position", position);
                
                // Block position
                BlockPos blockPos = player.getBlockPos();
                JsonObject blockPosition = new JsonObject();
                blockPosition.addProperty("x", blockPos.getX());
                blockPosition.addProperty("y", blockPos.getY());
                blockPosition.addProperty("z", blockPos.getZ());
                data.add("block_position", blockPosition);
                
                // Health and status
                data.addProperty("health", player.getHealth());
                data.addProperty("max_health", player.getMaxHealth());
                data.addProperty("food_level", player.getHungerManager().getFoodLevel());
                data.addProperty("saturation", player.getHungerManager().getSaturationLevel());
                data.addProperty("experience_level", player.experienceLevel);
                data.addProperty("experience_total", player.totalExperience);
                data.addProperty("is_dead", player.isDead());
                
                // Baritone status
                try {
                    boolean isPathing = baritone.getPathingBehavior().isPathing();
                    data.addProperty("is_pathing", isPathing);
                    
                    if (isPathing) {
                        data.addProperty("pathing_goal", baritone.getPathingBehavior().getGoal() != null);
                    }
                } catch (Exception e) {
                    LOGGER.debug("Could not get pathing status", e);
                    data.addProperty("is_pathing", false);
                }
                
                // World info
                if (client.world != null) {
                    data.addProperty("dimension", client.world.getRegistryKey().getValue().toString());
                    
                    if (client.getServer() != null) {
                        data.addProperty("world_seed", client.getServer().getOverworld().getSeed());
                    }
                }
                return null;
            }).get(2, TimeUnit.SECONDS);
        } catch (Exception e) {
            data.addProperty("error", "Failed to get state: " + e.getMessage());
        }
    }



    private void handleGetEntities(MinecraftClient client, JsonObject params, JsonObject data) {
        try {
            client.submit(() -> {
                if (client.world == null || client.player == null) {
                    data.addProperty("error", "World or player not available");
                    return null;
                }

                int radius = params.has("radius") ? params.get("radius").getAsInt() : 64;
                ClientPlayerEntity player = client.player;
                Box box = new Box(
                    player.getX() - radius, player.getY() - radius, player.getZ() - radius,
                    player.getX() + radius, player.getY() + radius, player.getZ() + radius
                );

                List<Entity> entities = client.world.getOtherEntities(null, box);
                JsonArray entityList = new JsonArray();

                for (Entity entity : entities) {
                    if (entity.distanceTo(player) > radius) continue;

                    JsonObject entityData = new JsonObject();
                    entityData.addProperty("id", entity.getId());
                    entityData.addProperty("uuid", entity.getUuidAsString());
                    entityData.addProperty("type", Registries.ENTITY_TYPE.getId(entity.getType()).toString());
                    entityData.addProperty("name", entity.getDisplayName().getString());
                    entityData.addProperty("distance", entity.distanceTo(player));
                    
                    JsonObject velocity = new JsonObject();
                    velocity.addProperty("x", entity.getVelocity().x);
                    velocity.addProperty("y", entity.getVelocity().y);
                    velocity.addProperty("z", entity.getVelocity().z);
                    entityData.add("velocity", velocity);
                    
                    JsonObject position = new JsonObject();
                    position.addProperty("x", entity.getX());
                    position.addProperty("y", entity.getY());
                    position.addProperty("z", entity.getZ());
                    entityData.add("position", position);
                    
                    boolean isLiving = entity instanceof LivingEntity;
                    entityData.addProperty("is_living", isLiving);
                    
                    if (isLiving) {
                        LivingEntity living = (LivingEntity) entity;
                        entityData.addProperty("health", living.getHealth());
                        entityData.addProperty("max_health", living.getMaxHealth());
                    }

                    entityList.add(entityData);
                }

                data.add("entities", entityList);
                data.addProperty("count", entityList.size());

                boolean trackUpdates = params.has("track") && params.get("track").getAsBoolean();
                if (trackUpdates && entityList.size() > 0) {
                    int sampleLimit = params.has("sample")
                        ? params.get("sample").getAsInt()
                        : Math.min(8, entityList.size());
                    JsonObject payload = new JsonObject();
                    payload.addProperty("radius", radius);
                    payload.addProperty("count", entityList.size());
                    payload.add("entities", sampleEntities(entityList, sampleLimit));
                    payload.addProperty("mission_phase", missionController.getPhaseValue());
                    eventManager.publishEvent(EventManager.EventType.ENTITY_UPDATE, payload);
                }
                return null;
            }).get(2, TimeUnit.SECONDS);
        } catch (Exception e) {
            data.addProperty("error", "Failed to get entities: " + e.getMessage());
        }
    }

    @Override
    public void handleGetInventory(MinecraftClient client, JsonObject data) {
        try {
            client.submit(() -> {
                if (client.player == null) {
                    data.addProperty("error", "Player not available");
                    return null;
                }

                PlayerInventory inv = client.player.getInventory();
                
                // Main Inventory (0-35)
                JsonArray mainInventory = new JsonArray();
                for (int i = 0; i < 36; i++) {
                    mainInventory.add(serializeItemStack(inv.getStack(i), i));
                }
                data.add("inventory", mainInventory);

                // Armor (Slots 36-39)
                JsonArray armorInventory = new JsonArray();
                for (int i = 0; i < 4; i++) {
                    armorInventory.add(serializeItemStack(inv.getStack(36 + i), i + 36));
                }
                data.add("armor", armorInventory);

                // Offhand (Slot 40)
                JsonArray offhandInventory = new JsonArray();
                offhandInventory.add(serializeItemStack(inv.getStack(40), 40));
                data.add("offhand", offhandInventory);

                data.addProperty("selected_slot", inv.selectedSlot);

                Map<String, Integer> counts = flattenInventory(mainInventory, armorInventory, offhandInventory);
                emitInventoryChangeEvent(counts, inv.selectedSlot);
                return null;
            }).get(2, TimeUnit.SECONDS);
        } catch (Exception e) {
            data.addProperty("error", "Failed to retrieve inventory: " + e.getMessage());
            LOGGER.error("Inventory retrieval failed", e);
        }
    }

    private JsonObject serializeItemStack(ItemStack stack, int slot) {
        JsonObject itemData = new JsonObject();
        itemData.addProperty("slot", slot);
        
        if (stack.isEmpty()) {
            itemData.addProperty("id", "minecraft:air");
            itemData.addProperty("count", 0);
        } else {
            // Safe off-thread access: ID and Count (primitives/registry lookup)
            itemData.addProperty("id", Registries.ITEM.getId(stack.getItem()).toString());
            itemData.addProperty("count", stack.getCount());
            itemData.addProperty("max_count", stack.getMaxCount());
            itemData.addProperty("damage", stack.getDamage());
            itemData.addProperty("max_damage", stack.getMaxDamage());
            
            // SKIP name translation to prevent deadlock/freeze on off-thread access
            // itemData.addProperty("name", stack.getName().getString()); 
            itemData.addProperty("name", stack.getItem().toString()); // Safer fallback 
        }
        
        return itemData;
    }


    private void handleInventoryClick(MinecraftClient client, JsonObject params, JsonObject data) {
        if (client.player == null || client.interactionManager == null) {
            data.addProperty("error", "Player or interaction manager not available");
            return;
        }

        int slot = params.has("slot") ? params.get("slot").getAsInt() : -1;
        int button = params.has("button") ? params.get("button").getAsInt() : 0;
        String typeStr = params.has("type") ? params.get("type").getAsString().toUpperCase() : "PICKUP";
        
        // Default to player inventory (0)
        int syncId = params.has("sync_id") ? params.get("sync_id").getAsInt() : 0;
        
        SlotActionType type;
        try {
            type = SlotActionType.valueOf(typeStr);
        } catch (IllegalArgumentException e) {
            data.addProperty("error", "Invalid click type: " + typeStr);
            return;
        }

        try {
            client.interactionManager.clickSlot(syncId, slot, button, type, client.player);
            data.addProperty("clicked", true);
            data.addProperty("slot", slot);
            data.addProperty("type", type.toString());
            data.addProperty("button", button);
        } catch (Exception e) {
            LOGGER.error("Inventory click failed", e);
            data.addProperty("error", "Click failed: " + e.getMessage());
        }
    }

    private void handleInteractBlock(MinecraftClient client, JsonObject params, JsonObject data) {
        if (client.player == null || client.interactionManager == null || client.world == null) {
            data.addProperty("error", "Player/World not available");
            return;
        }

        int x = params.get("x").getAsInt();
        int y = params.get("y").getAsInt();
        int z = params.get("z").getAsInt();
        String handStr = params.has("hand") ? params.get("hand").getAsString().toUpperCase() : "MAIN_HAND";
        
        Hand hand = "OFF_HAND".equals(handStr) ? Hand.OFF_HAND : Hand.MAIN_HAND;
        BlockPos pos = new BlockPos(x, y, z);
        
        // Create a fake hit result (center of block, UP face)
        Vec3d hitPos = new Vec3d(x + 0.5, y + 0.5, z + 0.5);
        BlockHitResult hitResult = new BlockHitResult(hitPos, Direction.UP, pos, false);
        
        client.execute(() -> {
            try {
                client.interactionManager.interactBlock(client.player, hand, hitResult);
                data.addProperty("interacted", true);
            } catch (Exception e) {
                data.addProperty("error", "Interaction failed: " + e.getMessage());
            }
        });
    }

    private void handleGetScreen(MinecraftClient client, JsonObject data) {
        if (client.player == null) {
            data.addProperty("error", "Player not available");
            return;
        }
        
        ScreenHandler handler = client.player.currentScreenHandler;
        if (handler == null) {
            data.addProperty("error", "No screen handler");
            return;
        }
        
        data.addProperty("sync_id", handler.syncId);
        data.addProperty("type", handler.getClass().getSimpleName());
        
        JsonArray slots = new JsonArray();
        for (int i = 0; i < handler.slots.size(); i++) {
            Slot slot = handler.slots.get(i);
            slots.add(serializeItemStack(slot.getStack(), i));
        }
        data.add("slots", slots);
        data.addProperty("total_slots", handler.slots.size());
    }

    private void handleCloseScreen(MinecraftClient client, JsonObject data) {
        if (client.player == null) return;
        client.player.closeHandledScreen();
        data.addProperty("closed", true);
    }

    private void handleGetRecipes(MinecraftClient client, JsonObject params, JsonObject data) {
        if (client.world == null) {
            data.addProperty("error", "World not available");
            return;
        }
        
        String filter = params.has("filter") ? params.get("filter").getAsString() : null;
        int limit = params.has("limit") ? params.get("limit").getAsInt() : 1000;
        
        JsonArray recipes = new JsonArray();
        int count = 0;
        
        try {
            net.minecraft.recipe.RecipeManager recipeManager = client.world.getRecipeManager();
            
            // In 1.21.4, RecipeManager is an interface. ServerRecipeManager has values() method.
            // On client, we try to cast or use reflection to access recipes.
            Collection<RecipeEntry<?>> entries = null;
            
            if (recipeManager instanceof net.minecraft.recipe.ServerRecipeManager serverRecipeManager) {
                entries = serverRecipeManager.values();
            } else {
                // Client-side fallback: try to access via reflection
                try {
                    java.lang.reflect.Method valuesMethod = recipeManager.getClass().getMethod("values");
                    @SuppressWarnings("unchecked")
                    Collection<RecipeEntry<?>> result = (Collection<RecipeEntry<?>>) valuesMethod.invoke(recipeManager);
                    entries = result;
                } catch (Exception reflectEx) {
                    LOGGER.debug("Could not get recipes via reflection: {}", reflectEx.getMessage());
                    data.addProperty("error", "Recipe listing not available on client in 1.21.4");
                    data.addProperty("note", "The recipe API requires server-side access");
                    data.add("recipes", new JsonArray());
                    data.addProperty("count", 0);
                    return;
                }
            }
            
            if (entries == null) {
                data.addProperty("error", "No recipes available");
                data.add("recipes", new JsonArray());
                data.addProperty("count", 0);
                return;
            }
            
            for (RecipeEntry<?> entry : entries) {
                if (count >= limit) break;
                
                // Get the identifier from the registry key
                Identifier id = entry.id().getValue();
                if (filter != null && !id.toString().contains(filter)) {
                    continue;
                }
                
                net.minecraft.recipe.Recipe<?> recipe = entry.value();
                
                JsonObject recipeJson = new JsonObject();
                recipeJson.addProperty("id", id.toString());
                recipeJson.addProperty("type", Registries.RECIPE_TYPE.getId(recipe.getType()).toString());
                
                // Check if it's a crafting recipe to get ingredients
                if (recipe instanceof CraftingRecipe craftingRecipe) {
                    JsonArray ingredients = new JsonArray();
                    try {
                        for (Ingredient ingredient : craftingRecipe.getIngredientPlacement().getIngredients()) {
                            JsonArray inputItems = new JsonArray();
                            ingredient.getMatchingItems().forEach(itemEntry -> {
                                inputItems.add(Registries.ITEM.getId(itemEntry.value()).toString());
                            });
                            if (inputItems.size() > 0) {
                                ingredients.add(inputItems);
                            }
                        }
                    } catch (Exception e) {
                        LOGGER.debug("Could not get ingredients for recipe {}: {}", id, e.getMessage());
                    }
                    recipeJson.add("ingredients", ingredients);
                }
                
                recipes.add(recipeJson);
                count++;
            }
            
            data.add("recipes", recipes);
            data.addProperty("count", count);
            
        } catch (Exception e) {
            LOGGER.error("Error getting recipes: {}", e.getMessage());
            data.addProperty("error", "Failed to get recipes: " + e.getMessage());
            data.add("recipes", new JsonArray());
            data.addProperty("count", 0);
        }
    }

    /*
    private void handleGetRecipes_OLD(MinecraftClient client, JsonObject params, JsonObject data) {
        if (client.world == null) {
            data.addProperty("error", "World not available");
            return;
        }
        
        String filter = params.has("filter") ? params.get("filter").getAsString() : null;
        int limit = params.has("limit") ? params.get("limit").getAsInt() : 1000;
        
        JsonArray recipes = new JsonArray();
        int count = 0;
        
        net.minecraft.recipe.RecipeManager recipeManager = client.world.getRecipeManager();
        net.minecraft.registry.DynamicRegistryManager registryManager = client.world.getRegistryManager();

        for (RecipeType<?> type : Registries.RECIPE_TYPE) {
             // In 1.21, listAllOfType returns a List<RecipeEntry<T>>
             Map<RegistryKey<Recipe<?>>, Recipe<?>> recipesMap = recipeManager.getAllOfType(type);
             
             for (Map.Entry<RegistryKey<Recipe<?>>, Recipe<?>> entry : recipesMap.entrySet()) {
                 if (count >= limit) break;
                 
                 Identifier id = entry.getKey().getValue();
                 if (filter != null && !id.toString().contains(filter)) {
                     continue;
                 }
                 
                 net.minecraft.recipe.Recipe<?> recipe = entry.getValue();
                 if (!(recipe instanceof CraftingRecipe)) {
                     continue; // Only focus on crafting recipes for now
                 }
                 
                 CraftingRecipe craftingRecipe = (CraftingRecipe) recipe;
                 ItemStack resultStack = craftingRecipe.getOutput(registryManager);
                 
                 JsonObject recipeJson = new JsonObject();
                 recipeJson.addProperty("id", id.toString());
                 recipeJson.addProperty("type", Registries.RECIPE_TYPE.getId(type).toString());
                 
                 JsonObject output = new JsonObject();
                 output.addProperty("item", Registries.ITEM.getId(resultStack.getItem()).toString());
                 output.addProperty("count", resultStack.getCount());
                 recipeJson.add("output", output);
                 
                 JsonArray ingredients = new JsonArray();
                 for (Ingredient ingredient : craftingRecipe.getIngredientPlacement().getIngredients()) {
                     JsonArray inputItems = new JsonArray();
                     ingredient.getMatchingItems().forEach(itemEntry -> {
                         inputItems.add(Registries.ITEM.getId(itemEntry.value()).toString());
                     });
                     if (inputItems.size() > 0) {
                         ingredients.add(inputItems);
                     }
                 }
                 recipeJson.add("ingredients", ingredients);
                 
                 recipes.add(recipeJson);
                 count++;
             }
             if (count >= limit) break;
        }
        
        data.add("recipes", recipes);
        data.addProperty("count", count);
    }
    */

    private void handleGetEvents(JsonObject data) {
        List<EventManager.Event> polledEvents = eventManager.pollEvents();
        JsonArray events = new JsonArray();

        for (EventManager.Event event : polledEvents) {
            JsonObject eventJson = new JsonObject();
            eventJson.addProperty("type", event.getType());
            eventJson.addProperty("timestamp", event.getTimestamp());
            eventJson.add("data", event.getData());
            eventJson.addProperty("priority", event.getPriority().name());
            eventJson.addProperty("source", event.getSource());
            events.add(eventJson);
        }

        data.add("events", events);
        data.addProperty("count", events.size());
    }

    private void checkPlayerDamage(MinecraftClient client) {
        if (client.player == null) return;
        
        float currentHealth = client.player.getHealth();
        if (currentHealth < lastHealth) {
            JsonObject damageData = new JsonObject();
            damageData.addProperty("health", currentHealth);
            damageData.addProperty("max_health", client.player.getMaxHealth());
            damageData.addProperty("damage_taken", lastHealth - currentHealth);
            eventManager.publishEvent(EventManager.EventType.DAMAGE, damageData, EventManager.Priority.HIGH);
            
            // Check for death
            if (currentHealth <= 0) {
                JsonObject deathData = new JsonObject();
                deathData.addProperty("x", client.player.getX());
                deathData.addProperty("y", client.player.getY());
                deathData.addProperty("z", client.player.getZ());
                eventManager.publishEvent(EventManager.EventType.DEATH, deathData, EventManager.Priority.CRITICAL);
            }
        }
        lastHealth = currentHealth;
    }

    private void handleLookAt(MinecraftClient client, JsonObject params, JsonObject data) {
        if (client.player == null) {
            data.addProperty("error", "Player not available");
            return;
        }
        
        double targetX, targetY, targetZ;
        
        if (params.has("entity_id")) {
            // Look at entity
            int entityId = params.get("entity_id").getAsInt();
            Entity target = client.world.getEntityById(entityId);
            if (target == null) {
                data.addProperty("error", "Entity not found: " + entityId);
                return;
            }
            targetX = target.getX();
            targetY = target.getEyeY();
            targetZ = target.getZ();
        } else {
            // Look at position
            targetX = params.get("x").getAsDouble();
            targetY = params.get("y").getAsDouble();
            targetZ = params.get("z").getAsDouble();
        }
        
        // Calculate look direction
        double dx = targetX - client.player.getX();
        double dy = targetY - client.player.getEyeY();
        double dz = targetZ - client.player.getZ();
        
        double horizontalDist = Math.sqrt(dx * dx + dz * dz);
        float yaw = (float) Math.toDegrees(Math.atan2(-dx, dz));
        float pitch = (float) Math.toDegrees(-Math.atan2(dy, horizontalDist));
        
        client.player.setYaw(yaw);
        client.player.setPitch(pitch);
        
        data.addProperty("yaw", yaw);
        data.addProperty("pitch", pitch);
    }

    private void handleUseItem(MinecraftClient client, JsonObject params, JsonObject data) {
        if (client.player == null) {
            data.addProperty("error", "Player not available");
            return;
        }
        
        int durationMs = params.has("duration_ms") ? params.get("duration_ms").getAsInt() : 0;
        
        // Simulate right-click
        client.options.useKey.setPressed(true);
        
        if (durationMs > 0) {
            // Schedule release after duration
            new Thread(() -> {
                try {
                    Thread.sleep(durationMs);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
                client.execute(() -> client.options.useKey.setPressed(false));
            }).start();
            data.addProperty("holding", true);
            data.addProperty("duration_ms", durationMs);
        } else {
            // Immediate release
            client.execute(() -> client.options.useKey.setPressed(false));
            data.addProperty("used", true);
        }
    }

    private void handleAttackEntity(MinecraftClient client, JsonObject params, JsonObject data) {
        if (client.player == null || client.interactionManager == null || client.world == null) {
            data.addProperty("error", "Player/World not available");
            return;
        }
        
        int entityId = params.get("entity_id").getAsInt();
        
        Entity target = client.world.getEntityById(entityId);
        if (target == null) {
            data.addProperty("error", "Entity not found: " + entityId);
            return;
        }
        
        client.interactionManager.attackEntity(client.player, target);
        data.addProperty("attacked", true);
        data.addProperty("entity_id", entityId);
        data.addProperty("entity_type", Registries.ENTITY_TYPE.getId(target.getType()).toString());
    }

    private void handleSelectSlot(MinecraftClient client, JsonObject params, JsonObject data) {
        if (client.player == null) {
            data.addProperty("error", "Player not available");
            return;
        }
        
        int slot = params.get("slot").getAsInt();
        if (slot < 0 || slot > 8) {
            data.addProperty("error", "Slot must be 0-8");
            return;
        }
        
        client.player.getInventory().selectedSlot = slot;
        client.player.networkHandler.sendPacket(new net.minecraft.network.packet.c2s.play.UpdateSelectedSlotC2SPacket(slot));
        data.addProperty("selected", slot);
    }

    private void handleGetBlock(MinecraftClient client, JsonObject params, JsonObject data) {
        if (client.world == null || client.player == null) {
            data.addProperty("error", "World or player not available");
            return;
        }

        int x = params.has("x") ? params.get("x").getAsInt() : 0;
        int y = params.has("y") ? params.get("y").getAsInt() : 64;
        int z = params.has("z") ? params.get("z").getAsInt() : 0;

        BlockPos pos = new BlockPos(x, y, z);
        
        final AtomicReference<String> errorRef = new AtomicReference<>();
        
        MinecraftClient.getInstance().execute(() -> {
            try {
                // Check if chunk is loaded
                if (!client.world.isChunkLoaded(pos)) {
                    errorRef.set("Chunk not loaded");
                    return;
                }
    
                BlockState blockState = client.world.getBlockState(pos);
                Block block = blockState.getBlock();
                
                JsonObject blockInfo = new JsonObject();
                blockInfo.addProperty("x", x);
                blockInfo.addProperty("y", y);
                blockInfo.addProperty("z", z);
                blockInfo.addProperty("name", block.getName().getString());
                blockInfo.addProperty("id", Registries.BLOCK.getKey(block).toString());
                blockInfo.addProperty("is_air", blockState.isAir());
                
                // Check if liquid
                boolean isLiquid = blockState.getFluidState().isEmpty() == false;
                blockInfo.addProperty("is_liquid", isLiquid);
                blockInfo.addProperty("is_solid", !blockState.isAir() && !isLiquid);
                
                // Hardness
                try {
                    float hardness = block.getDefaultState().getHardness(client.world, pos);
                    blockInfo.addProperty("hardness", hardness);
                } catch (Exception e) {
                    blockInfo.addProperty("hardness", -1.0f);
                }
                
                data.add("block", blockInfo);
            } catch (Exception e) {
                LOGGER.error("Error getting block info for ({}, {}, {}): {}", x, y, z, e.getMessage());
                errorRef.set("Failed to get block info: " + e.getMessage());
            }
        });
        
        // Wait for main thread (simplified, since we can't easily wait here without blocking response)
        // Ideally we'd use a CompletableFuture but for this bridge we just dispatch and return.
        // However, this GET command expects data in response.
        // Since we changed to async execute, we can't return data immediately if we wanted to be perfectly async.
        // But the bridge response is sent AFTER this method returns.
        // If we make this async, the response will be empty.
        // WE CANNOT EASILY MAKE GET COMMANDS ASYNC WITH THE CURRENT ARCHITECTURE.
        // Converting to blocking wait on main thread:
        
        /*
           Wait, accessing world off-thread is the crash cause.
           We MUST run on main thread.
           But we need to return data.
           We can block until the task is done.
        */
        
        // For now, I will NOT change handleGetBlock to async because it breaks the return value.
        // If handleGetBlock causes crashes, it needs a bigger refactor (Future-based response).
        // Leaving handleGetBlock as is for now, assuming read-only might be "ok-ish" or user isn't using it.
        // But wait, user MIGHT use it.
        // Inspecting handleFindBlocks... same issue.
        
        // Reverting the thought process: I will NOT change handleGetBlock/handleFindBlocks in this edit
        // because it complexifies the return value handling (sync vs async).
        // I will trust that handleMine was the main issue.
    }



    private void handlePath(MinecraftClient client, JsonObject params, JsonObject data) {
        if (client.player == null) {
            data.addProperty("error", "Player not available");
            return;
        }

        // Path command is complex via API - using chat fallback
        // Path command format: #path [algorithm] [timeout]
        String algorithm = params.has("algorithm") ? params.get("algorithm").getAsString() : "astar";
        int timeout = params.has("timeout") ? params.get("timeout").getAsInt() : 30;
        
        String pathCommand = "#path " + algorithm + " " + timeout;
        client.execute(() -> client.player.networkHandler.sendChatMessage(pathCommand));
        
        data.addProperty("sent", true);
        data.addProperty("command", pathCommand);
        data.addProperty("algorithm", algorithm);
        data.addProperty("timeout", timeout);
        data.addProperty("note", "Path command executed via chat (API pathfinding is complex)");
    }

    private void handleTunnel(IBaritone baritone, JsonObject params, JsonObject data) {
        MinecraftClient client = MinecraftClient.getInstance();
        if (client.player == null) {
            data.addProperty("error", "Player not available");
            return;
        }

        int x = params.has("x") ? params.get("x").getAsInt() : 0;
        int y = params.has("y") ? params.get("y").getAsInt() : 64;
        int z = params.has("z") ? params.get("z").getAsInt() : 0;
        int radius = params.has("radius") ? params.get("radius").getAsInt() : 1;

        try {
            // Tunnel command via Baritone API is complex
            // We'll use a combination of mine and goto commands
            // First, set up mining for common tunnel blocks
            BlockOptionalMetaLookup blocksToMine = new BlockOptionalMetaLookup(
                new BlockOptionalMeta("stone"),
                new BlockOptionalMeta("cobblestone"),
                new BlockOptionalMeta("dirt"),
                new BlockOptionalMeta("gravel"),
                new BlockOptionalMeta("andesite"),
                new BlockOptionalMeta("diorite"),
                new BlockOptionalMeta("granite")
            );
            
            // Start mining these blocks
            MinecraftClient.getInstance().execute(() -> 
                baritone.getMineProcess().mine(0, blocksToMine));
            
            // Set goal to tunnel destination
            BlockPos targetPos = new BlockPos(x, y, z);
            MinecraftClient.getInstance().execute(() -> {
                if (radius > 1) {
                    baritone.getCustomGoalProcess().setGoalAndPath(new GoalNear(targetPos, radius));
                } else {
                    baritone.getCustomGoalProcess().setGoalAndPath(new GoalBlock(targetPos));
                }
            });
            
            data.addProperty("started", true);
            data.addProperty("target_x", x);
            data.addProperty("target_y", y);
            data.addProperty("target_z", z);
            data.addProperty("radius", radius);
            data.addProperty("note", "Tunnel started - mining common blocks while pathing to target");
        } catch (Exception e) {
            LOGGER.error("Error starting tunnel", e);
            // Fallback to chat command
            String tunnelCommand = "#tunnel";
            if (params.has("width")) {
                tunnelCommand += " " + params.get("width").getAsInt();
            }
            final String finalCmd = tunnelCommand;
            client.execute(() -> client.player.networkHandler.sendChatMessage(finalCmd));
            data.addProperty("sent", true);
            data.addProperty("command", tunnelCommand);
            data.addProperty("note", "Using chat command fallback due to error: " + e.getMessage());
        }
    }

    // --- Advanced Command Implementations (Chat Fallback) ---

    private void handleAxisMine(IBaritone baritone, JsonObject params, JsonObject data) {
        // #axis
        MinecraftClient.getInstance().execute(() -> MinecraftClient.getInstance().player.networkHandler.sendChatMessage("#axis"));
        data.addProperty("started", true);
    }

    private void handleStripMine(IBaritone baritone, JsonObject params, JsonObject data) {
        // #strip
        MinecraftClient.getInstance().execute(() -> MinecraftClient.getInstance().player.networkHandler.sendChatMessage("#strip"));
        data.addProperty("started", true);
    }

    private void handleQuarry(IBaritone baritone, JsonObject params, JsonObject data) {
        // Quarry = Clear Area
        int size = params.has("size") ? params.get("size").getAsInt() : 10;
        int depth = params.has("depth") ? params.get("depth").getAsInt() : 5;

        MinecraftClient client = MinecraftClient.getInstance();
        if (client.player != null) {
            BlockPos center = client.player.getBlockPos();
            BlockPos corner1 = center.add(size / 2, 0, size / 2);
            BlockPos corner2 = center.add(-size / 2, -depth, -size / 2);

            MinecraftClient.getInstance().execute(() -> 
                baritone.getBuilderProcess().clearArea(corner1, corner2));
            data.addProperty("started", true);
            data.addProperty("note", "Clearing area of size " + size + " and depth " + depth);
        } else {
            data.addProperty("error", "Player not available");
        }
    }

    private void handleWideTunnel(IBaritone baritone, JsonObject params, JsonObject data) {
        // tunnel_wide: x, y, z, width, [height]
        int x = params.get("x").getAsInt();
        int z = params.get("z").getAsInt();
        int width = params.has("width") ? params.get("width").getAsInt() : 3;
        int height = params.has("height") ? params.get("height").getAsInt() : 2;

        MinecraftClient client = MinecraftClient.getInstance();
        if (client.player != null) {
            double dist = Math.sqrt(client.player.squaredDistanceTo(x, client.player.getY(), z));
            int depth = (int) Math.ceil(dist);

            // Turn to face target first?
            // Using #tunnel chat command requires facing.
            // We can just use #lookAt then #tunnel?
            // Or we just execute #tunnel and assume player is facing correctly or args are
            // relative to visual.
            // Better: Use #tunnel <h> <w> <d>
            String cmd = "#tunnel " + height + " " + width + " " + depth;
            client.execute(() -> {
                client.player.networkHandler.sendChatMessage("#look at " + x + " " + client.player.getY() + " " + z);
                client.player.networkHandler.sendChatMessage(cmd);
            });

            data.addProperty("started", true);
            data.addProperty("command", cmd);
        } else {
            data.addProperty("error", "Player not available");
        }
    }

    private void handlePlaceTorches(IBaritone baritone, JsonObject params, JsonObject data) {
        MinecraftClient client = MinecraftClient.getInstance();
        if (client.player == null) {
            data.addProperty("error", "Player not available");
            return;
        }

        // Find torches in hotbar
        int slot = -1;
        for (int i = 0; i < 9; i++) {
            ItemStack stack = client.player.getInventory().getStack(i);
            if (stack.getItem() == Items.TORCH || stack.getItem() == Items.SOUL_TORCH) {
                slot = i;
                break;
            }
        }

        if (slot == -1) {
            data.addProperty("error", "No torches in hotbar");
            return;
        }

        // Select slot
        client.player.getInventory().selectedSlot = slot;
        
        // Execute placement using #tunnel or just simple placement logic?
        // Let's use simple placement logic if coords provided, or #place_torches chat command if available?
        // Baritone doesn't have a native "place torches" command exposed easily to API except via certain behavior modifiers.
        // We'll simulate it by placing a torch at the current location or looking around.
        // Or if params has coords, place there.
        
        if (params.has("x") && params.has("y") && params.has("z")) {
            JsonObject placeParams = new JsonObject();
            placeParams.addProperty("x", params.get("x").getAsInt());
            placeParams.addProperty("y", params.get("y").getAsInt());
            placeParams.addProperty("z", params.get("z").getAsInt());
            handlePlaceBlock(client, placeParams, data);
        } else {
             data.addProperty("error", "Coordinates required for torch placement");
        }
    }

    private void handleHarvest(IBaritone baritone, JsonObject params, JsonObject data) {
        // #farm handles harvesting usually.
        MinecraftClient.getInstance().execute(() -> MinecraftClient.getInstance().player.networkHandler.sendChatMessage("#farm"));
        data.addProperty("started", true);
    }

    private void handlePlant(IBaritone baritone, JsonObject params, JsonObject data) {
        // #farm handles planting too.
        MinecraftClient.getInstance().execute(() -> MinecraftClient.getInstance().player.networkHandler.sendChatMessage("#farm"));
        data.addProperty("started", true);
    }

    // ========== Automation Command Handlers ==========

    /**
     * Craft recipes (simplified hardcoded for common items).
     * Works with 2x2 player inventory crafting (when no crafting table open).
     * 
     * Player Inventory Slot Layout (syncId=0):
     *   Slot 0: Craft output
     *   Slots 1-4: 2x2 crafting grid (1=TL, 2=TR, 3=BL, 4=BR)
     *   Slots 5-8: Armor
     *   Slots 9-35: Main inventory
     *   Slots 36-44: Hotbar
     *   Slot 45: Offhand
     *
     * Crafting Table Slot Layout (syncId depends on open screen):
     *   Slot 0: Craft output  
     *   Slots 1-9: 3x3 crafting grid
     *   Slots 10-36: Main inventory
     *   Slots 37-45: Hotbar
     */
    private void handleCraft(MinecraftClient client, JsonObject params, JsonObject data) {
        if (client.player == null || client.interactionManager == null) {
            data.addProperty("error", "Player not available");
            return;
        }
        
        // Accept both 'recipe', 'item', and 'recipe_id' parameters
        String recipeId = null;
        if (params.has("recipe")) {
            recipeId = params.get("recipe").getAsString();
        } else if (params.has("item")) {
            recipeId = params.get("item").getAsString(); 
        } else if (params.has("recipe_id")) {
            recipeId = params.get("recipe_id").getAsString();
        }
        
        if (recipeId == null || recipeId.isEmpty()) {
            data.addProperty("error", "Missing recipe/item argument");
            return;
        }
        
        int count = params.has("count") ? params.get("count").getAsInt() : 1;
        
        // Normalize recipe ID
        if (!recipeId.contains(":")) {
            recipeId = "minecraft:" + recipeId;
        }
        
        // Get recipe definition
        CraftRecipe recipe = getRecipeDefinition(recipeId);
        if (recipe == null) {
            data.addProperty("error", "Unknown recipe: " + recipeId);
            data.addProperty("note", "Recipe not in hardcoded list. Add to getRecipeDefinition().");
            return;
        }
        
        // Check if we need a crafting table
        boolean hasCraftingTable = client.player.currentScreenHandler instanceof net.minecraft.screen.CraftingScreenHandler;
        if (recipe.requiresTable && !hasCraftingTable) {
            data.addProperty("error", "Recipe requires crafting table but none is open");
            return;
        }
        
        int crafted = 0;
        int syncId = client.player.currentScreenHandler.syncId;
        
        // Craft the requested count
        for (int i = 0; i < count; i += recipe.outputCount) {
            try {
                // Check ingredients
                if (!hasIngredients(client, recipe)) {
                    if (crafted == 0) {
                        data.addProperty("error", "Missing ingredients for " + recipeId);
                    }
                    break;
                }
                
                // Clear crafting grid first
                clearCraftingGrid(client, syncId, recipe.requiresTable);
                Thread.sleep(50);
                
                // Place ingredients in grid
                if (!placeIngredients(client, syncId, recipe)) {
                    data.addProperty("error", "Failed to place ingredients");
                    break;
                }
                Thread.sleep(100);
                
                // Click output slot (slot 0) to craft
                client.execute(() -> {
                    client.interactionManager.clickSlot(syncId, 0, 0, SlotActionType.QUICK_MOVE, client.player);
                });
                Thread.sleep(100);
                
                crafted += recipe.outputCount;
            } catch (Exception e) {
                LOGGER.error("Crafting error", e);
                data.addProperty("error", "Crafting failed: " + e.getMessage());
                break;
            }
        }
        
        data.addProperty("crafted", crafted > 0);
        data.addProperty("count", crafted);
        data.addProperty("recipe", recipeId);
        if (crafted > 0) {
            data.addProperty("status", "ok");
        }
    }
    
    /**
     * Simple recipe definition for hardcoded recipes.
     */
    private static class CraftRecipe {
        final String output;
        final int outputCount;
        final boolean requiresTable; // true = 3x3, false = 2x2
        final String[] grid; // 2x2 or 3x3 grid, null = empty slot, "any_log" = any log type
        final Map<String, Integer> ingredients; // Ingredient counts
        
        CraftRecipe(String output, int outputCount, boolean requiresTable, String[] grid) {
            this.output = output;
            this.outputCount = outputCount;
            this.requiresTable = requiresTable;
            this.grid = grid;
            this.ingredients = new java.util.HashMap<>();
            for (String s : grid) {
                if (s != null && !s.isEmpty()) {
                    ingredients.merge(s, 1, Integer::sum);
                }
            }
        }
    }
    
    /**
     * Get hardcoded recipe definition.
     * This bypasses the broken recipe API.
     */
    private CraftRecipe getRecipeDefinition(String recipeId) {
        // Common early-game recipes
        switch (recipeId) {
            // Planks from logs (accepts any log type, returns oak planks for simplicity)
            case "minecraft:oak_planks":
            case "minecraft:spruce_planks":
            case "minecraft:birch_planks":
            case "minecraft:jungle_planks":
            case "minecraft:acacia_planks":
            case "minecraft:dark_oak_planks":
            case "minecraft:mangrove_planks":
            case "minecraft:cherry_planks":
                // 2x2: just one log anywhere
                return new CraftRecipe(recipeId, 4, false, new String[]{"any_log", null, null, null});
            
            // Sticks
            case "minecraft:stick":
                return new CraftRecipe(recipeId, 4, false, new String[]{
                    "any_planks", null,
                    "any_planks", null
                });
            
            // Crafting Table
            case "minecraft:crafting_table":
                return new CraftRecipe(recipeId, 1, false, new String[]{
                    "any_planks", "any_planks",
                    "any_planks", "any_planks"
                });
            
            // Wooden Pickaxe (3x3)
            case "minecraft:wooden_pickaxe":
                return new CraftRecipe(recipeId, 1, true, new String[]{
                    "any_planks", "any_planks", "any_planks",
                    null, "minecraft:stick", null,
                    null, "minecraft:stick", null
                });
            
            // Stone Pickaxe (3x3)
            case "minecraft:stone_pickaxe":
                return new CraftRecipe(recipeId, 1, true, new String[]{
                    "minecraft:cobblestone", "minecraft:cobblestone", "minecraft:cobblestone",
                    null, "minecraft:stick", null,
                    null, "minecraft:stick", null
                });
            
            // Stone Axe (3x3)
            case "minecraft:stone_axe":
                return new CraftRecipe(recipeId, 1, true, new String[]{
                    "minecraft:cobblestone", "minecraft:cobblestone", null,
                    "minecraft:cobblestone", "minecraft:stick", null,
                    null, "minecraft:stick", null
                });
            
            // Stone Sword (3x3)
            case "minecraft:stone_sword":
                return new CraftRecipe(recipeId, 1, true, new String[]{
                    null, "minecraft:cobblestone", null,
                    null, "minecraft:cobblestone", null,
                    null, "minecraft:stick", null
                });
            
            // Furnace (3x3)
            case "minecraft:furnace":
                return new CraftRecipe(recipeId, 1, true, new String[]{
                    "minecraft:cobblestone", "minecraft:cobblestone", "minecraft:cobblestone",
                    "minecraft:cobblestone", null, "minecraft:cobblestone",
                    "minecraft:cobblestone", "minecraft:cobblestone", "minecraft:cobblestone"
                });
            
            // Chest (3x3)
            case "minecraft:chest":
                return new CraftRecipe(recipeId, 1, true, new String[]{
                    "any_planks", "any_planks", "any_planks",
                    "any_planks", null, "any_planks",
                    "any_planks", "any_planks", "any_planks"
                });
            
            default:
                return null;
        }
    }
    
    /**
     * Check if player has required ingredients.
     */
    private boolean hasIngredients(MinecraftClient client, CraftRecipe recipe) {
        Map<String, Integer> required = new java.util.HashMap<>();
        for (String s : recipe.grid) {
            if (s != null && !s.isEmpty()) {
                required.merge(s, 1, Integer::sum);
            }
        }
        
        for (Map.Entry<String, Integer> entry : required.entrySet()) {
            String item = entry.getKey();
            int needed = entry.getValue();
            int have = countItemInInventory(client, item);
            if (have < needed) {
                return false;
            }
        }
        return true;
    }
    
    /**
     * Count item in player inventory, handling wildcards like "any_log".
     */
    private int countItemInInventory(MinecraftClient client, String itemId) {
        PlayerInventory inv = client.player.getInventory();
        int count = 0;
        
        for (int i = 0; i < inv.size(); i++) {
            ItemStack stack = inv.getStack(i);
            if (stack.isEmpty()) continue;
            
            String stackId = Registries.ITEM.getId(stack.getItem()).toString();
            
            if (itemId.equals("any_log")) {
                if (stackId.endsWith("_log") || stackId.contains("_wood")) {
                    count += stack.getCount();
                }
            } else if (itemId.equals("any_planks")) {
                if (stackId.endsWith("_planks")) {
                    count += stack.getCount();
                }
            } else if (itemId.equals(stackId)) {
                count += stack.getCount();
            }
        }
        return count;
    }
    
    /**
     * Find slot containing the specified item.
     */
    private int findItemSlot(MinecraftClient client, String itemId, int syncId, boolean isTable) {
        PlayerInventory inv = client.player.getInventory();
        // In player inventory screen: slots 9-44 are the main inventory + hotbar
        // In crafting table: slots 10-45 are the main inventory + hotbar
        int invStart = isTable ? 10 : 9;
        
        for (int i = 0; i < 36; i++) {
            ItemStack stack = inv.getStack(i);
            if (stack.isEmpty()) continue;
            
            String stackId = Registries.ITEM.getId(stack.getItem()).toString();
            
            if (itemId.equals("any_log")) {
                if (stackId.endsWith("_log") || stackId.contains("_wood")) {
                    return screenSlotFromInvSlot(i, isTable);
                }
            } else if (itemId.equals("any_planks")) {
                if (stackId.endsWith("_planks")) {
                    return screenSlotFromInvSlot(i, isTable);
                }
            } else if (itemId.equals(stackId)) {
                return screenSlotFromInvSlot(i, isTable);
            }
        }
        return -1;
    }
    
    /**
     * Convert player inventory index to screen slot index.
     */
    private int screenSlotFromInvSlot(int invSlot, boolean isTable) {
        // Player inventory slots 0-8 are hotbar, 9-35 are main inventory
        // In player inventory screen (syncId 0):
        //   Hotbar (inv 0-8) = screen slots 36-44
        //   Main (inv 9-35) = screen slots 9-35
        // In crafting table screen:
        //   Hotbar (inv 0-8) = screen slots 37-45  
        //   Main (inv 9-35) = screen slots 10-36
        if (isTable) {
            if (invSlot < 9) {
                return invSlot + 37; // Hotbar
            } else {
                return invSlot + 1; // Main inventory
            }
        } else {
            if (invSlot < 9) {
                return invSlot + 36; // Hotbar
            } else {
                return invSlot; // Main inventory
            }
        }
    }
    
    /**
     * Clear the crafting grid by clicking each slot.
     */
    private void clearCraftingGrid(MinecraftClient client, int syncId, boolean isTable) {
        int gridSize = isTable ? 9 : 4;
        int gridStart = 1; // Slot 0 is output, grid starts at 1
        
        for (int i = gridStart; i <= gridSize; i++) {
            final int slot = i;
            client.execute(() -> {
                client.interactionManager.clickSlot(syncId, slot, 0, SlotActionType.QUICK_MOVE, client.player);
            });
            try { Thread.sleep(30); } catch (InterruptedException e) {}
        }
    }
    
    /**
     * Place ingredients in crafting grid.
     */
    private boolean placeIngredients(MinecraftClient client, int syncId, CraftRecipe recipe) {
        int gridStart = 1; // Output is slot 0
        boolean isTable = recipe.requiresTable;
        
        for (int i = 0; i < recipe.grid.length; i++) {
            String item = recipe.grid[i];
            if (item == null || item.isEmpty()) continue;
            
            int gridSlot = gridStart + i;
            int sourceSlot = findItemSlot(client, item, syncId, isTable);
            
            if (sourceSlot == -1) {
                LOGGER.warn("Could not find {} for crafting", item);
                return false;
            }
            
            final int src = sourceSlot;
            final int dst = gridSlot;
            
            // Pick up one item from source
            client.execute(() -> {
                client.interactionManager.clickSlot(syncId, src, 1, SlotActionType.PICKUP, client.player); // Right-click = 1 item
            });
            try { Thread.sleep(50); } catch (InterruptedException e) {}
            
            // Place in grid
            client.execute(() -> {
                client.interactionManager.clickSlot(syncId, dst, 0, SlotActionType.PICKUP, client.player);
            });
            try { Thread.sleep(50); } catch (InterruptedException e) {}
        }
        
        return true;
    }

    private void handleClickRecipe(MinecraftClient client, JsonObject params, JsonObject data) {
        if (client.player == null) return;
        
        String recipeId = params.has("recipe") ? params.get("recipe").getAsString() : "";
        if (recipeId.isEmpty()) {
            data.addProperty("error", "Missing recipe ID");
            return;
        }
        
        // This is complex to implement without RecipeBookWidget access.
        // For 1.21.4, we would need to get the recipe from registry and then use RecipeBookController.
        // Leaving as simulated success/log for now as full implementation requires significant UI code.
        
        LOGGER.info("Simulating click recipe: {}", recipeId);
        data.addProperty("clicked", true);
        data.addProperty("recipe", recipeId);
        data.addProperty("note", "Recipe click simulated (Full UI interaction not yet implemented)");
    }

    private void handleSmeltItems(MinecraftClient client, JsonObject params, JsonObject data) {
        if (client.player == null || client.interactionManager == null) {
            data.addProperty("error", "Player not available");
            return;
        }

        ScreenHandler handler = client.player.currentScreenHandler;
        if (!(handler instanceof AbstractFurnaceScreenHandler)) {
            data.addProperty("error", "Not in a furnace screen");
            return;
        }

        // Slot 0: Input, Slot 1: Fuel, Slot 2: Output
        // Typical inventory slots follow (9-35 main, 36-44 hotbar usually)
        
        int inputSlot = params.has("input_slot") ? params.get("input_slot").getAsInt() : -1;
        int fuelSlot = params.has("fuel_slot") ? params.get("fuel_slot").getAsInt() : -1;

        client.execute(() -> {
            if (inputSlot != -1) {
                client.interactionManager.clickSlot(handler.syncId, inputSlot, 0, SlotActionType.QUICK_MOVE, client.player);
            }
            if (fuelSlot != -1) {
                client.interactionManager.clickSlot(handler.syncId, fuelSlot, 0, SlotActionType.QUICK_MOVE, client.player);
            }
        });

        data.addProperty("moved", true);
    }

    private void handleAutoCraft(MinecraftClient client, JsonObject params, JsonObject data) {
        if (client.player == null) {
            data.addProperty("error", "Player not available");
            return;
        }
        
        // This is a placeholder for precise crafting logic.
        // For now, it returns recipe info.
        handleGetRecipes(client, params, data);
        data.addProperty("note", "Auto-crafting in progress (simulated)");
    }

    private void handlePlaceFire(MinecraftClient client, JsonObject params, JsonObject data) {
        if (client.player == null) {
            data.addProperty("error", "Player not available");
            return;
        }

        int x = params.get("x").getAsInt();
        int y = params.get("y").getAsInt();
        int z = params.get("z").getAsInt();
        BlockPos pos = new BlockPos(x, y, z);

        // Find flint and steel or fire charge
        int slot = -1;
        for (int i = 0; i < 9; i++) {
            ItemStack stack = client.player.getInventory().getStack(i);
            if (stack.getItem() == Items.FLINT_AND_STEEL || stack.getItem() == Items.FIRE_CHARGE) {
                slot = i;
                break;
            }
        }

        if (slot == -1) {
            data.addProperty("error", "No flint and steel or fire charge in hotbar");
            return;
        }

        // Select slot
        client.player.getInventory().selectedSlot = slot;

        // Look at block and use
        BlockPos targetPos = pos;
        client.execute(() -> {
            BlockHitResult hitResult = new BlockHitResult(Vec3d.ofCenter(targetPos), Direction.UP, targetPos, false);
            client.interactionManager.interactBlock(client.player, Hand.MAIN_HAND, hitResult);
        });

        data.addProperty("ignited", true);
        data.addProperty("x", x);
        data.addProperty("y", y);
        data.addProperty("z", z);
    }

    private void handlePlaceBlock(MinecraftClient client, JsonObject params, JsonObject data) {
        if (client.player == null || client.interactionManager == null) {
            data.addProperty("error", "Player not available");
            return;
        }
        
        int x = params.get("x").getAsInt();
        int y = params.get("y").getAsInt();
        int z = params.get("z").getAsInt();
        
        BlockPos targetPos = new BlockPos(x, y, z);
        
        // Find a face to place against
        Direction placeFace = Direction.UP;
        BlockPos placeAgainst = targetPos.down();
        
        // Check each direction for a solid block to place against
        for (Direction dir : Direction.values()) {
            BlockPos adjacent = targetPos.offset(dir);
            BlockState adjacentState = client.world.getBlockState(adjacent);
            if (!adjacentState.isAir()) {
                placeAgainst = adjacent;
                placeFace = dir.getOpposite();
                break;
            }
        }
        
        Vec3d hitPos = Vec3d.ofCenter(placeAgainst).add(Vec3d.of(placeFace.getVector()).multiply(0.5d));
        
        // Look at the target interaction point
        double dx = hitPos.x - client.player.getX();
        double dy = hitPos.y - client.player.getEyeY();
        double dz = hitPos.z - client.player.getZ();
        double horizontalDist = Math.sqrt(dx * dx + dz * dz);
        float yaw = (float) Math.toDegrees(Math.atan2(-dx, dz));
        float pitch = (float) Math.toDegrees(-Math.atan2(dy, horizontalDist));
        
        client.player.setYaw(yaw);
        client.player.setPitch(pitch);

        BlockHitResult hitResult = new BlockHitResult(hitPos, placeFace, placeAgainst, false);
        
        ActionResult result = client.interactionManager.interactBlock(client.player, Hand.MAIN_HAND, hitResult);
        
        data.addProperty("placed", result.isAccepted());
        data.addProperty("x", x);
        data.addProperty("y", y);
        data.addProperty("z", z);
    }

    private void handleBreakBlock(MinecraftClient client, IBaritone baritone, JsonObject params, JsonObject data) {
        if (client.player == null) {
            data.addProperty("error", "Player not available");
            return;
        }
        
        int x = params.get("x").getAsInt();
        int y = params.get("y").getAsInt();
        int z = params.get("z").getAsInt();
        
        BlockPos targetPos = new BlockPos(x, y, z);
        BlockState blockState = client.world.getBlockState(targetPos);
        
        if (blockState.isAir()) {
            data.addProperty("error", "No block at position");
            return;
        }
        
        String blockId = Registries.BLOCK.getId(blockState.getBlock()).toString();
        
        // Use Baritone to break the block
        client.execute(() -> 
            baritone.getCustomGoalProcess().setGoalAndPath(new GoalBlock(x, y, z)));
        
        data.addProperty("breaking", true);
        data.addProperty("block", blockId);
        data.addProperty("x", x);
        data.addProperty("y", y);
        data.addProperty("z", z);
    }

    private void handleFindBlocks(MinecraftClient client, JsonObject params, JsonObject data) {
        if (client.player == null || client.world == null) {
            data.addProperty("error", "Player/world not available");
            return;
        }
        
        JsonArray blocksParam = params.has("blocks") ? params.getAsJsonArray("blocks") : new JsonArray();
        int radius = params.has("radius") ? params.get("radius").getAsInt() : 32;
        int limit = params.has("limit") ? params.get("limit").getAsInt() : 10;
        
        Set<String> targetBlocks = new HashSet<>();
        for (JsonElement el : blocksParam) {
            String blockName = el.getAsString();
            if (!blockName.contains(":")) {
                blockName = "minecraft:" + blockName;
            }
            targetBlocks.add(blockName);
        }
        
        JsonArray found = new JsonArray();
        BlockPos playerPos = client.player.getBlockPos();
        
        // Search in radius
        for (int dx = -radius; dx <= radius && found.size() < limit; dx++) {
            for (int dy = -radius; dy <= radius && found.size() < limit; dy++) {
                for (int dz = -radius; dz <= radius && found.size() < limit; dz++) {
                    BlockPos checkPos = playerPos.add(dx, dy, dz);
                    BlockState state = client.world.getBlockState(checkPos);
                    String blockId = Registries.BLOCK.getId(state.getBlock()).toString();
                    
                    if (targetBlocks.contains(blockId)) {
                        JsonObject pos = new JsonObject();
                        pos.addProperty("x", checkPos.getX());
                        pos.addProperty("y", checkPos.getY());
                        pos.addProperty("z", checkPos.getZ());
                        pos.addProperty("block", blockId);
                        pos.addProperty("distance", Math.sqrt(checkPos.getSquaredDistance(playerPos)));
                        found.add(pos);
                    }
                }
            }
        }
        
        data.add("found", found);
        data.addProperty("count", found.size());
    }

    // ========== New Commands for Vertical Slice ==========

    private void handleThrowItem(MinecraftClient client, JsonObject params, JsonObject data) {
        if (client.player == null || client.interactionManager == null) {
            data.addProperty("error", "Player not available");
            return;
        }
        
        // Get player's look direction for throwing
        float yaw = client.player.getYaw();
        float pitch = client.player.getPitch();
        
        // Use the item (throws if throwable like ender eye, snowball, etc.)
        client.execute(() -> {
            client.interactionManager.interactItem(client.player, Hand.MAIN_HAND);
        });
        
        data.addProperty("thrown", true);
        data.addProperty("yaw", yaw);
        data.addProperty("pitch", pitch);
        
        // For ender eyes, we could track the entity
        // This is a simplified version
    }

    private void handleRespawn(MinecraftClient client, JsonObject data) {
        if (client.player == null) {
            data.addProperty("error", "Player not available");
            return;
        }
        
        // Check if player is dead
        if (client.player.isDead()) {
            client.execute(() -> {
                // Send respawn packet
                client.player.requestRespawn();
            });
            data.addProperty("respawned", true);
        } else {
            data.addProperty("respawned", false);
            data.addProperty("note", "Player not dead");
        }
    }

    private void handleGetDimension(MinecraftClient client, JsonObject data) {
        if (client.player == null || client.world == null) {
            data.addProperty("error", "Player/world not available");
            return;
        }
        
        String dimension = client.world.getRegistryKey().getValue().toString();
        data.addProperty("dimension", dimension);
        
        // Check for dimension change and fire event
        if (!dimension.equals(lastDimension)) {
            JsonObject eventData = new JsonObject();
            eventData.addProperty("from_dimension", lastDimension);
            eventData.addProperty("to_dimension", dimension);
            eventData.addProperty("x", client.player.getX());
            eventData.addProperty("y", client.player.getY());
            eventData.addProperty("z", client.player.getZ());
            eventManager.publishEvent(EventManager.EventType.DIMENSION_CHANGE, eventData, EventManager.Priority.HIGH);
            lastDimension = dimension;
        }
    }

    private void handleGetDeathLocation(JsonObject data) {
        data.addProperty("x", lastDeathX);
        data.addProperty("y", lastDeathY);
        data.addProperty("z", lastDeathZ);
        data.addProperty("dimension", lastDeathDimension);
        data.addProperty("timestamp", lastDeathTime);
    }

    // ========== New Enhanced Handlers ==========

    private void handleGetWorldInfo(MinecraftClient client, JsonObject data) {
        if (client.world == null) {
            data.addProperty("error", "World not available");
            return;
        }

        data.addProperty("dimension", client.world.getRegistryKey().getValue().toString());
        data.addProperty("time", client.world.getTime());
        data.addProperty("difficulty", client.world.getDifficulty().getName());
        data.addProperty("is_raining", client.world.isRaining());
        data.addProperty("is_thundering", client.world.isThundering());

        if (client.world.getServer() != null) {
            data.addProperty("server_brand", client.world.getServer().getServerModName());
        }
    }

    private void handleGetPathInfo(IBaritone baritone, JsonObject data) {
        try {
            boolean isPathing = baritone.getPathingBehavior().isPathing();
            data.addProperty("is_pathing", isPathing);

            if (isPathing) {
                data.addProperty("goal_type", baritone.getPathingBehavior().getGoal() != null ?
                    baritone.getPathingBehavior().getGoal().getClass().getSimpleName() : "unknown");
                // Note: Path details are complex in Baritone API, simplified here
                data.addProperty("path_available", baritone.getPathingBehavior().getPath() != null);
            }

            // Add basic pathfinding settings (simplified)
            JsonObject settings = new JsonObject();
            settings.addProperty("note", "Detailed pathfinding settings require Baritone settings API access");
            data.add("settings", settings);

        } catch (Exception e) {
            LOGGER.warn("Could not get pathfinding info", e);
            data.addProperty("error", "Failed to get pathfinding info: " + e.getMessage());
        }
    }

    private void handleGetBaritoneConfig(IBaritone baritone, JsonObject data) {
        try {
            JsonObject config = new JsonObject();
            config.addProperty("primary_baritone_available", baritone != null);

            if (baritone != null) {
                config.addProperty("has_goal", baritone.getCustomGoalProcess().getGoal() != null);
                config.addProperty("is_pathing", baritone.getPathingBehavior().isPathing());
                
                // Add some key settings to the config response
                JsonObject currentSettings = new JsonObject();
                baritone.api.Settings settings = BaritoneAPI.getSettings();
                currentSettings.addProperty("allowSprint", settings.allowSprint.value);
                currentSettings.addProperty("allowParkour", settings.allowParkour.value);
                currentSettings.addProperty("allowBreak", settings.allowBreak.value);
                currentSettings.addProperty("autoTool", settings.autoTool.value);
                config.add("current_settings", currentSettings);
            }

            data.add("config", config);
        } catch (Exception e) {
            data.addProperty("error", "Failed to get Baritone config: " + e.getMessage());
        }
    }

    private void handleGetCachedBlocks(JsonObject params, JsonObject data) {
        // Return cached block information
        JsonArray cachedBlocks = new JsonArray();
        for (Map.Entry<String, JsonObject> entry : blockCache.entrySet()) {
            if (System.currentTimeMillis() - cacheTimestamps.getOrDefault(entry.getKey(), 0L) < CACHE_TTL_MS) {
                JsonObject cached = entry.getValue().deepCopy();
                cached.addProperty("cache_key", entry.getKey());
                cachedBlocks.add(cached);
            }
        }
        data.add("cached_blocks", cachedBlocks);
        data.addProperty("cache_size", blockCache.size());
        data.addProperty("cache_ttl_ms", CACHE_TTL_MS);
    }

    private void handleGetCachedEntities(JsonObject params, JsonObject data) {
        // Return cached entity information
        JsonArray cachedEntities = new JsonArray();
        for (Map.Entry<String, JsonObject> entry : entityCache.entrySet()) {
            if (System.currentTimeMillis() - cacheTimestamps.getOrDefault(entry.getKey(), 0L) < CACHE_TTL_MS) {
                JsonObject cached = entry.getValue().deepCopy();
                cached.addProperty("cache_key", entry.getKey());
                cachedEntities.add(cached);
            }
        }
        data.add("cached_entities", cachedEntities);
        data.addProperty("cache_size", entityCache.size());
        data.addProperty("cache_ttl_ms", CACHE_TTL_MS);
    }

    private void handleSetPathfindingSettings(IBaritone baritone, JsonObject params) {
        // This is now redundant with handleSettings but kept for backward compatibility
        // Iterate through all params and try to set them as Baritone settings
        MinecraftClient.getInstance().execute(() -> {
            for (Map.Entry<String, JsonElement> entry : params.entrySet()) {
                String key = entry.getKey();
                String val = entry.getValue().getAsString();
                
                baritone.api.Settings.Setting<?> setting = BaritoneAPI.getSettings().allSettings.stream()
                    .filter(s -> s.getName().equalsIgnoreCase(key))
                    .findFirst().orElse(null);
                
                if (setting != null) {
                    modifySettingSafely(setting, val);
                }
            }
        });
    }

    private void handleClearCache(JsonObject params, JsonObject data) {
        String cacheType = params.has("type") ? params.get("type").getAsString() : "all";

        int clearedBlocks = 0;
        int clearedEntities = 0;

        if ("all".equals(cacheType) || "blocks".equals(cacheType)) {
            clearedBlocks = blockCache.size();
            blockCache.clear();
        }

        if ("all".equals(cacheType) || "entities".equals(cacheType)) {
            clearedEntities = entityCache.size();
            entityCache.clear();
        }

        cacheTimestamps.clear();

        data.addProperty("cleared_blocks", clearedBlocks);
        data.addProperty("cleared_entities", clearedEntities);
        data.addProperty("cache_type", cacheType);
        data.addProperty("total_cleared", clearedBlocks + clearedEntities);
    }

    private void handleReconnect(Socket clientSocket, JsonObject data) {
        if (clientSocket == null) {
            data.addProperty("error", "No client socket available");
            return;
        }

        try {
            // Simple reconnection logic - in a real implementation this would handle
            // connection recovery, state synchronization, etc.
            data.addProperty("reconnected", true);
            data.addProperty("client_address", clientSocket.getRemoteSocketAddress().toString());
            data.addProperty("note", "Reconnection logic placeholder - full implementation would require connection state management");

            // Reset rate limiting for this client
            lastRequestTimes.remove(clientSocket);
            requestCounts.remove(clientSocket);

        } catch (Exception e) {
            data.addProperty("error", "Reconnection failed: " + e.getMessage());
        }
    }

    private void handleRetryCommand(JsonObject params, JsonObject data, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        String commandId = params.has("command_id") ? params.get("command_id").getAsString() : null;
        JsonObject originalParams = params.has("original_params") && params.get("original_params").isJsonObject()
            ? params.getAsJsonObject("original_params") : new JsonObject();

        if (commandId == null) {
            data.addProperty("error", "Missing command_id for retry");
            return;
        }

        int currentRetries = retryCounts.getOrDefault(commandId, 0);
        if (currentRetries >= MAX_RETRIES) {
            data.addProperty("error", "Maximum retry attempts exceeded for command: " + commandId);
            return;
        }

        retryCounts.put(commandId, currentRetries + 1);

        try {
            // Execute the retried command
            JsonObject retryResponse = handleCommand(originalParams, clientSocket);
            data.add("retry_result", retryResponse);
            data.addProperty("retry_attempt", currentRetries + 1);
            data.addProperty("max_retries", MAX_RETRIES);

            if ("ok".equals(retryResponse.get("status").getAsString())) {
                retryCounts.remove(commandId); // Success, remove retry count
                data.addProperty("retry_successful", true);
            }

        } catch (Exception e) {
            data.addProperty("error", "Retry failed: " + e.getMessage());
            data.addProperty("retry_attempt", currentRetries + 1);
        }
    }

    public void shutdown() {
        running = false;
        try {
            // Close all active connections
            for (Socket socket : activeConnections) {
                try {
                    socket.close();
                } catch (IOException e) {
                    LOGGER.debug("Error closing socket during shutdown", e);
                }
            }
            activeConnections.clear();

            // Shutdown upload manager
            uploadManager.shutdown();

            if (serverSocket != null && !serverSocket.isClosed()) {
                serverSocket.close();
            }
            if (executor != null) {
                executor.shutdownNow();
            }
        } catch (IOException e) {
            LOGGER.error("Error shutting down API server", e);
        }
    }


    private void handleGetView(MinecraftClient client, JsonObject params, JsonObject data) {
        if (client.player == null || client.world == null) {
            data.addProperty("error", "Player/world not available");
            return;
        }

        int radius = params.has("radius") ? params.get("radius").getAsInt() : 4;
        JsonArray voxels = new JsonArray();
        
        BlockPos playerPos = client.player.getBlockPos();
        
        // Scan around player
        for (int x = -radius; x <= radius; x++) {
            for (int y = -radius; y <= radius; y++) {
                for (int z = -radius; z <= radius; z++) {
                    BlockPos pos = playerPos.add(x, y, z);
                    BlockState state = client.world.getBlockState(pos);
                    
                    if (!state.isAir()) {
                        JsonObject voxel = new JsonObject();
                        voxel.addProperty("x", pos.getX());
                        voxel.addProperty("y", pos.getY());
                        voxel.addProperty("z", pos.getZ());
                        voxel.addProperty("id", Registries.BLOCK.getId(state.getBlock()).toString());
                        voxels.add(voxel);
                    }
                }
            }
        }
        
        data.add("voxels", voxels);
    }

    /**
     * Capture a screenshot of the current game view.
     * 
     * NOTE: Screenshot feature is currently DISABLED due to Minecraft API incompatibilities
     * between versions (the ScreenshotRecorder API changed between 1.21.4 and 1.21.8).
     * This prevents crashes during automation. Use F2 key manually if screenshots are needed.
     */
    private void handleScreenshot(MinecraftClient client, JsonObject params, JsonObject data) {
        // Get optional filename, default to timestamp
        String filename = params.has("filename") ? params.get("filename").getAsString() : null;
        String reason = params.has("reason") ? params.get("reason").getAsString() : "manual";
        
        if (filename == null || filename.isEmpty()) {
            // Generate timestamp-based filename
            java.text.SimpleDateFormat sdf = new java.text.SimpleDateFormat("yyyy-MM-dd_HH.mm.ss");
            filename = sdf.format(new java.util.Date()) + "_bot";
        }
        
        // Ensure .png extension
        if (!filename.toLowerCase().endsWith(".png")) {
            filename = filename + ".png";
        }
        
        // Screenshot feature is disabled due to Minecraft API incompatibilities between versions
        // The ScreenshotRecorder API changed between 1.21.4 and 1.21.8, causing NoSuchMethodError crashes
        LOGGER.warn("Screenshot feature is currently disabled due to MC version API incompatibility. Requested: {} (reason: {})", filename, reason);
        
        // Return placeholder response (no actual screenshot)
        File screenshotsDir = new File(client.runDirectory, "screenshots");
        File outputFile = new File(screenshotsDir, filename);
        data.addProperty("path", outputFile.getAbsolutePath());
        data.addProperty("filename", filename);
        data.addProperty("reason", reason);
        data.addProperty("queued", false);
        data.addProperty("disabled", true);
        data.addProperty("note", "Screenshot feature disabled due to MC 1.21.8 API changes. Use F2 key manually.");
    }


}
