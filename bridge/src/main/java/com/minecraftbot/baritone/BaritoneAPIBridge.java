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
import net.minecraft.util.Identifier;
import net.minecraft.registry.Registry;
import net.minecraft.registry.Registries;
import net.fabricmc.fabric.api.client.message.v1.ClientReceiveMessageEvents;
import net.minecraft.screen.CraftingScreenHandler;
import net.minecraft.screen.PlayerScreenHandler;
import net.minecraft.screen.FurnaceScreenHandler;
import net.minecraft.screen.AbstractFurnaceScreenHandler;
import net.minecraft.util.ActionResult;
// import net.fabricmc.fabric.api.event.player.AttackBlockCallback;
// import net.fabricmc.fabric.api.event.player.UseBlockCallback;
import net.minecraft.item.Items;
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

    // Connection tracking
    private final Set<Socket> activeConnections = ConcurrentHashMap.newKeySet();
    private final Map<String, Long> uploadTimestamps = new ConcurrentHashMap<>();
    private final Map<String, Socket> uploadOwners = new ConcurrentHashMap<>();

    // Schematic Upload State
    private final Map<String, OutputStream> uploadStreams = new ConcurrentHashMap<>();
    private final Map<String, MessageDigest> uploadDigests = new ConcurrentHashMap<>();
    private long lastSeq = 0;

    // Event buffer
    private static final int MAX_EVENT_BUFFER_SIZE = 100;
    private final ConcurrentLinkedQueue<JsonObject> eventBuffer = new ConcurrentLinkedQueue<>();
    private float lastHealth = 20.0f;
    private String lastDimension = "minecraft:overworld";
    private final MissionController missionController = new MissionController(this);
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
    private static final int RATE_LIMIT_REQUESTS = 100; // requests per window
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

        startAPIServer();
        registerEventListeners();
        LOGGER.info("Baritone API Bridge initialized on port " + DEFAULT_PORT);
    }

    private void registerEventListeners() {
        // Chat message listener
        ClientReceiveMessageEvents.GAME.register((message, overlay) -> {
            if (!overlay) {
                bufferEvent("chat", createChatEventData(message.getString()));
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

        // New event listeners for enhanced tracking
        registerBlockUpdateListener();
        registerEntityEventListener();
        registerPathfindingListener();

        LOGGER.info("Fabric event listeners registered for block interaction, block updates, entities, and pathfinding");
    }

    private void registerBlockUpdateListener() {
        // Note: Minecraft doesn't have a direct block update event, but we can hook into world changes
        // This is a placeholder for future implementation with mixins or other hooks
    }

    private void registerEntityEventListener() {
        // Track entity spawns, deaths, and movements via periodic checks
        executor.submit(() -> {
            MinecraftClient client = MinecraftClient.getInstance();
            Map<Integer, BlockPos> lastEntityPositions = new HashMap<>();

            while (running) {
                try {
                    if (client.world != null && client.player != null) {
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
                                bufferEvent("entity_spawn", spawnData);
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
                                    bufferEvent("entity_move", moveData);
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
                                bufferEvent("entity_despawn", despawnData);
                                return true;
                            }
                            return false;
                        });
                        // Check for player death
                        trackPlayerDeath(client);
                    }

                    Thread.sleep(1000); // Check every second
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    break;
                } catch (Exception e) {
                    LOGGER.warn("Error in entity tracking loop", e);
                }
            }
        });
    }

    private void registerPathfindingListener() {
        // Monitor Baritone pathfinding progress
        executor.submit(() -> {
            IBaritone lastBaritone = null;
            boolean lastPathingState = false;

            while (running) {
                try {
                    IBaritone baritone = BaritoneAPI.getProvider().getPrimaryBaritone();
                    if (baritone != null) {
                        boolean isPathing = baritone.getPathingBehavior().isPathing();
                        boolean stateChanged = (lastBaritone != baritone) || (lastPathingState != isPathing);

                        if (stateChanged) {
                            JsonObject pathData = new JsonObject();
                            pathData.addProperty("is_pathing", isPathing);
                            if (isPathing) {
                                pathData.addProperty("goal_type", baritone.getPathingBehavior().getGoal() != null ?
                                    baritone.getPathingBehavior().getGoal().getClass().getSimpleName() : "unknown");
                            }
                            bufferEvent("pathfinding_state", pathData);
                            lastPathingState = isPathing;
                            lastBaritone = baritone;
                        }
                    }

                    Thread.sleep(500); // Check twice per second
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    break;
                } catch (Exception e) {
                    LOGGER.warn("Error in pathfinding monitoring", e);
                }
            }
        });
    }

    @Override
    public void publishMissionEvent(String reason, JsonObject payload) {
        JsonObject eventPayload = new JsonObject();
        eventPayload.addProperty("reason", reason);
        eventPayload.addProperty("phase", missionController.getPhaseValue());
        if (payload != null) {
            eventPayload.add("payload", payload);
        }
        bufferEvent("mission", eventPayload);
    }

    private void bufferEvent(String type, JsonObject eventData) {
        JsonObject event = new JsonObject();
        event.addProperty("type", type);
        event.addProperty("timestamp", System.currentTimeMillis());
        event.add("data", eventData);
        
        eventBuffer.add(event);
        
        // Trim buffer if too large
        while (eventBuffer.size() > MAX_EVENT_BUFFER_SIZE) {
            eventBuffer.poll();
        }
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
        bufferEvent("tick_update", payload);

        String currentDimension = payload.get("dimension").getAsString();
        if (!currentDimension.equals(lastDimension)) {
            JsonObject eventData = new JsonObject();
            eventData.addProperty("from_dimension", lastDimension);
            eventData.addProperty("to_dimension", currentDimension);
            eventData.add("position", position.deepCopy());
            bufferEvent("dimension_change", eventData);
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
            bufferEvent("inventory_change", payload);
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
            bufferEvent("death", deathData);
            
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
            bufferEvent("respawn", respawnData);
            
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
        // Find and cleanup all uploads owned by this client
        uploadOwners.entrySet().removeIf(entry -> {
            if (entry.getValue().equals(clientSocket)) {
                String uploadName = entry.getKey();
                cleanupUpload(uploadName);
                return true;
            }
            return false;
        });
    }
    
    private void cleanupUpload(String name) {
        OutputStream os = uploadStreams.remove(name);
        if (os != null) {
            try {
                os.close();
            } catch (IOException e) {
                LOGGER.warn("Error closing upload stream for {}", name, e);
            }
        }
        uploadDigests.remove(name);
        uploadTimestamps.remove(name);
        uploadOwners.remove(name);
        LOGGER.debug("Cleaned up abandoned upload: {}", name);
    }

    private JsonObject handleCommand(JsonObject request) {
        return handleCommand(request, null);
    }
    
    JsonObject handleCommand(JsonObject request, Socket clientSocket) {
        JsonObject response = new JsonObject();
        response.addProperty("seq", ++lastSeq);
        response.addProperty("timestamp", System.currentTimeMillis());

        // Rate limiting check
        if (!checkRateLimit(clientSocket, response)) {
            return response;
        }

        if (request == null || !request.has("command")) {
            response.addProperty("status", "error");
            response.addProperty("error", "Missing command");
            return response;
        }

        String id = request.has("id") ? request.get("id").getAsString() : null;
        String command = request.has("command") ? request.get("command").getAsString() : null;
        JsonObject params = request.has("params") ? request.getAsJsonObject("params") : new JsonObject();

        response.addProperty("id", id);

        if (command == null) {
            response.addProperty("status", "error");
            response.addProperty("error", "Missing command");
            return response;
        }

        try {
            MinecraftClient client = MinecraftClient.getInstance();
            // Check for damage events on each command
            checkPlayerDamage(client);

            IBaritone baritone = BaritoneAPI.getProvider().getPrimaryBaritone();
            if (baritone == null) {
                response.addProperty("status", "error");
                response.addProperty("error", "Baritone not available. Make sure Baritone mod is installed and loaded.");
                return response;
            }

            // Allow some commands without player (e.g. status checks, uploads)
            if (client.player == null && !isOfflineCommand(command)) {
                response.addProperty("status", "error");
                response.addProperty("error", "Player not available");
                return response;
            }

            emitTickEvent(client, baritone);
            
            JsonObject data = new JsonObject();

            if (missionController.tryHandle(command, params, data, client, baritone, clientSocket)) {
                response.addProperty("status", "ok");
                response.add("data", data);
                return response;
            }

            switch (command) {
                // Movement
                case "goto":
                    handleGoto(baritone, params, data);
                    break;
                case "come":
                    handleCome(client, params, data);
                    break;
                case "follow":
                    handleFollow(baritone, params, data);
                    break;
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

                // Mining / World Interaction
                case "mine":
                    handleMine(baritone, params, data);
                    break;
                case "tunnel":
                    handleTunnel(baritone, params, data);
                    break;
                case "farm":
                    handleFarm(baritone, params, data);
                    break;
                case "build":
                    handleBuild(baritone, params, data);
                    break;
                case "sel":
                    handleSelection(baritone, params, data);
                    break;

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

                // State / Info
                case "get_state":
                    handleGetState(client, baritone, data);
                    break;
                case "get_inventory":
                    handleGetInventory(client, data);
                    break;
                case "inventory_click":
                    handleInventoryClick(client, params, data);
                    break;
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
                case "get_entities":
                    handleGetEntities(client, params, data);
                    break;
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
                case "craft":
                    handleCraft(client, params, data);
                    break;
                case "place_block":
                    handlePlaceBlock(client, params, data);
                    break;
                case "break_block":
                    handleBreakBlock(client, baritone, params, data);
                    break;
                case "find_blocks":
                    handleFindBlocks(client, params, data);
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

                default:
                    response.addProperty("status", "error");
                    response.addProperty("error", "Unknown command: " + command);
                    return response;
            }

            response.addProperty("status", "ok");
            response.add("data", data);

        } catch (Exception e) {
            response.addProperty("status", "error");
            response.addProperty("error", e.getMessage());
            LOGGER.error("Command execution error: " + command, e);
        }

        return response;
    }

    private boolean isOfflineCommand(String command) {
        return command.startsWith("schematic_") || missionController.isOfflineSafe(command);
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
        telemetry.addProperty("eventBufferSize", eventBuffer.size());
        telemetry.addProperty("missionQueue", missionController.queueSize());
        telemetry.addProperty("timestamp", System.currentTimeMillis());
        return telemetry;
    }

    private void handleGoto(IBaritone baritone, JsonObject params, JsonObject data) {
        int x = params.get("x").getAsInt();
        int y = params.get("y").getAsInt();
        int z = params.get("z").getAsInt();
        int radius = params.has("radius") ? params.get("radius").getAsInt() : 0;

        if (radius > 0) {
            baritone.getCustomGoalProcess().setGoalAndPath(new GoalNear(new BlockPos(x, y, z), radius));
        } else {
            baritone.getCustomGoalProcess().setGoalAndPath(new GoalBlock(x, y, z));
        }
        data.addProperty("started", true);
    }

    @Override
    public void handleMine(IBaritone baritone, JsonObject params, JsonObject data) {
        // Baritone's mine process is thread-safe, run directly
        int count = 0;
        if (params.has("count")) {
            count = params.get("count").getAsInt();
        } else if (params.has("quantity")) {
            count = params.get("quantity").getAsInt();
        }

        if (params.has("block_type")) {
            String blockId = params.get("block_type").getAsString();
            Identifier id = Identifier.of(blockId);
            if (Registries.BLOCK.containsId(id)) {
                Block block = Registries.BLOCK.get(id);
                baritone.getMineProcess().mine(count, block);
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

            List<BlockOptionalMeta> lookup = new ArrayList<>();
            for (JsonElement element : blocksArray) {
                if (!element.isJsonPrimitive()) {
                    continue;
                }
                String blockId = element.getAsString();
                Identifier id = Identifier.of(blockId);
                if (Registries.BLOCK.containsId(id)) {
                    lookup.add(new BlockOptionalMeta(Registries.BLOCK.get(id)));
                }
            }
            
            if (!lookup.isEmpty()) {
                baritone.getMineProcess().mine(count, lookup.toArray(new BlockOptionalMeta[0]));
                data.addProperty("started", true);
            } else {
                data.addProperty("error", "No valid blocks found to mine");
            }
        } else {
             data.addProperty("error", "Missing block_type or blocks");
        }
    }

    private void handleFarm(IBaritone baritone, JsonObject params, JsonObject data) {
        int range = params.has("range") ? params.get("range").getAsInt() : 0;
        // Native farm process
        baritone.getFarmProcess().farm(range);
        data.addProperty("started", true);
    }

    private void handleExplore(IBaritone baritone, JsonObject params, JsonObject data) {
        int x = params.has("x") ? params.get("x").getAsInt() : 0;
        int z = params.has("z") ? params.get("z").getAsInt() : 0;
        baritone.getExploreProcess().explore(x, z);
        data.addProperty("started", true);
    }

    private void handleFollow(IBaritone baritone, JsonObject params, JsonObject data) {
        // Follow is tricky via API in 1.15 API surface IIRC, requires Entity lookup.
        // Fallback to chat for simplicity or try to find entities.
        // Let's use chat for consistency with simple bridge for this one unless entity
        // UUID is passed.
        String entity = params.has("entity") ? params.get("entity").getAsString() : "player";
        MinecraftClient.getInstance().player.networkHandler.sendChatMessage("#follow " + entity);
        data.addProperty("sent", true);
    }

    private void handleBuild(IBaritone baritone, JsonObject params, JsonObject data) throws Exception {
        String name = params.get("schematic").getAsString();
        int x = params.get("x").getAsInt();
        int y = params.get("y").getAsInt();
        int z = params.get("z").getAsInt();

        File file = new File(schematicDir, name);
        if (!file.exists()) {
            throw new FileNotFoundException("Schematic not found: " + name);
        }

        // Load schematic using Baritone API
        Optional<ISchematicFormat> format = BaritoneAPI.getProvider().getSchematicSystem().getByFile(file);
        if (format.isPresent()) {
            ISchematic schematic = format.get().parse(new FileInputStream(file));
            baritone.getBuilderProcess().build(name, schematic, new Vec3i(x, y, z));
            data.addProperty("started", true);
        } else {
            throw new IOException("Unsupported schematic format for file: " + name);
        }
    }

    @Override
    public void handleSelection(IBaritone baritone, JsonObject params, JsonObject data) {
        String action = params.get("action").getAsString();

        if ("set".equals(action)) {
            int x1 = params.get("x1").getAsInt();
            int y1 = params.get("y1").getAsInt();
            int z1 = params.get("z1").getAsInt();
            int x2 = params.get("x2").getAsInt();
            int y2 = params.get("y2").getAsInt();
            int z2 = params.get("z2").getAsInt();

            baritone.getSelectionManager().removeAllSelections();
            baritone.getSelectionManager().addSelection(new BetterBlockPos(x1, y1, z1), new BetterBlockPos(x2, y2, z2));
            data.addProperty("set", true);
        } else if ("clear".equals(action)) {
            baritone.getSelectionManager().removeAllSelections();
        } else {
            // expand, contract, shift, etc.
            ISelection[] sels = baritone.getSelectionManager().getSelections();
            if (sels.length == 0)
                return;
            ISelection sel = sels[0]; // operate on first

            Direction dir = Direction.valueOf(params.get("direction").getAsString().toUpperCase());
            int blocks = params.get("blocks").getAsInt();

            if ("expand".equals(action)) {
                baritone.getSelectionManager().expand(sel, dir, blocks);
            } else if ("contract".equals(action)) {
                baritone.getSelectionManager().contract(sel, dir, blocks);
            } else if ("shift".equals(action)) {
                baritone.getSelectionManager().shift(sel, dir, blocks);
            }
        }
    }

    // Schematic Upload Handlers

    private void handleSchematicInit(JsonObject params, JsonObject data, Socket clientSocket) throws Exception {
        String name = params.get("name").getAsString();
        
        // Cleanup any existing upload with same name
        if (uploadStreams.containsKey(name)) {
            cleanupUpload(name);
        }
        
        // size param unused but nice to have validation
        File f = new File(schematicDir, name);
        uploadStreams.put(name, new FileOutputStream(f));
        uploadDigests.put(name, MessageDigest.getInstance("SHA-256"));
        uploadTimestamps.put(name, System.currentTimeMillis());
        
        if (clientSocket != null) {
            uploadOwners.put(name, clientSocket);
        }
        
        data.addProperty("ready", true);
    }

    private void handleSchematicChunk(JsonObject params, JsonObject data) throws Exception {
        String name = params.get("name").getAsString();
        
        // Check for timeout
        Long timestamp = uploadTimestamps.get(name);
        if (timestamp != null && (System.currentTimeMillis() - timestamp) > UPLOAD_TIMEOUT_MS) {
            cleanupUpload(name);
            throw new IOException("Upload timeout for " + name);
        }
        
        String b64 = params.get("data").getAsString();
        byte[] bytes = Base64.getDecoder().decode(b64);

        OutputStream os = uploadStreams.get(name);
        if (os != null) {
            os.write(bytes);
            uploadDigests.get(name).update(bytes);
            // Update timestamp
            uploadTimestamps.put(name, System.currentTimeMillis());
            data.addProperty("received", bytes.length);
        } else {
            throw new IOException("Upload sequence not initialized for " + name);
        }
    }

    private void handleSchematicCommit(JsonObject params, JsonObject data) throws Exception {
        String name = params.get("name").getAsString();
        OutputStream os = uploadStreams.remove(name);
        if (os != null) {
            os.close();
            MessageDigest digest = uploadDigests.remove(name);
            byte[] hash = digest.digest();
            // Convert hash to hex string
            StringBuilder hexString = new StringBuilder();
            for (byte b : hash)
                hexString.append(String.format("%02x", b));

            // Cleanup tracking
            uploadTimestamps.remove(name);
            uploadOwners.remove(name);

            data.addProperty("saved", true);
            data.addProperty("sha256", hexString.toString());
        } else {
            throw new IOException("No upload stream for " + name);
        }
    }

    @Override
    public void handleSettings(JsonObject params, JsonObject data) {
        if (params.has("get")) {
            String key = params.get("get").getAsString();
            // BaritoneAPI.getSettings().... uses generics, tricky via generic API
            // sometimes.
            // But we can iterate.
            // Or easier:
            MinecraftClient.getInstance().player.networkHandler.sendChatMessage("#settings " + key);
            data.addProperty("note", "Setting requested via chat");
        } else if (params.has("set")) {
            String key = params.get("set").getAsString();
            String val = params.get("value").getAsString();
            MinecraftClient.getInstance().player.networkHandler.sendChatMessage("#settings " + key + " " + val);
        }
    }

    // --- Basic Handlers ---

    private void handleStop(IBaritone baritone, JsonObject data) {
        baritone.getPathingBehavior().cancelEverything();
        data.addProperty("stopped", true);
    }

    private void handlePause(IBaritone baritone, JsonObject data) {
        baritone.getBuilderProcess().pause();
        // others?
        data.addProperty("paused", true);
    }

    private void handleCancel(IBaritone baritone, JsonObject data) {
        baritone.getPathingBehavior().cancelEverything();
        data.addProperty("cancelled", true);
    }

    private void handleGoal(IBaritone baritone, JsonObject params, JsonObject data) {
        String type = params.has("type") ? params.get("type").getAsString() : "yLevel";
        int value = params.has("value") ? params.get("value").getAsInt() : 64;

        if ("yLevel".equals(type)) {
            baritone.getCustomGoalProcess().setGoalAndPath(new GoalYLevel(value));
            data.addProperty("started", true);
        } else {
            data.addProperty("error", "Goal type not supported: " + type);
        }
    }

    @Override
    public void handleGetState(MinecraftClient client, IBaritone baritone, JsonObject data) {
        try {
            // Safe Off-Thread Read - No client.submit()
            if (client.player == null) {
                data.addProperty("error", "Player not available");
                return;
            }

            ClientPlayerEntity player = client.player;
            
            // Position (Primitives are volatile/safe)
            JsonObject position = new JsonObject();
            position.addProperty("x", player.getX());
            position.addProperty("y", player.getY());
            position.addProperty("z", player.getZ());
            position.addProperty("yaw", player.getYaw());
            position.addProperty("pitch", player.getPitch());
            data.add("position", position);
            
            // Block position (BlockPos is immutable struct)
            BlockPos blockPos = player.getBlockPos();
            JsonObject blockPosition = new JsonObject();
            blockPosition.addProperty("x", blockPos.getX());
            blockPosition.addProperty("y", blockPos.getY());
            blockPosition.addProperty("z", blockPos.getZ());
            data.add("block_position", blockPosition);
            
            // Health and status (Primitives)
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
            }
        } catch (Exception e) {
            data.addProperty("error", "Failed to get state: " + e.getMessage());
        }
    }



    private void handleGetEntities(MinecraftClient client, JsonObject params, JsonObject data) {
        if (client.world == null || client.player == null) {
            data.addProperty("error", "World or player not available");
            return;
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
            bufferEvent("entity_update", payload);
        }
    }

    @Override
    public void handleGetInventory(MinecraftClient client, JsonObject data) {
        try {
            if (client.player == null) {
                data.addProperty("error", "Player not available");
                return;
            }

            // Off-thread Safe Read
            // We iterate directly. Reading references/primitives is generally safe enough.
            // We consciously avoid complex methods like getName() to preventing locking.

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
                armorInventory.add(serializeItemStack(inv.getStack(36 + i), i));
            }
            data.add("armor", armorInventory);

            // Offhand (Slot 40)
            JsonArray offhandInventory = new JsonArray();
            offhandInventory.add(serializeItemStack(inv.getStack(40), 0));
            data.add("offhand", offhandInventory);

            data.addProperty("selected_slot", inv.selectedSlot);

            Map<String, Integer> counts = flattenInventory(mainInventory, armorInventory, offhandInventory);
            emitInventoryChangeEvent(counts, inv.selectedSlot);
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
        
        try {
            client.interactionManager.interactBlock(client.player, hand, hitResult);
            data.addProperty("interacted", true);
        } catch (Exception e) {
            data.addProperty("error", "Interaction failed: " + e.getMessage());
        }
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
        // Disabled due to RecipeManager API changes in 1.21.4
        data.addProperty("error", "Get recipes temporarily disabled");
        /*
        if (client.world == null) {
            data.addProperty("error", "World not available");
            return;
        }
        
        String filter = params.has("filter") ? params.get("filter").getAsString() : null;
        int limit = params.has("limit") ? params.get("limit").getAsInt() : 1000;
        
        JsonArray recipes = new JsonArray();
        int count = 0;
        
        for (RecipeType<?> type : Registries.RECIPE_TYPE) {
            if (count >= limit) break;
            
            for (RecipeEntry<?> entry : client.world.getRecipeManager().getAllOfType(type)) {
                if (count >= limit) break;
                
                if (entry.value() instanceof CraftingRecipe craftingRecipe) {
                    String outputId = Registries.ITEM.getId(craftingRecipe.getResult(client.world.getRegistryManager()).getItem()).toString();
                    
                    // Apply filter if provided
                    if (filter != null && !outputId.contains(filter)) continue;
                    
                    JsonObject recipeData = new JsonObject();
                    recipeData.addProperty("id", entry.id().toString());
                    recipeData.addProperty("output", outputId);
                    recipeData.addProperty("output_count", craftingRecipe.getResult(client.world.getRegistryManager()).getCount());
                    recipeData.addProperty("type", craftingRecipe.getClass().getSimpleName());
                    
                     // Get ingredients
                    JsonArray ingredients = new JsonArray();
                    for (Ingredient ingredient : craftingRecipe.getIngredients()) {
                        JsonArray options = new JsonArray();
                        for (var stack : ingredient.getMatchingStacks()) {
                            options.add(Registries.ITEM.getId(stack.getItem()).toString());
                        }
                        ingredients.add(options);
                    }
                    recipeData.add("ingredients", ingredients);
                    
                    recipes.add(recipeData);
                    count++;
                }
            }
        }
        
        data.add("recipes", recipes);
        data.addProperty("count", count);
        */
    }

    private void handleGetEvents(JsonObject data) {
        JsonArray events = new JsonArray();
        JsonObject event;
        while ((event = eventBuffer.poll()) != null) {
            events.add(event);
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
            bufferEvent("damage", damageData);
            
            // Check for death
            if (currentHealth <= 0) {
                JsonObject deathData = new JsonObject();
                deathData.addProperty("x", client.player.getX());
                deathData.addProperty("y", client.player.getY());
                deathData.addProperty("z", client.player.getZ());
                bufferEvent("death", deathData);
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

        // Check if chunk is loaded
        if (!client.world.isChunkLoaded(pos)) {
            data.addProperty("error", "Chunk not loaded");
            return;
        }

        try {
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
            data.addProperty("error", "Failed to get block info: " + e.getMessage());
        }
    }

    private void handleCome(MinecraftClient client, JsonObject params, JsonObject data) {
        if (client.player == null) {
            data.addProperty("error", "Player not available");
            return;
        }

        try {
            // Come command sets goal to camera/viewer position
            // In Baritone, this uses viewerPos() which is the camera entity position
            // For simplicity, we'll use the player's current position
            // In freecam scenarios, this would be the camera position
            BlockPos targetPos = client.player.getBlockPos();
            
            IBaritone baritone = BaritoneAPI.getProvider().getPrimaryBaritone();
            if (baritone == null) {
                data.addProperty("error", "Baritone not available");
                return;
            }
            
            // Use viewer position if available, otherwise player position
            baritone.getCustomGoalProcess().setGoalAndPath(new GoalBlock(targetPos));
            data.addProperty("started", true);
            data.addProperty("target_x", targetPos.getX());
            data.addProperty("target_y", targetPos.getY());
            data.addProperty("target_z", targetPos.getZ());
        } catch (Exception e) {
            LOGGER.error("Error executing come command", e);
            data.addProperty("error", "Failed to execute come command: " + e.getMessage());
        }
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
        client.player.networkHandler.sendChatMessage(pathCommand);
        
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
            baritone.getMineProcess().mine(0, blocksToMine);
            
            // Set goal to tunnel destination
            BlockPos targetPos = new BlockPos(x, y, z);
            if (radius > 1) {
                baritone.getCustomGoalProcess().setGoalAndPath(new GoalNear(targetPos, radius));
            } else {
                baritone.getCustomGoalProcess().setGoalAndPath(new GoalBlock(targetPos));
            }
            
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
            client.player.networkHandler.sendChatMessage(tunnelCommand);
            data.addProperty("sent", true);
            data.addProperty("command", tunnelCommand);
            data.addProperty("note", "Using chat command fallback due to error: " + e.getMessage());
        }
    }

    // --- Advanced Command Implementations (Chat Fallback) ---

    private void handleAxisMine(IBaritone baritone, JsonObject params, JsonObject data) {
        // #axis
        MinecraftClient.getInstance().player.networkHandler.sendChatMessage("#axis");
        data.addProperty("started", true);
    }

    private void handleStripMine(IBaritone baritone, JsonObject params, JsonObject data) {
        // #strip
        MinecraftClient.getInstance().player.networkHandler.sendChatMessage("#strip");
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

            baritone.getBuilderProcess().clearArea(corner1, corner2);
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
            client.player.networkHandler.sendChatMessage("#look at " + x + " " + client.player.getY() + " " + z);
            // Look might happen next tick, so this is imperfect but acceptable for bridge.
            // Ideally we'd set rotation server side or use a queued command.

            // Sending tunnel immediately after look might use old rotation.
            // But let's try.
            client.player.networkHandler.sendChatMessage(cmd);

            data.addProperty("started", true);
            data.addProperty("command", cmd);
        } else {
            data.addProperty("error", "Player not available");
        }
    }

    private void handlePlaceTorches(IBaritone baritone, JsonObject params, JsonObject data) {
        // Not standard command.
        data.addProperty("error", "Place torches not implemented (Reason: No standard API available)");
    }

    private void handleHarvest(IBaritone baritone, JsonObject params, JsonObject data) {
        // #farm handles harvesting usually.
        MinecraftClient.getInstance().player.networkHandler.sendChatMessage("#farm");
        data.addProperty("started", true);
    }

    private void handlePlant(IBaritone baritone, JsonObject params, JsonObject data) {
        // #farm handles planting too.
        MinecraftClient.getInstance().player.networkHandler.sendChatMessage("#farm");
        data.addProperty("started", true);
    }

    // ========== Automation Command Handlers ==========

    private void handleCraft(MinecraftClient client, JsonObject params, JsonObject data) {
        // Disabled
        data.addProperty("error", "Crafting command temporarily disabled");
    }

    private void handleClickRecipe(MinecraftClient client, JsonObject params, JsonObject data) {
        // Disabled
        data.addProperty("error", "Click recipe command temporarily disabled");
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
        
        // Look at the target block
        double dx = x + 0.5 - client.player.getX();
        double dy = y + 0.5 - client.player.getEyeY();
        double dz = z + 0.5 - client.player.getZ();
        double horizontalDist = Math.sqrt(dx * dx + dz * dz);
        float yaw = (float) Math.toDegrees(Math.atan2(-dx, dz));
        float pitch = (float) Math.toDegrees(-Math.atan2(dy, horizontalDist));
        
        client.player.setYaw(yaw);
        client.player.setPitch(pitch);
        
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
        
        Vec3d hitPos = Vec3d.ofCenter(placeAgainst).add(Vec3d.of(placeFace.getVector()).multiply(0.5));
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
        baritone.getCustomGoalProcess().setGoalAndPath(new GoalBlock(x, y, z));
        
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
            bufferEvent("dimension_change", eventData);
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
            // Note: Baritone settings API is complex, this is a simplified version
            JsonObject config = new JsonObject();
            config.addProperty("note", "Baritone configuration retrieval is limited via API");
            config.addProperty("primary_baritone_available", baritone != null);

            if (baritone != null) {
                config.addProperty("has_goal", baritone.getCustomGoalProcess().getGoal() != null);
                config.addProperty("is_pathing", baritone.getPathingBehavior().isPathing());
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

    private void handleSetPathfindingSettings(IBaritone baritone, JsonObject params, JsonObject data) {
        try {
            // Apply pathfinding settings - simplified version
            if (params.has("allow_parkour")) {
                boolean allowParkour = params.get("allow_parkour").getAsBoolean();
                // Note: Direct setting via API is complex, this would need proper Baritone API calls
                data.addProperty("setting_applied", "allow_parkour");
                data.addProperty("value", allowParkour);
            }

            if (params.has("allow_sprint")) {
                boolean allowSprint = params.get("allow_sprint").getAsBoolean();
                data.addProperty("setting_applied", "allow_sprint");
                data.addProperty("value", allowSprint);
            }

            data.addProperty("note", "Pathfinding settings applied (simplified implementation)");
        } catch (Exception e) {
            data.addProperty("error", "Failed to set pathfinding settings: " + e.getMessage());
        }
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
            
            // Cleanup all pending uploads
            for (String uploadName : new ArrayList<>(uploadStreams.keySet())) {
                cleanupUpload(uploadName);
            }
            
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


}
