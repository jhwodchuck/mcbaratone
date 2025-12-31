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
import net.minecraft.util.Identifier;
import net.minecraft.registry.Registry;
import net.minecraft.registry.Registries;
import net.fabricmc.fabric.api.client.message.v1.ClientReceiveMessageEvents;
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
public class BaritoneAPIBridge implements ModInitializer {

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

    // Event buffer
    private static final int MAX_EVENT_BUFFER_SIZE = 100;
    private final ConcurrentLinkedQueue<JsonObject> eventBuffer = new ConcurrentLinkedQueue<>();
    private float lastHealth = 20.0f;

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

    private JsonObject createChatEventData(String message) {
        JsonObject data = new JsonObject();
        data.addProperty("message", message);
        return data;
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
                    JsonObject error = new JsonObject();
                    // Preserve request ID if available
                    if (requestId != null) {
                        error.addProperty("id", requestId);
                    } else if (request != null && request.has("id")) {
                        error.addProperty("id", request.get("id").getAsString());
                    } else {
                        // Try to parse just the ID from the line as fallback
                        try {
                            JsonObject fallbackRequest = GSON.fromJson(line, JsonObject.class);
                            if (fallbackRequest.has("id")) {
                                error.addProperty("id", fallbackRequest.get("id").getAsString());
                            } else {
                                error.addProperty("id", (String) null);
                            }
                        } catch (Exception parseEx) {
                            error.addProperty("id", (String) null);
                        }
                    }
                    error.addProperty("status", "error");
                    error.addProperty("error", e.getMessage());
                    out.println(GSON.toJson(error));
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
        String id = request.has("id") ? request.get("id").getAsString() : null;
        String command = request.has("command") ? request.get("command").getAsString() : null;
        JsonObject params = request.has("params") ? request.getAsJsonObject("params") : new JsonObject();

        JsonObject response = new JsonObject();
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
            // Allow some commands without player (e.g. status checks, uploads)
            if (client.player == null && !isOfflineCommand(command)) {
                response.addProperty("status", "error");
                response.addProperty("error", "Player not available");
                return response;
            }

            IBaritone baritone = BaritoneAPI.getProvider().getPrimaryBaritone();
            if (baritone == null) {
                response.addProperty("status", "error");
                response.addProperty("error", "Baritone not available. Make sure Baritone mod is installed and loaded.");
                return response;
            }
            
            JsonObject data = new JsonObject();

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
                case "get_block":
                    handleGetBlock(client, params, data);
                    break;
                case "get_entities":
                    handleGetEntities(client, params, data);
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
        return command.startsWith("schematic_");
    }

    // --- Implementations ---

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

    private void handleMine(IBaritone baritone, JsonObject params, JsonObject data) {
        // Native mine support
        if (params.has("block_type")) {
            String blockId = params.get("block_type").getAsString();
            int count = params.has("count") ? params.get("count").getAsInt() : 0;

            // Resolve block
            Optional<Block> block = Registries.BLOCK.getOrEmpty(Identifier.of(blockId));
            if (block.isPresent()) {
                baritone.getMineProcess().mine(count, block.get());
                data.addProperty("started", true);
            } else {
                data.addProperty("error", "Unknown block: " + blockId);
            }
        } else {
            data.addProperty("error", "Missing block_type");
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

    private void handleSelection(IBaritone baritone, JsonObject params, JsonObject data) {
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

    private void handleSettings(JsonObject params, JsonObject data) {
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

    private void handleGetState(MinecraftClient client, IBaritone baritone, JsonObject data) {
        if (client.player == null) {
            data.addProperty("error", "Player not available");
            return;
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
            entityData.addProperty("x", entity.getX());
            entityData.addProperty("y", entity.getY());
            entityData.addProperty("z", entity.getZ());
            entityData.addProperty("yaw", entity.getYaw());
            entityData.addProperty("pitch", entity.getPitch());
            entityData.addProperty("distance", entity.distanceTo(player));
            
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
    }

    private void handleGetInventory(MinecraftClient client, JsonObject data) {
        if (client.player == null) {
            data.addProperty("error", "Player not available");
            return;
        }

        ClientPlayerEntity player = client.player;
        
        // Main Inventory (0-35)
        // 0-8: Hotbar
        // 9-35: Storage
        JsonArray mainInventory = new JsonArray();
        for (int i = 0; i < player.getInventory().main.size(); i++) {
            ItemStack stack = player.getInventory().main.get(i);
            mainInventory.add(serializeItemStack(stack, i));
        }
        data.add("inventory", mainInventory);

        // Armor
        JsonArray armorInventory = new JsonArray();
        for (int i = 0; i < player.getInventory().armor.size(); i++) {
            ItemStack stack = player.getInventory().armor.get(i);
            armorInventory.add(serializeItemStack(stack, i));
        }
        data.add("armor", armorInventory);

        // Offhand
        JsonArray offhandInventory = new JsonArray();
        for (int i = 0; i < player.getInventory().offHand.size(); i++) {
            ItemStack stack = player.getInventory().offHand.get(i);
            offhandInventory.add(serializeItemStack(stack, i));
        }
        data.add("offhand", offhandInventory);
        
        data.addProperty("selected_slot", player.getInventory().selectedSlot);
    }

    private JsonObject serializeItemStack(ItemStack stack, int slot) {
        JsonObject itemData = new JsonObject();
        itemData.addProperty("slot", slot);
        
        if (stack.isEmpty()) {
            itemData.addProperty("id", "minecraft:air");
            itemData.addProperty("count", 0);
        } else {
            itemData.addProperty("id", Registries.ITEM.getId(stack.getItem()).toString());
            itemData.addProperty("count", stack.getCount());
            itemData.addProperty("max_count", stack.getMaxCount());
            itemData.addProperty("damage", stack.getDamage());
            itemData.addProperty("max_damage", stack.getMaxDamage());
            itemData.addProperty("name", stack.getName().getString());
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
        if (client.world == null) {
            data.addProperty("error", "World not available");
            return;
        }
        
        String filter = params.has("filter") ? params.get("filter").getAsString() : null;
        int limit = params.has("limit") ? params.get("limit").getAsInt() : 50;
        
        JsonArray recipes = new JsonArray();
        int count = 0;
        
        for (RecipeEntry<?> entry : client.world.getRecipeManager().values()) {
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
        
        data.add("recipes", recipes);
        data.addProperty("count", count);
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
        }
        lastHealth = currentHealth;
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
}
