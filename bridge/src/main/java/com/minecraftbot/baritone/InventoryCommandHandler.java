package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.entity.player.PlayerInventory;
import net.minecraft.item.ItemStack;
import net.minecraft.registry.Registries;
import net.minecraft.screen.slot.SlotActionType;

import java.net.Socket;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Command handler for inventory operations: get_inventory, inventory_click.
 */
public class InventoryCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "inventory";
    }

    @Override
    protected CommandResult execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
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

    private CommandResult handleGetInventory(MinecraftClient client) {
        CountDownLatch latch = new CountDownLatch(1);
        AtomicReference<JsonObject> dataRef = new AtomicReference<>();
        AtomicReference<String> errorRef = new AtomicReference<>();

        client.execute(() -> {
            try {
                if (client.player == null) {
                    errorRef.set("Player not available");
                    return;
                }

                PlayerInventory inv = client.player.getInventory();

                // Main Inventory (0-35)
                JsonArray mainInventory = new JsonArray();
                for (int i = 0; i < 36; i++) {
                    mainInventory.add(serializeItemStack(inv.getStack(i), i));
                }

                // Armor (Slots 36-39)
                JsonArray armorInventory = new JsonArray();
                for (int i = 0; i < 4; i++) {
                    armorInventory.add(serializeItemStack(inv.getStack(36 + i), i));
                }

                // Offhand (Slot 40)
                JsonArray offhandInventory = new JsonArray();
                offhandInventory.add(serializeItemStack(inv.getStack(40), 0));

                JsonObject data = new JsonObject();
                data.add("inventory", mainInventory);
                data.add("armor", armorInventory);
                data.add("offhand", offhandInventory);
                data.addProperty("selected_slot", inv.selectedSlot);

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

    private CommandResult handleInventoryClick(MinecraftClient client, JsonObject params) {
        if (client.player == null || client.interactionManager == null) {
            return CommandResult.error("Player or interaction manager not available");
        }

        int slot = params.has("slot") ? params.get("slot").getAsInt() : -1;
        int button = params.has("button") ? params.get("button").getAsInt() : 0;
        String typeStr = params.has("type") ? params.get("type").getAsString().toUpperCase() : "PICKUP";

        // Default to player inventory (0)
        int syncId = params.has("sync_id") ? params.get("sync_id").getAsInt() : 0;

        SlotActionType type;
        try {
            type = SlotActionType.valueOf(typeStr);
        } catch (IllegalArgumentException e) {
            return CommandResult.error("Invalid click type: " + typeStr);
        }

        try {
            client.interactionManager.clickSlot(syncId, slot, button, type, client.player);

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

    private JsonObject serializeItemStack(ItemStack stack, int slot) {
        JsonObject itemData = new JsonObject();
        itemData.addProperty("slot", slot);

        if (stack.isEmpty()) {
            itemData.addProperty("id", "minecraft:air");
            itemData.addProperty("count", 0);
        } else {
            itemData.addProperty("id", Registries.ITEM.getId(stack.getItem()).toString());
            itemData.addProperty("count", stack.getCount());
            itemData.addProperty("max_count", stack.getMaxCount());
            itemData.addProperty("damage", stack.getDamage());
            itemData.addProperty("max_damage", stack.getMaxDamage());
            itemData.addProperty("name", stack.getItem().toString()); // Safer fallback
        }

        return itemData;
    }
}