package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.entity.Entity;
import net.minecraft.registry.Registries;
import net.minecraft.util.Hand;

import java.net.Socket;
import java.util.concurrent.CompletableFuture;

/**
 * Handler for the attack_entity command.
 * Attacks a specific entity by ID.
 */
public class AttackEntityCommandHandler implements CommandHandler {

    @Override
    public CompletableFuture<CommandResult> handle(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        if (!params.has("entity_id")) {
            return CompletableFuture.completedFuture(CommandResult.error("Missing required parameter: entity_id"));
        }

        int entityId;
        float minimumCooldown;
        try {
            entityId = params.get("entity_id").getAsInt();
            minimumCooldown = params.has("min_cooldown")
                ? clampCooldown(params.get("min_cooldown").getAsFloat())
                : 0.0f;
        } catch (Exception e) {
            return CompletableFuture.completedFuture(CommandResult.error("Invalid attack parameters: " + e.getMessage()));
        }

        CompletableFuture<CommandResult> result = new CompletableFuture<>();
        Runnable attackTask = () -> {
            try {
                if (client.player == null || client.interactionManager == null || client.world == null) {
                    result.complete(CommandResult.error("Player/World not available"));
                    return;
                }

                Entity target = client.world.getEntityById(entityId);
                if (target == null) {
                    result.complete(CommandResult.error("Entity not found: " + entityId));
                    return;
                }

                float cooldown = client.player.getAttackCooldownProgress(0.0f);
                JsonObject data = new JsonObject();
                data.addProperty("entity_id", entityId);
                data.addProperty("entity_type", entityType(target));
                data.addProperty("attack_cooldown", cooldown);
                data.addProperty("minimum_cooldown", minimumCooldown);

                if (!cooldownReady(cooldown, minimumCooldown)) {
                    data.addProperty("attacked", false);
                    data.addProperty("reason", "cooldown");
                    result.complete(CommandResult.success(data));
                    return;
                }

                client.interactionManager.attackEntity(client.player, target);
                client.player.swingHand(Hand.MAIN_HAND);
                data.addProperty("attacked", true);
                result.complete(CommandResult.success(data));
            } catch (Exception e) {
                result.complete(CommandResult.error("Attack entity failed: " + e.getMessage()));
            }
        };
        try {
            client.execute(attackTask);
        } catch (Exception e) {
            result.complete(CommandResult.error("Could not schedule attack: " + e.getMessage()));
        }
        return result;
    }

    static float clampCooldown(float cooldown) {
        return Math.max(0.0f, Math.min(1.0f, cooldown));
    }

    static boolean cooldownReady(float cooldown, float minimumCooldown) {
        return cooldown >= minimumCooldown;
    }

    String entityType(Entity target) {
        return Registries.ENTITY_TYPE.getId(target.getType()).toString();
    }

    @Override
    public String getCommandName() {
        return "attack_entity";
    }
}
