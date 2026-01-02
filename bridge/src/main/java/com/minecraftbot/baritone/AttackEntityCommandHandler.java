package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.entity.Entity;
import net.minecraft.registry.Registries;
import java.net.Socket;

/**
 * Handler for the attack_entity command.
 * Attacks a specific entity by ID.
 */
public class AttackEntityCommandHandler implements CommandHandler {

    @Override
    public CommandResult handle(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        if (client.player == null || client.interactionManager == null || client.world == null) {
            return CommandResult.error("Player/World not available");
        }
        
        try {
            if (!params.has("entity_id")) {
                return CommandResult.error("Missing required parameter: entity_id");
            }
            
            int entityId = params.get("entity_id").getAsInt();
            
            Entity target = client.world.getEntityById(entityId);
            if (target == null) {
                return CommandResult.error("Entity not found: " + entityId);
            }
            
            // Execute attack on main thread
            client.execute(() -> client.interactionManager.attackEntity(client.player, target));
            
            JsonObject data = new JsonObject();
            data.addProperty("attacked", true);
            data.addProperty("entity_id", entityId);
            data.addProperty("entity_type", Registries.ENTITY_TYPE.getId(target.getType()).toString());
            
            return CommandResult.success(data);
        } catch (Exception e) {
            return CommandResult.error("Attack entity failed: " + e.getMessage());
        }
    }

    @Override
    public String getCommandName() {
        return "attack_entity";
    }
}
