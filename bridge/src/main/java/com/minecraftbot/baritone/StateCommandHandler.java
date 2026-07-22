package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.entity.Entity;
import net.minecraft.entity.LivingEntity;
import net.minecraft.registry.Registries;
import net.minecraft.util.math.Box;
import net.minecraft.server.integrated.IntegratedServer;
import net.minecraft.server.world.ServerWorld;
import net.minecraft.world.World;

import java.net.Socket;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Command handler for state queries: get_state, get_entities.
 */
public class StateCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "state";
    }

    @Override
    protected CommandResult execute(JsonObject params, MinecraftClient client, IBaritone baritone,
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
            default:
                return CommandResult.error("Unknown state action: " + action);
        }
    }

    private CommandResult handleGetState(MinecraftClient client, IBaritone baritone) {
        // Read most state directly - these are volatile or immutable and safe to read
        // off-thread
        try {
            if (client.player == null) {
                return CommandResult.error("Player not available");
            }

            if (client.world == null) {
                return CommandResult.error("World not available");
            }

            ClientPlayerEntity player = client.player;

            // Position (volatile double fields, safe to read)
            JsonObject position = new JsonObject();
            position.addProperty("x", player.getX());
            position.addProperty("y", player.getY());
            position.addProperty("z", player.getZ());
            position.addProperty("yaw", player.getYaw());
            position.addProperty("pitch", player.getPitch());

            // Block position (BlockPos is computed from position)
            var blockPos = player.getBlockPos();
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
            data.addProperty("food_level", player.getHungerManager().getFoodLevel());
            data.addProperty("saturation", player.getHungerManager().getSaturationLevel());
            data.addProperty("experience_level", player.experienceLevel);
            data.addProperty("experience_total", player.totalExperience);
            data.addProperty("is_dead", player.isDead());

            // Player Flags
            data.addProperty("is_sprinting", player.isSprinting());
            data.addProperty("is_sneaking", player.isSneaking());
            data.addProperty("is_on_ground", player.isOnGround());

            // Active Effects
            JsonArray effects = new JsonArray();
            player.getStatusEffects().forEach(effect -> {
                JsonObject eff = new JsonObject();
                // Fix: Handle RegistryEntry if needed, or check mappings.
                // In 1.21, getEffectType() returns RegistryEntry<StatusEffect>.
                // We need to call .value() to get the StatusEffect, or use getId() on the
                // entry.
                eff.addProperty("id", Registries.STATUS_EFFECT.getId(effect.getEffectType().value()).toString());
                eff.addProperty("duration", effect.getDuration());
                eff.addProperty("amplifier", effect.getAmplifier());
                effects.add(eff);
            });
            data.add("effects", effects);

            // Velocity
            JsonObject velocity = new JsonObject();
            velocity.addProperty("x", player.getVelocity().x);
            velocity.addProperty("y", player.getVelocity().y);
            velocity.addProperty("z", player.getVelocity().z);
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
            data.addProperty("dimension", client.world.getRegistryKey().getValue().toString());
            data.addProperty("world_time", client.world.getTimeOfDay());
            String biomeId = client.world.getBiome(blockPos).getKey()
                .map(key -> key.getValue().toString())
                .orElse("unknown");
            data.addProperty("biome", biomeId);

            JsonObject worldIdentity = new JsonObject();
            worldIdentity.addProperty("version", 1);
            worldIdentity.addProperty("dimension_context", client.world.getRegistryKey().getValue().toString());

            // Add world seed for reset detection (only available on integrated server)
            if (client.getServer() != null) {
                IntegratedServer server = client.getServer();
                ServerWorld world = server.getOverworld();
                if (world != null) {
                    data.addProperty("world_seed", world.getSeed());
                    worldIdentity.addProperty("seed", world.getSeed());
                }
                worldIdentity.addProperty("world_name", server.getSaveProperties().getLevelName());
                worldIdentity.addProperty("scope", "singleplayer");
            } else if (client.getCurrentServerEntry() != null) {
                worldIdentity.addProperty("server_address", client.getCurrentServerEntry().address);
                worldIdentity.addProperty("scope", "multiplayer");
            }
            data.add("world_identity", worldIdentity);

            // Screen info - may be slightly stale but acceptable
            if (client.currentScreen != null) {
                data.addProperty("screen", client.currentScreen.getClass().getSimpleName());
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

    private CommandResult handleGetEntities(MinecraftClient client, JsonObject params) {
        CountDownLatch latch = new CountDownLatch(1);
        AtomicReference<JsonObject> dataRef = new AtomicReference<>();
        AtomicReference<String> errorRef = new AtomicReference<>();

        int radius = params.has("radius") ? params.get("radius").getAsInt() : 64;

        client.execute(() -> {
            try {
                if (client.world == null || client.player == null) {
                    errorRef.set("World or player not available");
                    return;
                }

                ClientPlayerEntity player = client.player;
                Box box = new Box(
                        player.getX() - radius, player.getY() - radius, player.getZ() - radius,
                        player.getX() + radius, player.getY() + radius, player.getZ() + radius);

                var entities = client.world.getOtherEntities(null, box);
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

    private JsonObject serializeEntity(Entity entity, ClientPlayerEntity player) {
        JsonObject entityData = new JsonObject();
        entityData.addProperty("id", entity.getId());
        entityData.addProperty("uuid", entity.getUuidAsString());
        entityData.addProperty("type", Registries.ENTITY_TYPE.getId(entity.getType()).toString());
        entityData.addProperty("name", entity.getDisplayName().getString());
        entityData.addProperty("distance", entity.distanceTo(player));

        JsonObject entityVelocity = new JsonObject();
        entityVelocity.addProperty("x", entity.getVelocity().x);
        entityVelocity.addProperty("y", entity.getVelocity().y);
        entityVelocity.addProperty("z", entity.getVelocity().z);
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

        if (entity instanceof net.minecraft.entity.passive.TameableEntity tameable) {
            entityData.addProperty("is_tamed", tameable.isTamed());
            if (tameable.getOwner() != null) {
                entityData.addProperty("owner_uuid", tameable.getOwner().getUuid().toString());
            }
        }

        if (entity instanceof net.minecraft.entity.passive.VillagerEntity villager) {
            var villagerData = villager.getVillagerData();
            String profession = villagerData.profession().getKey()
                .map(key -> key.getValue().toString())
                .orElse("unknown");
            entityData.addProperty("profession", profession);
            entityData.addProperty("level", villagerData.level());
            entityData.addProperty("offers_count", villager.getOffers().size());
        }

        if (entity instanceof net.minecraft.entity.projectile.FishingBobberEntity bobber) {
            boolean hasCatch = bobber.getHookedEntity() != null || bobber.isInOpenWater();
            entityData.addProperty("has_catch", hasCatch);
        }
        return entityData;
    }

    private String safeEntityType(Entity entity) {
        try {
            return Registries.ENTITY_TYPE.getId(entity.getType()).toString();
        } catch (Exception ignored) {
            return "unknown";
        }
    }
}
