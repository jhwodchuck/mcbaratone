package com.minecraftbot.baritone;
import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.*;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;

/**
 * MissionController handles high-level automation missions backed by simple macros.
 */
public class MissionController {

    private enum MissionPhase {
        IDLE("idle"),
        BOOTSTRAP("bootstrap"),
        BASE_ESTABLISHED("base_established"),
        RESOURCE_GATHERING("resource_gathering"),
        NETHER_READY("nether_ready"),
        EYES_READY("eyes_ready"),
        STRONGHOLD_HUNT("stronghold_hunt"),
        FINAL_BATTLE("final_battle"),
        COMPLETE("complete");

        private final String value;

        MissionPhase(String value) {
            this.value = value;
        }

        public String value() {
            return value;
        }

        public static MissionPhase fromString(String raw) {
            if (raw == null) {
                return null;
            }
            for (MissionPhase phase : values()) {
                if (phase.value.equalsIgnoreCase(raw) || phase.name().equalsIgnoreCase(raw)) {
                    return phase;
                }
            }
            return null;
        }
    }

    private interface MissionHandler {
        void handle(JsonObject params, JsonObject data, Minecraft client, IBaritone baritone, Socket socket);
    }

    private final MissionBridgeAdapter bridge;
    private final Map<String, MissionHandler> handlers = new HashMap<>();
    private final Deque<String> queue = new ArrayDeque<>();
    private final LinkedList<String> history = new LinkedList<>();
    private final Set<String> offlineSafe =
        Collections.unmodifiableSet(new HashSet<>(Collections.singletonList("mission_status")));

    private MissionPhase phase = MissionPhase.IDLE;
    private String note = "not_started";
    private long lastUpdate = System.currentTimeMillis();
    private Socket missionOwner;
    private JsonObject lastMacroResult = new JsonObject();

    // Enhanced tracking for mission automation
    private final Map<String, Long> phaseStartTimes = new HashMap<>();
    private final Map<String, Integer> phaseRetries = new HashMap<>();
    private final List<JsonObject> phaseHistory = new ArrayList<>();
    private long missionStartTime = 0;

    MissionController(MissionBridgeAdapter bridge) {
        this.bridge = bridge;
        handlers.put("mission_status", this::handleStatus);
        handlers.put("mission_checkpoint", this::handleCheckpoint);
        handlers.put("mission_macro", this::handleMacro);
        handlers.put("mission_queue", this::handleQueue);
        handlers.put("mission_advance", this::handleAdvance);
        handlers.put("mission_retry", this::handleRetry);
        handlers.put("mission_reset", this::handleReset);
    }

    synchronized boolean tryHandle(
        String command,
        JsonObject params,
        JsonObject data,
        Minecraft client,
        IBaritone baritone,
        Socket socket
    ) {
        MissionHandler handler = handlers.get(command);
        if (handler == null) {
            return false;
        }
        handler.handle(params, data, client, baritone, socket);
        return true;
    }

    boolean isOfflineSafe(String command) {
        return offlineSafe.contains(command);
    }

    synchronized int queueSize() {
        return queue.size();
    }

    synchronized void releaseOwner(Socket socket) {
        if (socket != null && socket.equals(missionOwner)) {
            missionOwner = null;
        }
    }

    String getPhaseValue() {
        return phase.value();
    }

    private boolean ensureOwner(Socket socket, JsonObject data) {
        if (socket == null) {
            data.addProperty("error", "Mission commands require an identified client");
            data.addProperty("error_code", "missing_owner");
            return false;
        }
        if (missionOwner == null) {
            missionOwner = socket;
            return true;
        }
        if (missionOwner.equals(socket)) {
            return true;
        }
        data.addProperty("error", "Mission is currently controlled by another client");
        data.addProperty("error_code", "locked");
        return false;
    }

    private void handleStatus(JsonObject params, JsonObject data, Minecraft client, IBaritone baritone, Socket socket) {
        data.add("mission", buildMissionStatus(client, baritone));
    }

    private void handleCheckpoint(JsonObject params, JsonObject data, Minecraft client, IBaritone baritone, Socket socket) {
        if (!ensureOwner(socket, data)) {
            return;
        }
        MissionPhase requested = MissionPhase.fromString(params.has("phase") ? params.get("phase").getAsString() : null);
        if (requested == null) {
            data.addProperty("error", "Unknown phase");
            data.addProperty("error_code", "invalid_phase");
            return;
        }
        String newNote = params.has("note") ? params.get("note").getAsString() : note;
        updatePhase(requested, newNote);
        data.add("mission", buildMissionStatus(client, baritone));
    }

    private void handleQueue(JsonObject params, JsonObject data, Minecraft client, IBaritone baritone, Socket socket) {
        boolean touchedQueue = false;
        if (params.has("clear") && params.get("clear").getAsBoolean()) {
            if (!ensureOwner(socket, data)) {
                return;
            }
            queue.clear();
            touchedQueue = true;
        }
        if (params.has("actions") && params.get("actions").isJsonArray()) {
            if (!ensureOwner(socket, data)) {
                return;
            }
            for (JsonElement elem : params.getAsJsonArray("actions")) {
                if (elem.isJsonPrimitive()) {
                    enqueue(elem.getAsString());
                }
            }
            touchedQueue = true;
        }
        data.addProperty("queued", queue.size());
        data.add("queue", toJsonArray(queue));
        data.add("mission", buildMissionStatus(client, baritone));
        if (touchedQueue) {
            JsonObject payload = new JsonObject();
            payload.add("queue", toJsonArray(queue));
            bridge.publishMissionEvent("queue_update", payload);
        }
    }

    private void handleMacro(JsonObject params, JsonObject data, Minecraft client, IBaritone baritone, Socket socket) {
        if (!ensureOwner(socket, data)) {
            return;
        }

        String macroName = params.has("name") ? params.get("name").getAsString() : null;
        boolean dequeue = params.has("dequeue") && params.get("dequeue").getAsBoolean();
        if ((macroName == null || macroName.isEmpty()) && dequeue) {
            macroName = queue.pollFirst();
            if (macroName == null) {
                data.addProperty("error", "Macro queue empty");
                data.addProperty("error_code", "empty_queue");
                return;
            }
        }

        if (macroName == null || macroName.isEmpty()) {
            data.addProperty("error", "Missing macro name");
            data.addProperty("error_code", "missing_macro");
            return;
        }

        JsonObject macroParams = params.has("params") && params.get("params").isJsonObject()
            ? params.getAsJsonObject("params")
            : new JsonObject();

        JsonObject macroResult = new JsonObject();
        if (!runMacro(macroName, macroParams, macroResult, client, baritone)) {
            data.addProperty("error", "Unknown macro: " + macroName);
            data.addProperty("error_code", "unknown_macro");
            return;
        }

        lastMacroResult = macroResult.deepCopy();
        data.addProperty("macro", macroName);
        data.add("result", macroResult);
        data.add("mission", buildMissionStatus(client, baritone));

        JsonObject payload = new JsonObject();
        payload.addProperty("macro", macroName);
        payload.add("result", macroResult);
        bridge.publishMissionEvent("macro", payload);
    }

    private JsonObject buildMissionStatus(Minecraft client, IBaritone baritone) {
        JsonObject mission = new JsonObject();
        mission.addProperty("phase", phase.value());
        mission.addProperty("note", note);
        mission.addProperty("last_update_ms", lastUpdate);
        mission.add("telemetry", bridge.collectTelemetry(client, baritone));
        mission.add("queue", toJsonArray(queue));
        mission.add("history", toJsonArray(history));
        mission.add("last_macro", lastMacroResult.deepCopy());
        return mission;
    }

    private void enqueue(String action) {
        if (action == null || action.isEmpty()) {
            return;
        }
        queue.addLast(action);
        while (queue.size() > 32) {
            queue.removeFirst();
        }
    }

    private JsonArray toJsonArray(Collection<String> values) {
        JsonArray array = new JsonArray();
        for (String value : values) {
            array.add(value);
        }
        return array;
    }

    private void updatePhase(MissionPhase newPhase, String newNote) {
        if (newPhase != null && newPhase != phase) {
            // Record old phase completion
            if (phase != MissionPhase.IDLE) {
                JsonObject phaseRecord = new JsonObject();
                phaseRecord.addProperty("phase", phase.value());
                phaseRecord.addProperty("start_time", phaseStartTimes.getOrDefault(phase.value(), lastUpdate));
                phaseRecord.addProperty("end_time", System.currentTimeMillis());
                phaseRecord.addProperty("duration_ms", System.currentTimeMillis() - phaseStartTimes.getOrDefault(phase.value(), lastUpdate));
                phaseRecord.addProperty("completed", true);
                phaseHistory.add(phaseRecord);
            }

            // Start new phase
            phase = newPhase;
            phaseStartTimes.put(newPhase.value(), System.currentTimeMillis());
            if (missionStartTime == 0) {
                missionStartTime = System.currentTimeMillis();
            }
        }

        if (newNote != null) {
            note = newNote;
        }
        lastUpdate = System.currentTimeMillis();
        recordHistory(phase.value());

        JsonObject eventPayload = new JsonObject();
        eventPayload.addProperty("phase", phase.value());
        eventPayload.addProperty("note", note);
        eventPayload.addProperty("phase_start_time", phaseStartTimes.getOrDefault(phase.value(), lastUpdate));
        bridge.publishMissionEvent("phase_change", eventPayload);
    }

    private void recordHistory(String entry) {
        history.add(entry);
        while (history.size() > 25) {
            history.removeFirst();
        }
    }

    private boolean runMacro(String macroName, JsonObject params, JsonObject result, Minecraft client, IBaritone baritone) {
        switch (macroName) {
            case "bootstrap":
                runBootstrapMacro(params, result, client, baritone);
                updatePhase(MissionPhase.BOOTSTRAP, "Bridge defaults applied");
                return true;
            case "scout_spawn":
                runScoutSpawnMacro(params, result, client, baritone);
                updatePhase(MissionPhase.BOOTSTRAP, "Spawn scouted");
                return true;
            case "establish_base":
                runBaseMacro(params, result, client, baritone);
                updatePhase(MissionPhase.BASE_ESTABLISHED, "Starter base perimeter cleared");
                return true;
            case "resource_pipeline":
                runResourceMacro(params, result, baritone);
                updatePhase(MissionPhase.RESOURCE_GATHERING, "Resource pipeline started");
                return true;
            case "enter_nether":
                runNetherPrepMacro(params, result, client);
                if (result.has("ready") && result.get("ready").getAsBoolean()) {
                    updatePhase(MissionPhase.NETHER_READY, "Portal materials ready");
                }
                return true;
            case "craft_eyes":
                runCraftEyesMacro(params, result, client);
                if (result.has("ready") && result.get("ready").getAsBoolean()) {
                    updatePhase(MissionPhase.EYES_READY, "Eyes crafted");
                }
                return true;
            case "dragon_prep":
                runDragonPrepMacro(result, client);
                updatePhase(MissionPhase.FINAL_BATTLE, "Dragon prep validated");
                return true;
            case "locate_stronghold":
                runStrongholdMacro(result, client);
                updatePhase(MissionPhase.STRONGHOLD_HUNT, "Stronghold search initiated");
                return true;
            case "fight_dragon":
                runFightDragonMacro(result, client, baritone);
                updatePhase(MissionPhase.FINAL_BATTLE, "Engaging dragon");
                return true;
            default:
                return false;
        }
    }

    private void runBootstrapMacro(JsonObject params, JsonObject result, Minecraft client, IBaritone baritone) {
        Map<String, String> settings = new LinkedHashMap<>();
        settings.put("allowSprint", "true");
        settings.put("allowParkour", "true");
        settings.put("allowBreak", "true");
        settings.put("renderGoal", "true");
        settings.put("failureTimeoutMs", "8000");
        if (params.has("settings") && params.get("settings").isJsonObject()) {
            JsonObject overrides = params.getAsJsonObject("settings");
            for (Map.Entry<String, JsonElement> entry : overrides.entrySet()) {
                settings.put(entry.getKey(), entry.getValue().getAsString());
            }
        }

        for (Map.Entry<String, String> entry : settings.entrySet()) {
            JsonObject settingParams = new JsonObject();
            settingParams.addProperty("set", entry.getKey());
            settingParams.addProperty("value", entry.getValue());
            JsonObject sink = new JsonObject();
            bridge.handleSettings(settingParams, sink);
        }

        result.addProperty("settingsApplied", settings.size());
        result.add("telemetry", bridge.collectTelemetry(client, baritone));
    }

    private void runScoutSpawnMacro(JsonObject params, JsonObject result, Minecraft client, IBaritone baritone) {
        if (client.player == null) {
            result.addProperty("error", "Player not available");
            return;
        }
        int radius = params.has("radius") ? params.get("radius").getAsInt() : 96;
        int samples = params.has("samples") ? params.get("samples").getAsInt() : 4;
        BlockPos center = client.player.blockPosition();

        JsonArray waypoints = new JsonArray();
        for (int i = 0; i < samples; i++) {
            int dx = (int) (Math.cos((2 * Math.PI / samples) * i) * radius);
            int dz = (int) (Math.sin((2 * Math.PI / samples) * i) * radius);
            waypoints.add(blockPosToJson(center.offset(dx, 0, dz)));
        }

        result.add("origin", blockPosToJson(center));
        result.add("waypoints", waypoints);
        result.addProperty("radius", radius);
        result.addProperty("samples", samples);
        try {
            baritone.getExploreProcess().explore(center.getX(), center.getZ());
            result.addProperty("explore_started", true);
        } catch (Exception e) {
            result.addProperty("explore_error", e.getMessage());
        }
        result.add("telemetry", bridge.collectTelemetry(client, baritone));
    }

    private void runBaseMacro(JsonObject params, JsonObject result, Minecraft client, IBaritone baritone) {
        if (client.player == null) {
            result.addProperty("error", "Player not available");
            return;
        }
        int size = params.has("size") ? params.get("size").getAsInt() : 13;
        int height = params.has("height") ? params.get("height").getAsInt() : 6;

        BlockPos center = client.player.blockPosition();
        BlockPos corner1 = center.offset(size / 2, 0, size / 2);
        BlockPos corner2 = center.offset(-size / 2, -height, -size / 2);

        JsonObject selParams = new JsonObject();
        selParams.addProperty("action", "set");
        selParams.addProperty("x1", corner1.getX());
        selParams.addProperty("y1", corner1.getY());
        selParams.addProperty("z1", corner1.getZ());
        selParams.addProperty("x2", corner2.getX());
        selParams.addProperty("y2", corner2.getY());
        selParams.addProperty("z2", corner2.getZ());
        bridge.handleSelection(baritone, selParams, new JsonObject());

        baritone.getBuilderProcess().clearArea(corner1, corner2);

        JsonObject base = new JsonObject();
        base.addProperty("size", size);
        base.addProperty("height", height);
        base.add("corner_a", blockPosToJson(corner1));
        base.add("corner_b", blockPosToJson(corner2));
        result.add("base", base);
    }

    private JsonObject blockPosToJson(BlockPos pos) {
        JsonObject json = new JsonObject();
        json.addProperty("x", pos.getX());
        json.addProperty("y", pos.getY());
        json.addProperty("z", pos.getZ());
        return json;
    }

    private void runResourceMacro(JsonObject params, JsonObject result, IBaritone baritone) {
        List<JsonObject> targets = new ArrayList<>();
        if (params.has("targets") && params.get("targets").isJsonArray()) {
            JsonArray array = params.getAsJsonArray("targets");
            for (JsonElement element : array) {
                if (element.isJsonObject()) {
                    targets.add(element.getAsJsonObject());
                } else if (element.isJsonPrimitive()) {
                    JsonObject target = new JsonObject();
                    target.addProperty("block", element.getAsString());
                    targets.add(target);
                }
            }
        }

        if (targets.isEmpty()) {
            targets.add(buildMiningTarget("minecraft:iron_ore", 64));
            targets.add(buildMiningTarget("minecraft:diamond_ore", 12));
            targets.add(buildMiningTarget("minecraft:obsidian", 20));
            targets.add(buildMiningTarget("minecraft:nether_quartz_ore", 16));
        }

        JsonArray actions = new JsonArray();
        for (JsonObject target : targets) {
            JsonObject mineParams = new JsonObject();
            String blockOrItem = target.has("block") ? target.get("block").getAsString() : target.get("item").getAsString();
            if (blockOrItem.startsWith("minecraft:")) {
                mineParams.addProperty("block_type", blockOrItem);
            }
            if (target.has("count")) {
                mineParams.addProperty("count", target.get("count").getAsInt());
            }
            JsonObject action = new JsonObject();
            bridge.handleMine(baritone, mineParams, action);
            action.addProperty("target", blockOrItem);
            if (target.has("stage")) {
                action.addProperty("stage", target.get("stage").getAsString());
            }
            actions.add(action);
        }

        result.add("actions", actions);
    }

    private JsonObject buildMiningTarget(String block, int count) {
        JsonObject target = new JsonObject();
        target.addProperty("block", block);
        target.addProperty("count", count);
        return target;
    }

    private void runNetherPrepMacro(JsonObject params, JsonObject result, Minecraft client) {
        JsonObject inventoryPayload = new JsonObject();
        bridge.handleGetInventory(client, inventoryPayload);
        Map<String, Integer> counts = summarizeInventory(inventoryPayload);

        int requiredObsidian = params.has("obsidian") ? params.get("obsidian").getAsInt() : 12;
        boolean hasObsidian = counts.getOrDefault("minecraft:obsidian", 0) >= requiredObsidian;
        boolean hasFlintSteel = counts.containsKey("minecraft:flint_and_steel");
        boolean hasIgnitionPlan = hasFlintSteel ||
            (counts.getOrDefault("minecraft:flint", 0) > 0 && counts.getOrDefault("minecraft:iron_ingot", 0) > 0);

        result.addProperty("hasObsidian", hasObsidian);
        result.addProperty("hasIgnition", hasIgnitionPlan);
        result.addProperty("requiredObsidian", requiredObsidian);
        result.add("inventory_counts", mapToJson(counts));
        result.addProperty("ready", hasObsidian && hasIgnitionPlan);
    }

    private void runCraftEyesMacro(JsonObject params, JsonObject result, Minecraft client) {
        JsonObject inventoryPayload = new JsonObject();
        bridge.handleGetInventory(client, inventoryPayload);
        Map<String, Integer> counts = summarizeInventory(inventoryPayload);

        int pearls = counts.getOrDefault("minecraft:ender_pearl", 0);
        int powder = counts.getOrDefault("minecraft:blaze_powder", 0);
        int required = params.has("required") ? params.get("required").getAsInt() : 12;
        int craftable = Math.min(pearls, powder);

        result.addProperty("pearls", pearls);
        result.addProperty("blaze_powder", powder);
        result.addProperty("craftableEyes", craftable);
        result.addProperty("required", required);
        result.addProperty("ready", craftable >= required);
    }

    private void runDragonPrepMacro(JsonObject result, Minecraft client) {
        JsonObject inventoryPayload = new JsonObject();
        bridge.handleGetInventory(client, inventoryPayload);
        Map<String, Integer> counts = summarizeInventory(inventoryPayload);

        int arrows = counts.getOrDefault("minecraft:arrow", 0);
        int beds = counts.getOrDefault("minecraft:bed", 0) + counts.getOrDefault("minecraft:white_bed", 0);
        int slowFalling = counts.getOrDefault("minecraft:potion{Potion:\"minecraft:slow_falling\"}", 0);
        int pearls = counts.getOrDefault("minecraft:ender_pearl", 0);

        result.addProperty("arrows", arrows);
        result.addProperty("beds", beds);
        result.addProperty("slow_falling", slowFalling);
        result.addProperty("pearls", pearls);
        result.addProperty("ready", arrows >= 64 && beds >= 4 && slowFalling >= 2 && pearls >= 12);
        result.add("inventory_counts", mapToJson(counts));
    }

    private void runStrongholdMacro(JsonObject result, Minecraft client) {
        if (client.player == null) {
            result.addProperty("error", "Player not available");
            return;
        }
        client.execute(() -> client.player.connection.sendChat("#stronghold"));
        result.addProperty("command", "#stronghold");
    }

    private void runFightDragonMacro(JsonObject result, Minecraft client, IBaritone baritone) {
        JsonObject statePayload = new JsonObject();
        bridge.handleGetState(client, baritone, statePayload);
        result.add("state", statePayload);
    }

    private Map<String, Integer> summarizeInventory(JsonObject payload) {
        Map<String, Integer> counts = new HashMap<>();
        accumulateCounts(payload.get("inventory"), counts);
        accumulateCounts(payload.get("offhand"), counts);
        accumulateCounts(payload.get("armor"), counts);
        return counts;
    }

    private void accumulateCounts(JsonElement element, Map<String, Integer> counts) {
        if (element == null || !element.isJsonArray()) {
            return;
        }
        JsonArray array = element.getAsJsonArray();
        for (JsonElement entry : array) {
            if (!entry.isJsonObject()) {
                continue;
            }
            JsonObject obj = entry.getAsJsonObject();
            String id = obj.has("id") ? obj.get("id").getAsString() : "minecraft:air";
            int count = obj.has("count") ? obj.get("count").getAsInt() : 0;
            counts.merge(id, count, Integer::sum);
        }
    }

    private JsonObject mapToJson(Map<String, Integer> counts) {
        JsonObject json = new JsonObject();
        for (Map.Entry<String, Integer> entry : counts.entrySet()) {
            json.addProperty(entry.getKey(), entry.getValue());
        }
        return json;
    }

    // ========== Enhanced Mission Handler Methods ==========

    private void handleAdvance(JsonObject params, JsonObject data, Minecraft client, IBaritone baritone, Socket socket) {
        if (!ensureOwner(socket, data)) {
            return;
        }

        boolean force = params.has("force") && params.get("force").getAsBoolean();
        MissionPhase nextPhase = getNextPhase();

        if (nextPhase != null && (force || canAdvanceTo(nextPhase, client, baritone))) {
            updatePhase(nextPhase, "Auto-advanced to next phase");
            data.addProperty("advanced", true);
            data.addProperty("new_phase", nextPhase.value());
        } else {
            data.addProperty("advanced", false);
            data.addProperty("reason", force ? "Next phase not available" : "Prerequisites not met");
        }

        data.add("mission", buildMissionStatus(client, baritone));
    }

    private void handleRetry(JsonObject params, JsonObject data, Minecraft client, IBaritone baritone, Socket socket) {
        if (!ensureOwner(socket, data)) {
            return;
        }

        String phaseName = params.has("phase") ? params.get("phase").getAsString() : phase.value();
        MissionPhase retryPhase = MissionPhase.fromString(phaseName);

        if (retryPhase == null) {
            data.addProperty("error", "Invalid phase for retry");
            return;
        }

        int currentRetries = phaseRetries.getOrDefault(phaseName, 0);
        int maxRetries = params.has("max_retries") ? params.get("max_retries").getAsInt() : 3;

        if (currentRetries < maxRetries) {
            phaseRetries.put(phaseName, currentRetries + 1);
            updatePhase(retryPhase, "Retrying phase (attempt " + (currentRetries + 1) + "/" + maxRetries + ")");
            data.addProperty("retry_attempt", currentRetries + 1);
            data.addProperty("max_retries", maxRetries);
        } else {
            data.addProperty("error", "Maximum retry attempts exceeded for phase: " + phaseName);
        }

        data.add("mission", buildMissionStatus(client, baritone));
    }

    private void handleReset(JsonObject params, JsonObject data, Minecraft client, IBaritone baritone, Socket socket) {
        if (!ensureOwner(socket, data)) {
            return;
        }

        // Reset all mission state
        phase = MissionPhase.IDLE;
        note = "mission_reset";
        lastUpdate = System.currentTimeMillis();
        missionOwner = null;
        lastMacroResult = new JsonObject();
        phaseStartTimes.clear();
        phaseRetries.clear();
        phaseHistory.clear();
        queue.clear();
        history.clear();
        missionStartTime = 0;

        data.addProperty("reset", true);
        data.add("mission", buildMissionStatus(client, baritone));

        JsonObject payload = new JsonObject();
        payload.addProperty("action", "reset");
        bridge.publishMissionEvent("mission_reset", payload);
    }

    private MissionPhase getNextPhase() {
        switch (phase) {
            case IDLE: return MissionPhase.BOOTSTRAP;
            case BOOTSTRAP: return MissionPhase.BASE_ESTABLISHED;
            case BASE_ESTABLISHED: return MissionPhase.RESOURCE_GATHERING;
            case RESOURCE_GATHERING: return MissionPhase.NETHER_READY;
            case NETHER_READY: return MissionPhase.EYES_READY;
            case EYES_READY: return MissionPhase.STRONGHOLD_HUNT;
            case STRONGHOLD_HUNT: return MissionPhase.FINAL_BATTLE;
            case FINAL_BATTLE: return MissionPhase.COMPLETE;
            default: return null;
        }
    }

    private boolean canAdvanceTo(MissionPhase nextPhase, Minecraft client, IBaritone baritone) {
        // Check basic prerequisites based on current inventory and state
        switch (nextPhase) {
            case BASE_ESTABLISHED:
                // Basic survival items
                return hasBasicSurvivalItems(client);
            case RESOURCE_GATHERING:
                // Iron tools and basic resources
                return hasResourceGatheringItems(client);
            case NETHER_READY:
                // Nether portal materials
                return hasNetherMaterials(client);
            case EYES_READY:
                // Ender pearls and blaze powder
                return hasEnderMaterials(client);
            case STRONGHOLD_HUNT:
                // Eyes of Ender ready
                return hasEyesOfEnder(client);
            case FINAL_BATTLE:
                // Stronghold located, end access ready
                return hasEndAccessItems(client);
            default:
                return true;
        }
    }

    private boolean hasBasicSurvivalItems(Minecraft client) {
        if (client.player == null) return false;
        JsonObject inventoryPayload = new JsonObject();
        bridge.handleGetInventory(client, inventoryPayload);
        Map<String, Integer> counts = summarizeInventory(inventoryPayload);
        return counts.getOrDefault("minecraft:wood", 0) >= 16 &&
               counts.getOrDefault("minecraft:stone_pickaxe", 0) >= 1;
    }

    private boolean hasResourceGatheringItems(Minecraft client) {
        if (client.player == null) return false;
        JsonObject inventoryPayload = new JsonObject();
        bridge.handleGetInventory(client, inventoryPayload);
        Map<String, Integer> counts = summarizeInventory(inventoryPayload);
        return counts.getOrDefault("minecraft:iron_pickaxe", 0) >= 1 &&
               counts.getOrDefault("minecraft:iron_ingot", 0) >= 10;
    }

    private boolean hasNetherMaterials(Minecraft client) {
        if (client.player == null) return false;
        JsonObject inventoryPayload = new JsonObject();
        bridge.handleGetInventory(client, inventoryPayload);
        Map<String, Integer> counts = summarizeInventory(inventoryPayload);
        return counts.getOrDefault("minecraft:obsidian", 0) >= 10 &&
               (counts.containsKey("minecraft:flint_and_steel") ||
                (counts.getOrDefault("minecraft:flint", 0) > 0 && counts.getOrDefault("minecraft:iron_ingot", 0) > 0));
    }

    private boolean hasEnderMaterials(Minecraft client) {
        if (client.player == null) return false;
        JsonObject inventoryPayload = new JsonObject();
        bridge.handleGetInventory(client, inventoryPayload);
        Map<String, Integer> counts = summarizeInventory(inventoryPayload);
        return counts.getOrDefault("minecraft:ender_pearl", 0) >= 12 &&
               counts.getOrDefault("minecraft:blaze_powder", 0) >= 12;
    }

    private boolean hasEyesOfEnder(Minecraft client) {
        if (client.player == null) return false;
        JsonObject inventoryPayload = new JsonObject();
        bridge.handleGetInventory(client, inventoryPayload);
        Map<String, Integer> counts = summarizeInventory(inventoryPayload);
        return counts.getOrDefault("minecraft:ender_eye", 0) >= 1;
    }

    private boolean hasEndAccessItems(Minecraft client) {
        if (client.player == null) return false;
        // Check if in the End dimension or has end portal access
        return client.level != null &&
               "minecraft:the_end".equals(client.level.dimension().identifier().toString());
    }


}
