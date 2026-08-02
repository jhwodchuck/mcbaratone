package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import net.minecraft.client.Minecraft;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.inventory.ContainerInput;

/**
 * Command handler for inventory operations: get_inventory, inventory_click.
 */
public class InventoryCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "inventory";
    }

    @Override
    protected CommandResult execute(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        String action = params.has("action") ? params.get("action").getAsString() : "get";

        switch (action) {
            case "get":
                return handleGetInventory(client);
            case "click":
                return handleInventoryClick(client, params);
            default:
                return CommandResult.error("Unknown inventory action: " + action);
        }
    }

    private CommandResult handleGetInventory(Minecraft client) {
        CountDownLatch latch = new CountDownLatch(1);
        AtomicReference<JsonObject> dataRef = new AtomicReference<>();
        AtomicReference<String> errorRef = new AtomicReference<>();

        client.execute(() -> {
            try {
                if (client.player == null) {
                    errorRef.set("Player not available");
                    return;
                }

                Inventory inv = client.player.getInventory();

                // Main Inventory (0-35)
                JsonArray mainInventory = new JsonArray();
                for (int i = 0; i < 36; i++) {
                    mainInventory.add(ItemStackJsonSerializer.serialize(inv.getItem(i), i));
                }

                // Armor (Slots 36-39)
                JsonArray armorInventory = new JsonArray();
                for (int i = 0; i < 4; i++) {
                    armorInventory.add(ItemStackJsonSerializer.serialize(inv.getItem(36 + i), i));
                }

                // Offhand (Slot 40)
                JsonArray offhandInventory = new JsonArray();
                offhandInventory.add(ItemStackJsonSerializer.serialize(inv.getItem(40), 0));

                JsonObject data = new JsonObject();
                data.add("inventory", mainInventory);
                data.add("armor", armorInventory);
                data.add("offhand", offhandInventory);
                data.addProperty("selected_slot", inv.selected);

                dataRef.set(data);
            } catch (Exception e) {
                errorRef.set("Failed to retrieve inventory: " + e.getMessage());
            } finally {
                latch.countDown();
            }
        });

        try {
            if (!latch.await(5, TimeUnit.SECONDS)) {
                return CommandResult.error("Timeout waiting for inventory data");
            }
        } catch (InterruptedException e) {
            return CommandResult.error("Interrupted while waiting for inventory data");
        }

        if (errorRef.get() != null) {
            return CommandResult.error(errorRef.get());
        }

        return CommandResult.success(dataRef.get());
    }

    private CommandResult handleInventoryClick(Minecraft client, JsonObject params) {
        if (client.player == null || client.gameMode == null) {
            return CommandResult.error("Player or interaction manager not available");
        }

        int slot = params.has("slot") ? params.get("slot").getAsInt() : -1;
        int button = params.has("button") ? params.get("button").getAsInt() : 0;
        String typeStr = params.has("type") ? params.get("type").getAsString().toUpperCase() : "PICKUP";

        // Default to player inventory (0)
        int syncId = params.has("sync_id") ? params.get("sync_id").getAsInt() : 0;

        ContainerInput type;
        try {
            type = ContainerInput.valueOf(typeStr);
        } catch (IllegalArgumentException e) {
            return CommandResult.error("Invalid click type: " + typeStr);
        }

        try {
            client.gameMode.handleContainerInput(syncId, slot, button, type, client.player);

            JsonObject data = new JsonObject();
            data.addProperty("clicked", true);
            data.addProperty("slot", slot);
            data.addProperty("type", type.toString());
            data.addProperty("button", button);
            data.addProperty("sync_id", syncId);
            return CommandResult.success(data);
        } catch (Exception e) {
            logger.error("Inventory click failed", e);
            return CommandResult.error("Click failed: " + e.getMessage());
        }
    }

}
