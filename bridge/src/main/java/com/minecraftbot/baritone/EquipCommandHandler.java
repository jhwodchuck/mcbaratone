package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.entity.player.PlayerInventory;
import net.minecraft.inventory.Inventory;
import net.minecraft.item.ItemStack;
import net.minecraft.registry.Registries;
import net.minecraft.screen.ScreenHandler;
import net.minecraft.screen.slot.Slot;
import net.minecraft.screen.slot.SlotActionType;
import net.minecraft.util.Identifier;

import java.net.Socket;
import java.util.concurrent.CompletableFuture;

/**
 * Handler for equip command.
 * Equips an armor/offhand item by moving it into the target slot.
 */
public class EquipCommandHandler implements CommandHandler {

    @Override
    public CompletableFuture<CommandResult> handle(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        if (client.player == null || client.interactionManager == null) {
            return CompletableFuture.completedFuture(CommandResult.error("Player not available"));
        }

        try {
            CommandResult result = client.submit(() -> {
                String slotName = params.has("slot") ? params.get("slot").getAsString().toLowerCase() : "";
                String itemId = params.has("item") ? params.get("item").getAsString() : "";

                if (slotName.isBlank()) {
                    return CommandResult.error("Missing slot parameter");
                }
                if (itemId.isBlank()) {
                    return CommandResult.error("Missing item parameter");
                }

                Integer targetInventoryIndex = getTargetInventoryIndex(slotName);
                if (targetInventoryIndex == null) {
                    return CommandResult.error("Unsupported equip slot: " + slotName);
                }

                PlayerInventory inv = client.player.getInventory();
                int sourceInventoryIndex = findItemSlot(inv, itemId);
                if (sourceInventoryIndex < 0) {
                    return CommandResult.error("Item not found in inventory: " + itemId);
                }

                ScreenHandler handler = client.player.playerScreenHandler;
                Integer sourceSlotId = findScreenSlotId(handler, inv, sourceInventoryIndex);
                Integer targetSlotId = findScreenSlotId(handler, inv, targetInventoryIndex);

                if (sourceSlotId == null || targetSlotId == null) {
                    return CommandResult.error("Unable to resolve slot ids for equip");
                }

                int syncId = handler.syncId;
                client.interactionManager.clickSlot(syncId, sourceSlotId, 0, SlotActionType.PICKUP, client.player);
                client.interactionManager.clickSlot(syncId, targetSlotId, 0, SlotActionType.PICKUP, client.player);

                JsonObject data = new JsonObject();
                data.addProperty("equipped", true);
                data.addProperty("slot", slotName);
                data.addProperty("item", itemId);
                return CommandResult.success(data);
            }).get();

            return CompletableFuture.completedFuture(result);
        } catch (Exception e) {
            return CompletableFuture.completedFuture(CommandResult.error("Equip failed: " + e.getMessage()));
        }
    }

    private Integer getTargetInventoryIndex(String slotName) {
        switch (slotName) {
            case "head":
            case "helmet":
                return 39;
            case "chest":
            case "chestplate":
                return 38;
            case "legs":
            case "leggings":
                return 37;
            case "feet":
            case "boots":
                return 36;
            case "offhand":
                return 40;
            default:
                return null;
        }
    }

    private int findItemSlot(PlayerInventory inv, String itemId) {
        Identifier itemIdentifier;
        try {
            itemIdentifier = Identifier.of(itemId);
        } catch (IllegalArgumentException e) {
            return -1;
        }

        for (int i = 0; i < 36; i++) {
            ItemStack stack = inv.getStack(i);
            if (!stack.isEmpty() && Registries.ITEM.getId(stack.getItem()).equals(itemIdentifier)) {
                return i;
            }
        }
        return -1;
    }

    private Integer findScreenSlotId(ScreenHandler handler, Inventory inv, int inventoryIndex) {
        for (int i = 0; i < handler.slots.size(); i++) {
            Slot slot = handler.slots.get(i);
            if (slot.inventory == inv && slot.getIndex() == inventoryIndex) {
                return i;
            }
        }
        return null;
    }

    @Override
    public String getCommandName() {
        return "equip";
    }
}
