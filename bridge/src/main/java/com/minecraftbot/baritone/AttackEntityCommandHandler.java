package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.entity.Entity;

/**
 * Handler for the attack_entity command.
 * Attacks a specific entity by ID.
 */
public class AttackEntityCommandHandler implements CommandHandler {

    @Override
    public CompletableFuture<CommandResult> handle(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
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
            if (result.isDone()) {
                return;
            }
            try {
                if (client.player == null || client.gameMode == null || client.level == null) {
                    result.complete(CommandResult.error("Player/World not available"));
                    return;
                }

                Entity target = client.level.getEntity(entityId);
                if (!isValidAttackTarget(client.player, target)) {
                    result.complete(CommandResult.error("Entity not found: " + entityId));
                    return;
                }

                float cooldown = client.player.getAttackStrengthScale(0.0f);
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

                client.gameMode.attack(client.player, target);
                client.player.swing(InteractionHand.MAIN_HAND);
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

    /** Reject stale client ghosts and entities that cannot receive an attack. */
    static boolean isValidAttackTarget(Entity player, Entity target) {
        return target != null && isValidAttackTarget(
            target == player,
            target.isRemoved(),
            target.isAlive(),
            target.isAttackable()
        );
    }

    static boolean isValidAttackTarget(
        boolean self,
        boolean removed,
        boolean alive,
        boolean attackable
    ) {
        return !self && !removed && alive && attackable;
    }

    String entityType(Entity target) {
        return BuiltInRegistries.ENTITY_TYPE.getKey(target.getType()).toString();
    }

    @Override
    public String getCommandName() {
        return "attack_entity";
    }
}
