package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.client.server.IntegratedServer;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.Mob;
import net.minecraft.world.entity.projectile.Projectile;
import net.minecraft.world.phys.AABB;

/**
 * Command handler for state queries: get_state, get_entities, and combat snapshots.
 */
public class StateCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "state";
    }

    @Override
    protected CommandResult execute(JsonObject params, Minecraft client, IBaritone baritone,
            Socket clientSocket) {
        // Determine action from explicit 'action' param or check for entity-specific
        // params
        String action = "get"; // default

        if (params.has("action")) {
            action = params.get("action").getAsString();
        } else if (params.has("radius") && !params.has("position")) {
            // If 'radius' is specified but not 'position', it's likely get_entities
            action = "entities";
        }

        switch (action) {
            case "get":
                return handleGetState(client, baritone);
            case "entities":
                return handleGetEntities(client, params);
            case "combat_snapshot":
                return handleCombatSnapshot(client, baritone, params);
            default:
                return CommandResult.error("Unknown state action: " + action);
        }
    }

    private CommandResult handleGetState(Minecraft client, IBaritone baritone) {
        // Read most state directly - these are volatile or immutable and safe to read
        // off-thread
        try {
            if (client.player == null) {
                return CommandResult.error("Player not available");
            }

            if (client.level == null) {
                return CommandResult.error("World not available");
            }

            LocalPlayer player = client.player;

            // Position (volatile double fields, safe to read)
            JsonObject position = new JsonObject();
            position.addProperty("x", player.getX());
            position.addProperty("y", player.getY());
            position.addProperty("z", player.getZ());
            position.addProperty("yaw", player.getYRot());
            position.addProperty("pitch", player.getXRot());

            // Block position (BlockPos is computed from position)
            var blockPos = player.blockPosition();
            JsonObject blockPosition = new JsonObject();
            blockPosition.addProperty("x", blockPos.getX());
            blockPosition.addProperty("y", blockPos.getY());
            blockPosition.addProperty("z", blockPos.getZ());

            // Health and status (float/int fields, volatile enough for our purposes)
            JsonObject data = new JsonObject();
            data.add("position", position);
            data.add("block_position", blockPosition);
            data.addProperty("health", player.getHealth());
            data.addProperty("max_health", player.getMaxHealth());
            data.addProperty("food_level", player.getFoodData().getFoodLevel());
            data.addProperty("saturation", player.getFoodData().getSaturationLevel());
            data.addProperty("experience_level", player.experienceLevel);
            data.addProperty("experience_total", player.totalExperience);
            data.addProperty("is_dead", player.isDeadOrDying());
            data.addProperty("entity_id", player.getId());
            data.addProperty("entity_uuid", player.getStringUUID());

            // Player Flags
            data.addProperty("is_sprinting", player.isSprinting());
            data.addProperty("is_sneaking", player.isShiftKeyDown());
            data.addProperty("is_on_ground", player.onGround());
            data.addProperty("armor_points", player.getArmorValue());
            int armorCount = 0;
            for (int slot = 36; slot < 40; slot++) {
                if (!player.getInventory().getItem(slot).isEmpty()) armorCount++;
            }
            data.addProperty("armor_count", armorCount);
            data.addProperty("main_hand", BuiltInRegistries.ITEM.getKey(player.getMainHandItem().getItem()).toString());
            data.addProperty("off_hand", BuiltInRegistries.ITEM.getKey(player.getOffhandItem().getItem()).toString());
            data.addProperty("is_using_item", player.isUsingItem());
            data.addProperty("is_blocking", player.isBlocking());
            data.addProperty("attack_cooldown", player.getAttackStrengthScale(0.0f));

            // Active Effects
            JsonArray effects = new JsonArray();
            player.getActiveEffects().forEach(effect -> {
                JsonObject eff = new JsonObject();
                // Fix: Handle RegistryEntry if needed, or check mappings.
                // In 1.21, getEffectType() returns RegistryEntry<StatusEffect>.
                // We need to call .value() to get the StatusEffect, or use getId() on the
                // entry.
                eff.addProperty("id", BuiltInRegistries.MOB_EFFECT.getKey(effect.getEffect().value()).toString());
                eff.addProperty("duration", effect.getDuration());
                eff.addProperty("amplifier", effect.getAmplifier());
                effects.add(eff);
            });
            data.add("effects", effects);

            // Velocity
            JsonObject velocity = new JsonObject();
            velocity.addProperty("x", player.getDeltaMovement().x);
            velocity.addProperty("y", player.getDeltaMovement().y);
            velocity.addProperty("z", player.getDeltaMovement().z);
            data.add("velocity", velocity);

            // Baritone status - wrap in try/catch as it could throw
            try {
                boolean isPathing = baritone.getPathingBehavior().isPathing();
                data.addProperty("is_pathing", isPathing);

                if (isPathing && baritone.getPathingBehavior().getGoal() != null) {
                    data.addProperty("pathing_goal", true);
                }
            } catch (Exception e) {
                logger.debug("Could not get pathing status", e);
                data.addProperty("is_pathing", false);
            }

            // World info (dimension key is immutable, time is volatile)
            data.addProperty("dimension", client.level.dimension().identifier().toString());
            data.addProperty("difficulty", client.level.getDifficulty().getSerializedName());
            data.addProperty("world_time", client.level.getOverworldClockTime());
            String biomeId = client.level.getBiome(blockPos).unwrapKey()
                .map(key -> key.identifier().toString())
                .orElse("unknown");
            data.addProperty("biome", biomeId);

            JsonObject worldIdentity = new JsonObject();
            worldIdentity.addProperty("version", 1);
            worldIdentity.addProperty("dimension_context", client.level.dimension().identifier().toString());

            // Add world seed for reset detection (only available on integrated server)
            if (client.getSingleplayerServer() != null) {
                IntegratedServer server = client.getSingleplayerServer();
                ServerLevel world = server.overworld();
                if (world != null) {
                    data.addProperty("world_seed", world.getSeed());
                    worldIdentity.addProperty("seed", world.getSeed());
                }
                worldIdentity.addProperty("world_name", server.getWorldData().getLevelName());
                worldIdentity.addProperty("scope", "singleplayer");
            } else if (client.getCurrentServer() != null) {
                worldIdentity.addProperty("server_address", client.getCurrentServer().ip);
                worldIdentity.addProperty("scope", "multiplayer");
            }
            data.add("world_identity", worldIdentity);

            // Screen info - may be slightly stale but acceptable
            if (client.gui.screen() != null) {
                data.addProperty("screen", client.gui.screen().getClass().getSimpleName());
                data.addProperty("has_gui", true);
            } else {
                data.addProperty("screen", "none");
                data.addProperty("has_gui", false);
            }

            return CommandResult.success(data);
        } catch (Exception e) {
            return CommandResult.error("Failed to get state: " + e.getMessage());
        }
    }

    private CommandResult handleGetEntities(Minecraft client, JsonObject params) {
        CountDownLatch latch = new CountDownLatch(1);
        AtomicReference<JsonObject> dataRef = new AtomicReference<>();
        AtomicReference<String> errorRef = new AtomicReference<>();

        int radius = params.has("radius") ? params.get("radius").getAsInt() : 64;

        client.execute(() -> {
            try {
                if (client.level == null || client.player == null) {
                    errorRef.set("World or player not available");
                    return;
                }

                LocalPlayer player = client.player;
                AABB box = new AABB(
                        player.getX() - radius, player.getY() - radius, player.getZ() - radius,
                        player.getX() + radius, player.getY() + radius, player.getZ() + radius);

                var entities = client.level.getEntities(null, box);
                JsonArray entityList = new JsonArray();
                JsonArray serializationErrors = new JsonArray();
                int skippedEntities = 0;

                for (Entity entity : entities) {
                    if (entity.distanceTo(player) > radius)
                        continue;

                    try {
                        JsonObject entityData = serializeEntity(entity, player);
                        entityList.add(entityData);
                    } catch (Exception e) {
                        skippedEntities++;
                        JsonObject error = new JsonObject();
                        error.addProperty("id", entity.getId());
                        error.addProperty("type", safeEntityType(entity));
                        error.addProperty("error", e.getClass().getSimpleName() + ": " + e.getMessage());
                        serializationErrors.add(error);
                        logger.warn("Skipping entity {} during state serialization", entity.getId(), e);
                    }
                }

                JsonObject data = new JsonObject();
                data.add("entities", entityList);
                data.addProperty("count", entityList.size());
                data.addProperty("radius", radius);
                data.addProperty("skipped_count", skippedEntities);
                if (!serializationErrors.isEmpty()) {
                    data.add("serialization_errors", serializationErrors);
                }
                dataRef.set(data);
            } catch (Exception e) {
                errorRef.set("Failed to get entities: " + e.getMessage());
            } finally {
                latch.countDown();
            }
        });

        try {
            if (!latch.await(5, TimeUnit.SECONDS)) {
                return CommandResult.error("Timeout waiting for entity data");
            }
        } catch (InterruptedException e) {
            return CommandResult.error("Interrupted while waiting for entity data");
        }

        if (errorRef.get() != null) {
            return CommandResult.error(errorRef.get());
        }

        return CommandResult.success(dataRef.get());
    }

    /**
     * Capture player readiness and nearby entities in one client-thread task so
     * combat policy never combines observations from different game ticks.
     */
    private CommandResult handleCombatSnapshot(Minecraft client, IBaritone baritone, JsonObject params) {
        CountDownLatch latch = new CountDownLatch(1);
        AtomicReference<CommandResult> resultRef = new AtomicReference<>();
        int radius = Math.max(1, Math.min(64, params.has("radius") ? params.get("radius").getAsInt() : 16));

        client.execute(() -> {
            try {
                if (client.level == null || client.player == null) {
                    resultRef.set(CommandResult.error("World or player not available"));
                    return;
                }

                CommandResult stateResult = handleGetState(client, baritone);
                if (!stateResult.isSuccess()) {
                    resultRef.set(stateResult);
                    return;
                }

                LocalPlayer player = client.player;
                AABB box = player.getBoundingBox().inflate(radius);
                JsonArray entityList = new JsonArray();
                JsonArray serializationErrors = new JsonArray();
                int skippedEntities = 0;
                for (Entity entity : client.level.getEntities(player, box)) {
                    if (entity.distanceTo(player) > radius) continue;
                    try {
                        entityList.add(serializeEntity(entity, player));
                    } catch (Exception e) {
                        skippedEntities++;
                        JsonObject error = new JsonObject();
                        error.addProperty("id", entity.getId());
                        error.addProperty("type", safeEntityType(entity));
                        error.addProperty("error", e.getClass().getSimpleName() + ": " + e.getMessage());
                        serializationErrors.add(error);
                    }
                }

                JsonObject data = new JsonObject();
                data.addProperty("snapshot_version", 1);
                data.addProperty("tick", client.level.getGameTime());
                data.addProperty("radius", radius);
                data.add("player", stateResult.getData());
                data.add("entities", entityList);
                data.addProperty("count", entityList.size());
                data.addProperty("skipped_count", skippedEntities);
                if (!serializationErrors.isEmpty()) data.add("serialization_errors", serializationErrors);
                resultRef.set(CommandResult.success(data));
            } catch (Exception e) {
                resultRef.set(CommandResult.error("Failed to get combat snapshot: " + e.getMessage()));
            } finally {
                latch.countDown();
            }
        });

        try {
            if (!latch.await(5, TimeUnit.SECONDS)) {
                return CommandResult.error("Timeout waiting for combat snapshot");
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return CommandResult.error("Interrupted while waiting for combat snapshot");
        }
        return resultRef.get() != null ? resultRef.get() : CommandResult.error("Combat snapshot unavailable");
    }

    private JsonObject serializeEntity(Entity entity, LocalPlayer player) {
        JsonObject entityData = new JsonObject();
        entityData.addProperty("id", entity.getId());
        entityData.addProperty("uuid", entity.getStringUUID());
        entityData.addProperty("type", BuiltInRegistries.ENTITY_TYPE.getKey(entity.getType()).toString());
        entityData.addProperty("name", entity.getDisplayName().getString());
        entityData.addProperty("distance", entity.distanceTo(player));

        JsonObject entityVelocity = new JsonObject();
        entityVelocity.addProperty("x", entity.getDeltaMovement().x);
        entityVelocity.addProperty("y", entity.getDeltaMovement().y);
        entityVelocity.addProperty("z", entity.getDeltaMovement().z);
        entityData.add("velocity", entityVelocity);

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
            entityData.addProperty("age", living.isBaby() ? -1 : 0);
            entityData.addProperty("is_baby", living.isBaby());
        }

        if (entity instanceof Mob mob) {
            Entity target = mob.getTarget();
            if (target != null) {
                entityData.addProperty("target_id", target.getId());
                entityData.addProperty("target_uuid", target.getStringUUID());
                entityData.addProperty("target_type", safeEntityType(target));
            }
            entityData.addProperty("is_aggressive", target == player);
            entityData.addProperty("can_see_player", mob.hasLineOfSight(player));
        }

        if (entity instanceof Projectile projectile && projectile.getOwner() != null) {
            Entity owner = projectile.getOwner();
            entityData.addProperty("owner_id", owner.getId());
            entityData.addProperty("owner_type", safeEntityType(owner));
        }

        if (entity instanceof net.minecraft.world.entity.TamableAnimal tameable) {
            entityData.addProperty("is_tamed", tameable.isTame());
            if (tameable.getOwner() != null) {
                entityData.addProperty("owner_uuid", tameable.getOwner().getUUID().toString());
            }
        }

        if (entity instanceof net.minecraft.world.entity.npc.villager.Villager villager) {
            var villagerData = villager.getVillagerData();
            String profession = villagerData.profession().unwrapKey()
                .map(key -> key.identifier().toString())
                .orElse("unknown");
            entityData.addProperty("profession", profession);
            entityData.addProperty("level", villagerData.level());
            entityData.addProperty("offers_count", villager.getOffers().size());
        }

        if (entity instanceof net.minecraft.world.entity.projectile.FishingHook bobber) {
            boolean hasCatch = bobber.getHookedIn() != null || bobber.isOpenWaterFishing();
            entityData.addProperty("has_catch", hasCatch);
        }
        return entityData;
    }

    private String safeEntityType(Entity entity) {
        try {
            return BuiltInRegistries.ENTITY_TYPE.getKey(entity.getType()).toString();
        } catch (Exception ignored) {
            return "unknown";
        }
    }
}
