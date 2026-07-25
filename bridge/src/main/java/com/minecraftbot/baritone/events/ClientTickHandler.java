package com.minecraftbot.baritone.events;

import baritone.api.BaritoneAPI;
import baritone.api.IBaritone;
import com.minecraftbot.baritone.BaritoneAPIBridge;
import com.minecraftbot.baritone.EventManager;
import com.google.gson.JsonObject;
import net.minecraft.block.BlockState;
import net.minecraft.client.MinecraftClient;
import net.minecraft.entity.Entity;
import net.minecraft.entity.damage.DamageSource;
import net.minecraft.registry.Registries;
import net.minecraft.util.math.BlockPos;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Handles per-tick logic for the Baritone Bridge.
 * Encapsulates entity tracking, pathfinding state monitoring,
 * and environmental checks (time, weather, block updates).
 */
public class ClientTickHandler {

    private static final Logger LOGGER = LoggerFactory.getLogger("baritone-tick-handler");
    private static final long TICK_EVENT_INTERVAL_MS = 750;

    private final BaritoneAPIBridge.IPlayerContext playerContext;
    private final EventManager eventManager;

    // State tracking
    private int tickCounter = 0;
    private IBaritone lastBaritone = null;
    private boolean lastPathingState = false;
    private final Map<Integer, BlockPos> lastEntityPositions = new HashMap<>();
    private long lastTickEventTime = 0L;

    // Weather and time tracking
    private boolean lastRaining = false;
    private boolean lastThundering = false;
    private float lastRainGradient = 0.0f;
    private long lastTime = 0L;
    private String lastTimePhase = "day";

    // Death tracking
    private boolean wasDeadLastTick = false;
    private double lastDeathX = 0, lastDeathY = 0, lastDeathZ = 0;
    private String lastDeathDimension = "minecraft:overworld";
    private long lastDeathTime = 0;
    private float lastObservedHealth = Float.NaN;

    // Block tracking
    private final Map<BlockPos, BlockState> previousBlockStates = new ConcurrentHashMap<>();

    public ClientTickHandler(BaritoneAPIBridge.IPlayerContext playerContext, EventManager eventManager) {
        this.playerContext = playerContext;
        this.eventManager = eventManager;
    }

    public void onClientTick(MinecraftClient client) {
        if (playerContext.isPlayerNull() || client.world == null) return;

        // Advance any in-progress manual (non-Baritone) block break. Cheap
        // no-op when idle; only active during a dig_block request.
        com.minecraftbot.baritone.ManualMiningController.getInstance().tick(client);

        tickCounter++;

        // Audit/EMIT tick event periodically
        IBaritone baritone = BaritoneAPI.getProvider().getPrimaryBaritone();
        if (baritone != null) {
            emitTickEvent(baritone);
        }

        // Entity tracking (roughly once per second / 20 ticks)
        if (tickCounter % 20 == 0) {
            handleEntityTick(client);
        }

        // Pathfinding monitoring (roughly twice per second / 10 ticks)
        if (tickCounter % 10 == 0 && baritone != null) {
            handlePathfindingTick(baritone);
        }

        // Damage and death tracking (every tick is fine)
        trackPlayerDamage(client);
        trackPlayerDeath();

        // Weather and time change detection (every tick)
        checkWeatherChanges(client);
        checkTimeChanges(client);

        // Block update tracking (every 20 ticks)
        handleBlockUpdates(client);
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

    public void emitTickEvent(IBaritone baritone) {
        if (playerContext.isPlayerNull()) {
            return;
        }
        long now = System.currentTimeMillis();
        if (now - lastTickEventTime < TICK_EVENT_INTERVAL_MS) {
            return;
        }
        lastTickEventTime = now;

        JsonObject position = new JsonObject();
        position.addProperty("x", playerContext.getX());
        position.addProperty("y", playerContext.getY());
        position.addProperty("z", playerContext.getZ());
        
        JsonObject velocity = new JsonObject(); 
        velocity.addProperty("x", 0);
        velocity.addProperty("y", 0);
        velocity.addProperty("z", 0);

        JsonObject payload = new JsonObject();
        payload.add("position", position);
        payload.add("velocity", velocity);
        payload.addProperty("health", playerContext.getHealth());
        payload.addProperty("food", playerContext.getFoodLevel());
        payload.addProperty("dimension", playerContext.getDimension());
        // Note: mission phase is not readily available here without circular dependency on MissionController,
        // but it's emitted separately by MissionController itself.
        payload.addProperty("timestamp", now);
        try {
            payload.addProperty("is_pathing", baritone.getPathingBehavior().isPathing());
        } catch (Exception e) {
            payload.addProperty("is_pathing", false);
        }
        eventManager.publishEvent(EventManager.EventType.TICK_UPDATE, payload);
    }

    private void trackPlayerDeath() {
        if (playerContext.isPlayerNull()) return;

        boolean isDead = playerContext.getHealth() <= 0;

        if (isDead && !wasDeadLastTick) {
            // Player just died - record death location
            lastDeathX = playerContext.getX();
            lastDeathY = playerContext.getY();
            lastDeathZ = playerContext.getZ();
            lastDeathDimension = playerContext.getDimension();
            lastDeathTime = System.currentTimeMillis();

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
            respawnData.addProperty("x", playerContext.getX());
            respawnData.addProperty("y", playerContext.getY());
            respawnData.addProperty("z", playerContext.getZ());
            respawnData.addProperty("death_x", lastDeathX);
            respawnData.addProperty("death_y", lastDeathY);
            respawnData.addProperty("death_z", lastDeathZ);
            respawnData.addProperty("death_dimension", lastDeathDimension);
            eventManager.publishEvent(EventManager.EventType.RESPAWN, respawnData, EventManager.Priority.HIGH);

            LOGGER.info("Player respawned");
        }

        wasDeadLastTick = isDead;
    }

    /** Publish health loss immediately, including the best available source metadata. */
    private void trackPlayerDamage(MinecraftClient client) {
        if (client.player == null) return;
        observePlayerDamage(
            client.player,
            client.world.getRegistryKey().getValue().toString()
        );
    }

    void observePlayerDamage(net.minecraft.client.network.ClientPlayerEntity player, String dimension) {
        if (player == null) return;

        JsonObject data = new JsonObject();
        data.addProperty("x", player.getX());
        data.addProperty("y", player.getY());
        data.addProperty("z", player.getZ());
        data.addProperty("dimension", dimension);

        DamageSource source = player.getRecentDamageSource();
        if (source != null) {
            data.addProperty("damage_type", source.getName());
            Entity attacker = source.getAttacker();
            Entity directSource = source.getSource();
            if (attacker != null) {
                addDamageEntity(data, "attacker", attacker);
                addDamageDirection(data, attacker, player);
            }
            if (directSource != null && directSource != attacker) {
                addDamageEntity(data, "source", directSource);
                if (attacker == null) addDamageDirection(data, directSource, player);
            }
            data.addProperty("is_projectile", directSource != null && directSource != attacker);
        }

        observeHealth(player.getHealth(), data);
    }

    void observeHealth(float currentHealth, JsonObject data) {
        if (Float.isNaN(lastObservedHealth)) {
            lastObservedHealth = currentHealth;
            return;
        }
        if (currentHealth >= lastObservedHealth) {
            lastObservedHealth = currentHealth;
            return;
        }

        data.addProperty("amount", lastObservedHealth - currentHealth);
        data.addProperty("previous_health", lastObservedHealth);
        data.addProperty("health", currentHealth);
        eventManager.publishEvent(EventManager.EventType.DAMAGE, data, EventManager.Priority.HIGH);
        lastObservedHealth = currentHealth;
    }

    private void addDamageEntity(JsonObject data, String prefix, Entity entity) {
        data.addProperty(prefix + "_id", entity.getId());
        data.addProperty(prefix + "_uuid", entity.getUuidAsString());
        data.addProperty(prefix + "_type", Registries.ENTITY_TYPE.getId(entity.getType()).toString());
    }

    private void addDamageDirection(JsonObject data, Entity source, Entity player) {
        double dx = source.getX() - player.getX();
        double dz = source.getZ() - player.getZ();
        double length = Math.sqrt(dx * dx + dz * dz);
        if (length > 0.0001) {
            data.addProperty("direction_x", dx / length);
            data.addProperty("direction_z", dz / length);
        }
    }

    private void checkWeatherChanges(MinecraftClient client) {
        if (client.world == null) return;

        boolean currentRaining = client.world.isRaining();
        boolean currentThundering = client.world.isThundering();
        float currentRainGradient = client.world.getRainGradient(1.0f);

        boolean weatherChanged = (lastRaining != currentRaining) ||
                                (lastThundering != currentThundering) ||
                                (Math.abs(lastRainGradient - currentRainGradient) > 0.01f);

        if (weatherChanged) {
            JsonObject data = new JsonObject();
            String weatherType;
            if (currentThundering) {
                weatherType = "thunder";
            } else if (currentRaining) {
                weatherType = "rain";
            } else {
                weatherType = "clear";
            }

            data.addProperty("weather_type", weatherType);
            data.addProperty("strength", currentRainGradient);

            String previousState;
            if (lastThundering) {
                previousState = "thunder";
            } else if (lastRaining) {
                previousState = "rain";
            } else {
                previousState = "clear";
            }
            data.addProperty("previous_state", previousState);

            eventManager.publishEvent(EventManager.EventType.WEATHER_CHANGE, data, EventManager.Priority.LOW, "weather_change");

            lastRaining = currentRaining;
            lastThundering = currentThundering;
            lastRainGradient = currentRainGradient;
        }
    }

    private void checkTimeChanges(MinecraftClient client) {
        if (client.world == null) return;

        long currentTime = client.world.getTime();
        String currentPhase = (currentTime % 24000) < 12000 ? "day" : "night";
        boolean phaseChanged = !lastTimePhase.equals(currentPhase);

        if (phaseChanged || lastTime == 0) {
            JsonObject data = new JsonObject();
            data.addProperty("time_of_day", currentTime);
            data.addProperty("phase", currentPhase);
            data.addProperty("previous_phase", lastTimePhase);

            eventManager.publishEvent(EventManager.EventType.TIME_CHANGE, data, EventManager.Priority.LOW, "time_change");

            lastTimePhase = currentPhase;
        }

        lastTime = currentTime;
    }

    private void handleBlockUpdates(MinecraftClient client) {
        if (client.player == null || client.world == null) return;

        if (tickCounter % 20 != 0) return;

        BlockPos playerPos = client.player.getBlockPos();

        // Check blocks in 3x3x3 area around player for natural changes
        for (int dx = -1; dx <= 1; dx++) {
            for (int dy = -1; dy <= 1; dy++) {
                for (int dz = -1; dz <= 1; dz++) {
                    BlockPos pos = playerPos.add(dx, dy, dz);
                    BlockState currentState = client.world.getBlockState(pos);
                    BlockState previousState = previousBlockStates.get(pos);

                    if (previousState != null && !currentState.equals(previousState)) {
                        JsonObject data = new JsonObject();
                        data.addProperty("x", pos.getX());
                        data.addProperty("y", pos.getY());
                        data.addProperty("z", pos.getZ());
                        data.addProperty("old_state", Registries.BLOCK.getId(previousState.getBlock()).toString());
                        data.addProperty("new_state", Registries.BLOCK.getId(currentState.getBlock()).toString());
                        data.addProperty("dimension", client.world.getRegistryKey().getValue().toString());
                        eventManager.publishEvent(EventManager.EventType.BLOCK_UPDATE, data, EventManager.Priority.LOW, "block_update");
                    }

                    previousBlockStates.put(pos, currentState);
                }
            }
        }
    }
}
