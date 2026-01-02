package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.screen.slot.SlotActionType;
import java.net.Socket;

public class InventoryClickCommandHandler implements CommandHandler {

    @Override
    public CommandResult handle(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
         try {
             return client.submit(() -> {
                 if (client.player == null || client.interactionManager == null) {
                     return CommandResult.error("Player not available");
                 }
                 
                 int slot = params.has("slot") ? params.get("slot").getAsInt() : -1;
                 int button = params.has("button") ? params.get("button").getAsInt() : 0;
                 String typeStr = params.has("type") ? params.get("type").getAsString().toUpperCase() : "PICKUP";
                 int syncId = params.has("sync_id") ? params.get("sync_id").getAsInt() : 0;
                 
                 SlotActionType type;
                 try {
                     type = SlotActionType.valueOf(typeStr);
                 } catch (Exception e) {
                     return CommandResult.error("Invalid type: " + typeStr);
                 }
                 
                 client.interactionManager.clickSlot(syncId, slot, button, type, client.player);
                 
                 JsonObject data = new JsonObject();
                 data.addProperty("clicked", true);
                 return CommandResult.success(data);
             }).get();
         } catch (Exception e) {
             return CommandResult.error("Click failed: " + e.getMessage());
         }
    }
    
    @Override
    public String getCommandName() { return "inventory_click"; }
}
