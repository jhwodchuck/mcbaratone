package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;
import net.minecraft.world.inventory.ContainerInput;

public class InventoryClickCommandHandler extends AbstractBaseCommandHandler {

    @Override
    public CompletableFuture<CommandResult> handle(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        try {
            final net.minecraft.world.inventory.AbstractContainerMenu[] menu = {null};
            final JsonObject[] before = {null};
            return ObservedMutation.run(client::execute, () -> client.level.getGameTime(), () -> {
                if (client.player == null || client.gameMode == null) {
                    return CommandResult.error("Player not available");
                }
                 
                int slot = params.has("slot") ? params.get("slot").getAsInt() : -1;
                int button = params.has("button") ? params.get("button").getAsInt() : 0;
                String typeStr = params.has("type") ? params.get("type").getAsString().toUpperCase() : "PICKUP";
                 
                menu[0] = client.player.containerMenu;
                if (menu[0] == null) return CommandResult.error("No active container menu; no click dispatched");
                if ((slot < 0 && slot != -999) || slot >= menu[0].slots.size()) {
                    return CommandResult.error("Invalid menu slot");
                }
                if (params.has("sync_id") && params.get("sync_id").getAsInt() != menu[0].containerId) {
                    return CommandResult.error("Stale sync_id; no click dispatched");
                }
                before[0] = ContainerEvidence.snapshot(menu[0]);
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
                data.addProperty("clicked", false);
                data.add("before", before[0]);
                data.addProperty("slot", slot);
                return CommandResult.success(data);
            }, data -> {
                if (client.player.containerMenu != menu[0]) throw new IllegalStateException("Menu changed while observing click");
                JsonObject after = ContainerEvidence.snapshot(menu[0]);
                data.add("after", after);
                return !before[0].equals(after);
            }, "clicked");
        } catch (Exception e) {
            return CompletableFuture.completedFuture(CommandResult.error("Click failed: " + e.getMessage()));
        }
    }
    
    @Override
    public String getCommandName() { return "inventory_click"; }
}
