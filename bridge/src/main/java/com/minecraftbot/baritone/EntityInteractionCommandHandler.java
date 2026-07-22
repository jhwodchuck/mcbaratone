package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.entity.Entity;
import net.minecraft.entity.passive.AnimalEntity;
import net.minecraft.entity.passive.CowEntity;
import net.minecraft.entity.passive.MooshroomEntity;
import net.minecraft.entity.passive.SheepEntity;
import net.minecraft.entity.passive.TameableEntity;
import net.minecraft.entity.passive.VillagerEntity;
import net.minecraft.registry.Registries;
import net.minecraft.util.ActionResult;
import net.minecraft.util.Hand;
import net.minecraft.util.math.MathHelper;
import net.minecraft.util.math.Vec3d;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.Socket;
import java.util.concurrent.CompletableFuture;

/**
 * Advanced entity interaction system supporting multiple interaction types with
 * safety checks.
 * Supports attack, tame, trade, look_at actions with distance validation and
 * interaction type detection.
 */
public class EntityInteractionCommandHandler extends AsyncCommandHandler {

    private static final Logger LOGGER = LoggerFactory.getLogger(EntityInteractionCommandHandler.class);

    public enum InteractionType {
        ATTACK,
        TAME,
        TRADE,
        LOOK_AT,
        SHEAR,
        MILK,
        INTERACT
    }

    private static class InteractionValidation {
        boolean valid = true;
        String error = "";
        double distance = 0.0;
        InteractionType detectedType;

        static InteractionValidation success(double distance, InteractionType type) {
            InteractionValidation v = new InteractionValidation();
            v.distance = distance;
            v.detectedType = type;
            return v;
        }

        static InteractionValidation error(String message) {
            InteractionValidation v = new InteractionValidation();
            v.valid = false;
            v.error = message;
            return v;
        }
    }

    @Override
    public String getCommandName() {
        return "entity_interact";
    }

    @Override
    public CompletableFuture<CommandResult> execute(JsonObject params, MinecraftClient client, IBaritone baritone,
            Socket clientSocket) {
        String action = params.has("action") ? params.get("action").getAsString() : "look_at";

        switch (action) {
            case "feed":
            case "interact":
                return handleDirectEntityInteraction(params, client, action);
            case "tame":
            case "shear":
            case "milk":
                return handleEntityInteraction(params, client, action);
            case "look_at":
                return handleLookAtEntity(params, client);
            case "detect":
                return handleDetectInteractions(params, client);
            default:
                return CompletableFuture.completedFuture(CommandResult.error("Unknown interaction action: " + action));
        }
    }

    private CompletableFuture<CommandResult> handleEntityInteraction(JsonObject params, MinecraftClient client,
            String action) {
        return executeOnMainThread(client, () -> {
            if (client.player == null || client.world == null) {
                return CommandResult.error("Player or world not available");
            }

            if (!params.has("entity_id") && !params.has("entity_type")) {
                return CommandResult.error("Missing required parameter: entity_id or entity_type");
            }

            double maxDistance = params.has("max_distance") ? params.get("max_distance").getAsDouble() : 5.0;
            Entity target;
            String targetDescription;
            if (params.has("entity_id")) {
                int entityId = params.get("entity_id").getAsInt();
                target = client.world.getEntityById(entityId);
                targetDescription = "entity id " + entityId;
            } else {
                String entityType = params.get("entity_type").getAsString();
                target = findNearestEntityOfType(client, entityType, maxDistance);
                targetDescription = entityType;
            }

            if (target == null) {
                return CommandResult.error(
                    "No valid " + targetDescription + " found within " + maxDistance + " blocks");
            }

            // Validate interaction
            InteractionValidation validation = validateInteraction(client, target, action);
            if (!validation.valid) {
                return CommandResult.error(validation.error);
            }

            // Check inventory requirements
            String inventoryError = checkInventoryRequirements(client, action);
            if (inventoryError != null) {
                return CommandResult.error(inventoryError);
            }

            // Perform the interaction
            boolean success = performInteraction(client, target, validation.detectedType);

            JsonObject data = new JsonObject();
            data.addProperty("success", success);
            data.addProperty("entity_id", target.getId());
            data.addProperty("entity_type", Registries.ENTITY_TYPE.getId(target.getType()).toString());
            data.addProperty("action", action);
            data.addProperty("distance", validation.distance);
            data.addProperty("interaction_type", validation.detectedType.name());

            return CommandResult.success(data);
        });
    }

    /**
     * Right-click a specific entity with the currently selected main-hand
     * item. Unlike crosshair-driven use_item, the entity id remains the target
     * even if it moves between sensing and interaction.
     */
    private CompletableFuture<CommandResult> handleDirectEntityInteraction(
            JsonObject params, MinecraftClient client, String action) {
        return executeOnMainThread(client, () -> {
            if (client.player == null || client.world == null
                    || client.interactionManager == null) {
                return CommandResult.error("Player, world, or interaction manager not available");
            }
            if (!params.has("entity_id")) {
                return CommandResult.error("Missing required parameter: entity_id");
            }

            int entityId = params.get("entity_id").getAsInt();
            Entity target = client.world.getEntityById(entityId);
            if (target == null) {
                return CommandResult.error("Target entity not found: " + entityId);
            }
            if ("feed".equals(action) && !(target instanceof AnimalEntity)) {
                return CommandResult.error("Feed target is not an animal: " + entityId);
            }

            double maxDistance = params.has("max_distance")
                ? params.get("max_distance").getAsDouble() : 6.0;
            double distance = distanceToPlayer(client, target);
            if (distance > maxDistance) {
                return CommandResult.error(
                    "Entity too far: " + String.format("%.2f", distance)
                        + " blocks (max " + String.format("%.2f", maxDistance) + ")");
            }

            String heldItem = Registries.ITEM.getId(
                client.player.getMainHandStack().getItem()).toString();
            int heldCountBefore = client.player.getMainHandStack().getCount();
            ActionResult interactionResult = client.interactionManager.interactEntity(
                client.player, target, Hand.MAIN_HAND);
            if (interactionResult.isAccepted()) {
                client.player.swingHand(Hand.MAIN_HAND);
            }

            JsonObject data = new JsonObject();
            data.addProperty("success", interactionResult.isAccepted());
            data.addProperty("accepted", interactionResult.isAccepted());
            data.addProperty("result", interactionResult.toString());
            data.addProperty("action", action);
            data.addProperty("entity_id", target.getId());
            data.addProperty("entity_type",
                Registries.ENTITY_TYPE.getId(target.getType()).toString());
            data.addProperty("distance", distance);
            data.addProperty("hand", "main_hand");
            data.addProperty("held_item", heldItem);
            data.addProperty("held_count_before", heldCountBefore);
            data.addProperty("held_count_after", client.player.getMainHandStack().getCount());
            return CommandResult.success(data);
        });
    }

    private CompletableFuture<CommandResult> handleLookAtEntity(JsonObject params, MinecraftClient client) {
        return executeOnMainThread(client, () -> {
            if (client.player == null) {
                return CommandResult.error("Player not available");
            }

            Entity target = null;
            if (params.has("entity_id")) {
                int entityId = params.get("entity_id").getAsInt();
                target = client.world.getEntityById(entityId);
            } else if (params.has("entity_type")) {
                // Find nearest entity of type
                String entityType = params.get("entity_type").getAsString();
                target = findNearestEntityOfType(client, entityType, 16.0); // Use larger range for look_at
            }

            if (target == null) {
                return CommandResult.error("Target entity not found");
            }

            // Look at the entity
            lookAtEntity(client, target);

            JsonObject data = new JsonObject();
            data.addProperty("target_entity_id", target.getId());
            data.addProperty("entity_type", Registries.ENTITY_TYPE.getId(target.getType()).toString());
            Vec3d pos = new Vec3d(target.getX(), target.getY(), target.getZ());
            data.addProperty("target_x", pos.x);
            data.addProperty("target_y", pos.y);
            data.addProperty("target_z", pos.z);

            return CommandResult.success(data);
        });
    }

    private CompletableFuture<CommandResult> handleDetectInteractions(JsonObject params, MinecraftClient client) {
        return executeOnMainThread(client, () -> {
            if (client.player == null || client.world == null) {
                return CommandResult.error("Player or world not available");
            }

            double maxDistance = params.has("max_distance") ? params.get("max_distance").getAsDouble() : 16.0;

            JsonArray interactions = new JsonArray();
            for (Entity entity : client.world.getEntities()) {
                double distance = new Vec3d(client.player.getX(), client.player.getY(), client.player.getZ())
                        .distanceTo(new Vec3d(entity.getX(), entity.getY(), entity.getZ()));
                if (distance <= maxDistance) {
                    for (InteractionType type : InteractionType.values()) {
                        InteractionValidation validation = validateInteraction(client, entity,
                                type.name().toLowerCase());
                        if (validation.valid) {
                            JsonObject interaction = new JsonObject();
                            interaction.addProperty("entity_id", entity.getId());
                            interaction.addProperty("entity_type",
                                    Registries.ENTITY_TYPE.getId(entity.getType()).toString());
                            interaction.addProperty("interaction_type", type.name());
                            interaction.addProperty("distance", distance);
                            interactions.add(interaction);
                        }
                    }
                }
            }

            JsonObject data = new JsonObject();
            data.add("available_interactions", interactions);
            return CommandResult.success(data);
        });
    }

    private String checkInventoryRequirements(MinecraftClient client, String action) {
        if (client.player == null || client.player.getInventory() == null) {
            return "Player inventory not available";
        }

        switch (action) {
            case "shear":
                // Check for shears
                for (int i = 0; i < client.player.getInventory().size(); i++) {
                    var item = client.player.getInventory().getStack(i);
                    if (!item.isEmpty()
                            && Registries.ITEM.getId(item.getItem()).toString().equals("minecraft:shears")) {
                        return null; // Found shears
                    }
                }
                return "Shears required in inventory to shear sheep";
            case "milk":
                // Check for empty bucket
                for (int i = 0; i < client.player.getInventory().size(); i++) {
                    var item = client.player.getInventory().getStack(i);
                    if (!item.isEmpty()
                            && Registries.ITEM.getId(item.getItem()).toString().equals("minecraft:bucket")) {
                        return null; // Found bucket
                    }
                }
                return "Empty bucket required in inventory to milk cows";
            case "tame":
                // No special items required for taming
                return null;
            default:
                return "Unknown action for inventory check";
        }
    }

    private InteractionValidation validateInteraction(MinecraftClient client, Entity target, String action) {
        Vec3d playerPos = new Vec3d(client.player.getX(), client.player.getY(), client.player.getZ());
        Vec3d targetPos = new Vec3d(target.getX(), target.getY(), target.getZ());
        double distance = playerPos.distanceTo(targetPos);

        // Distance check (Minecraft interaction range)
        if (distance > 6.0) {
            return InteractionValidation.error("Entity too far: " + String.format("%.2f", distance) + " blocks");
        }

        // Action-specific validation
        try {
            InteractionType type = InteractionType.valueOf(action.toUpperCase());

            switch (type) {
                case ATTACK:
                    // Any entity can be attacked
                    return InteractionValidation.success(distance, InteractionType.ATTACK);

                case TAME:
                    if (!(target instanceof TameableEntity)) {
                        return InteractionValidation.error(
                                "Entity cannot be tamed: " + Registries.ENTITY_TYPE.getId(target.getType()).toString());
                    }
                    TameableEntity tameable = (TameableEntity) target;
                    if (tameable.isTamed()) {
                        return InteractionValidation.error("Entity is already tamed");
                    }
                    String entityTypeId = Registries.ENTITY_TYPE.getId(target.getType()).toString();
                    if (!entityTypeId.equals("minecraft:wolf") &&
                            !entityTypeId.equals("minecraft:cat") &&
                            !entityTypeId.equals("minecraft:parrot")) {
                        return InteractionValidation.error("Only wolves, cats, and parrots can be tamed");
                    }
                    return InteractionValidation.success(distance, InteractionType.TAME);

                case SHEAR:
                    if (!(target instanceof SheepEntity)) {
                        return InteractionValidation.error("Entity cannot be sheared: "
                                + Registries.ENTITY_TYPE.getId(target.getType()).toString());
                    }
                    SheepEntity sheep = (SheepEntity) target;
                    if (sheep.isSheared()) {
                        return InteractionValidation.error("Sheep is already sheared");
                    }
                    return InteractionValidation.success(distance, InteractionType.SHEAR);

                case MILK:
                    if (!(target instanceof CowEntity) && !(target instanceof MooshroomEntity)) {
                        return InteractionValidation.error("Entity cannot be milked: "
                                + Registries.ENTITY_TYPE.getId(target.getType()).toString());
                    }
                    return InteractionValidation.success(distance, InteractionType.MILK);

                case LOOK_AT:
                    return InteractionValidation.success(distance, InteractionType.LOOK_AT);

                case INTERACT:
                    return InteractionValidation.success(distance, InteractionType.INTERACT);
            }
        } catch (IllegalArgumentException e) {
            return InteractionValidation.error("Unknown interaction type: " + action);
        }

        return InteractionValidation.error("Unsupported interaction: " + action);
    }

    private boolean performInteraction(MinecraftClient client, Entity target, InteractionType type) {
        try {
            switch (type) {
                case ATTACK:
                    client.interactionManager.attackEntity(client.player, target);
                    return true;

                case TAME:
                    if (target instanceof AnimalEntity) {
                        // Attempt to tame by interacting
                        client.interactionManager.interactEntity(client.player, target,
                                net.minecraft.util.Hand.MAIN_HAND);
                        return true;
                    }
                    break;

                case TRADE:
                    if (target instanceof VillagerEntity) {
                        client.interactionManager.interactEntity(client.player, target,
                                net.minecraft.util.Hand.MAIN_HAND);
                        return true;
                    }
                    break;

                case SHEAR:
                case MILK:
                    // Use item on entity
                    client.interactionManager.interactEntity(client.player, target, net.minecraft.util.Hand.MAIN_HAND);
                    return true;

                case LOOK_AT:
                    lookAtEntity(client, target);
                    return true;

                case INTERACT:
                    return client.interactionManager.interactEntity(
                        client.player, target, Hand.MAIN_HAND).isAccepted();
            }
        } catch (Exception e) {
            LOGGER.error("Interaction failed", e);
        }
        return false;
    }

    private void lookAtEntity(MinecraftClient client, Entity target) {
        Vec3d targetPos = new Vec3d(target.getX(), target.getY(), target.getZ());
        double dx = targetPos.x - client.player.getX();
        double dy = (targetPos.y + target.getHeight() / 2)
                - (client.player.getY() + client.player.getEyeHeight(client.player.getPose()));
        double dz = targetPos.z - client.player.getZ();

        double dist = Math.sqrt(dx * dx + dz * dz);
        float yaw = (float) (MathHelper.atan2(dz, dx) * (180.0 / Math.PI)) - 90.0f;
        float pitch = (float) -(MathHelper.atan2(dy, dist) * (180.0 / Math.PI));

        client.player.setYaw(yaw);
        client.player.setPitch(pitch);
    }

    private Entity findNearestEntityOfType(MinecraftClient client, String entityType, double maxDistance) {
        Entity nearest = null;
        double minDistance = Double.MAX_VALUE;

        for (Entity entity : client.world.getEntities()) {
            String typeId = Registries.ENTITY_TYPE.getId(entity.getType()).toString();
            if (typeId.equals(entityType) || typeId.endsWith(":" + entityType)) {
                double distance = new Vec3d(client.player.getX(), client.player.getY(), client.player.getZ())
                        .distanceTo(new Vec3d(entity.getX(), entity.getY(), entity.getZ()));
                if (distance <= maxDistance && distance < minDistance) {
                    minDistance = distance;
                    nearest = entity;
                }
            }
        }

        return nearest;
    }

    private double distanceToPlayer(MinecraftClient client, Entity target) {
        return new Vec3d(client.player.getX(), client.player.getY(), client.player.getZ())
            .distanceTo(new Vec3d(target.getX(), target.getY(), target.getZ()));
    }
}
