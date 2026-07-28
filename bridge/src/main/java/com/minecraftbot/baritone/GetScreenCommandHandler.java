package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.net.Socket;
import net.minecraft.client.Minecraft;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.item.ItemStack;

/**
 * Handler for the get_screen command - returns current screen info.
 */
public class GetScreenCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "get_screen";
    }

    @Override
    protected CommandResult execute(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        if (client.player == null) {
            return CommandResult.error("Player not available");
        }

        try {
            return client.submit(() -> {
                AbstractContainerMenu handler = client.player.containerMenu;
                if (handler == null) {
                    return CommandResult.error("No screen handler");
                }

                JsonObject data = new JsonObject();
                data.addProperty("sync_id", handler.containerId);
                data.addProperty("type", handler.getClass().getSimpleName());

                JsonArray slots = new JsonArray();
                for (int i = 0; i < handler.slots.size(); i++) {
                    Slot slot = handler.slots.get(i);
                    slots.add(serializeItemStack(slot.getItem(), i));
                }
                data.add("slots", slots);
                data.addProperty("total_slots", handler.slots.size());

                return CommandResult.success(data);
            }).get();
        } catch (Exception e) {
            return CommandResult.error("Failed to read screen: " + e.getMessage());
        }
    }

    private JsonObject serializeItemStack(ItemStack stack, int slot) {
        JsonObject itemData = new JsonObject();
        itemData.addProperty("slot", slot);

        if (stack.isEmpty()) {
            itemData.addProperty("id", "minecraft:air");
            itemData.addProperty("count", 0);
        } else {
            itemData.addProperty("id", BuiltInRegistries.ITEM.getKey(stack.getItem()).toString());
            itemData.addProperty("count", stack.getCount());
            itemData.addProperty("max_count", stack.getMaxStackSize());
            itemData.addProperty("damage", stack.getDamageValue());
            itemData.addProperty("max_damage", stack.getMaxDamage());
            itemData.addProperty("name", stack.getItem().toString());
        }

        return itemData;
    }
}
