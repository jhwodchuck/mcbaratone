package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;
import net.minecraft.world.inventory.ContainerInput;

public class InventoryClickCommandHandler implements CommandHandler {

    @Override
    public CompletableFuture<CommandResult> handle(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        try {
            CommandResult result = client.submit(() -> {
                if (client.player == null || client.gameMode == null) {
                    return CommandResult.error("Player not available");
                }
                 
                int slot = params.has("slot") ? params.get("slot").getAsInt() : -1;
                int button = params.has("button") ? params.get("button").getAsInt() : 0;
                String typeStr = params.has("type") ? params.get("type").getAsString().toUpperCase() : "PICKUP";
                 
                // Use current screen's sync_id if not provided
                int syncId = params.has("sync_id") && params.get("sync_id").getAsInt() != 0 
                    ? params.get("sync_id").getAsInt() 
                    : client.player.containerMenu.containerId;
                 
                ContainerInput type;
                try {
                    type = ContainerInput.valueOf(typeStr);
                } catch (Exception e) {
                    return CommandResult.error("Invalid type: " + typeStr);
                }
                 
                client.gameMode.handleContainerInput(syncId, slot, button, type, client.player);
                 
                JsonObject data = new JsonObject();
                data.addProperty("clicked", true);
                return CommandResult.success(data);
            }).get();
            return CompletableFuture.completedFuture(result);
        } catch (Exception e) {
            return CompletableFuture.completedFuture(CommandResult.error("Click failed: " + e.getMessage()));
        }
    }
    
    @Override
    public String getCommandName() { return "inventory_click"; }
}
