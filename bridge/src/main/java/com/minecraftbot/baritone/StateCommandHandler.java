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
import net.minecraft.world.World;

import java.net.Socket;

/**
 * Command handler for state queries: get_state, get_entities.
 */
public class StateCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "state";
    }

    @Override
    protected CommandResult execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        String action = params.has("action") ? params.get("action").getAsString() : "get";

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
        try {
            if (client.player == null) {
                return CommandResult.error("Player not available");
            }

            ClientPlayerEntity player = client.player;

            // Position (Primitives are volatile/safe)
            JsonObject position = new JsonObject();
            position.addProperty("x", player.getX());
            position.addProperty("y", player.getY());
            position.addProperty("z", player.getZ());
            position.addProperty("yaw", player.getYaw());
            position.addProperty("pitch", player.getPitch());

            // Block position (BlockPos is immutable struct)
            var blockPos = player.getBlockPos();
            JsonObject blockPosition = new JsonObject();
            blockPosition.addProperty("x", blockPos.getX());
            blockPosition.addProperty("y", blockPos.getY());
            blockPosition.addProperty("z", blockPos.getZ());

            // Health and status (Primitives)
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

            // Baritone status
            try {
                boolean isPathing = baritone.getPathingBehavior().isPathing();
                data.addProperty("is_pathing", isPathing);

                if (isPathing) {
                    data.addProperty("pathing_goal", baritone.getPathingBehavior().getGoal() != null);
                }
            } catch (Exception e) {
                logger.debug("Could not get pathing status", e);
                data.addProperty("is_pathing", false);
            }

            // World info
            if (client.world != null) {
                data.addProperty("dimension", client.world.getRegistryKey().getValue().toString());
            }

            return CommandResult.success(data);
        } catch (Exception e) {
            return CommandResult.error("Failed to get state: " + e.getMessage());
        }
    }

    private CommandResult handleGetEntities(MinecraftClient client, JsonObject params) {
        if (client.world == null || client.player == null) {
            return CommandResult.error("World or player not available");
        }

        int radius = params.has("radius") ? params.get("radius").getAsInt() : 64;
        ClientPlayerEntity player = client.player;
        Box box = new Box(
            player.getX() - radius, player.getY() - radius, player.getZ() - radius,
            player.getX() + radius, player.getY() + radius, player.getZ() + radius
        );

        var entities = client.world.getOtherEntities(null, box);
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

        JsonObject data = new JsonObject();
        data.add("entities", entityList);
        data.addProperty("count", entityList.size());
        data.addProperty("radius", radius);

        return CommandResult.success(data);
    }
}