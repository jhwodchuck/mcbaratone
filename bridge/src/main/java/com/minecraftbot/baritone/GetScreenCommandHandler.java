package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.item.ItemStack;
import net.minecraft.registry.Registries;
import net.minecraft.screen.ScreenHandler;
import net.minecraft.screen.slot.Slot;

import java.net.Socket;

/**
 * Handler for the get_screen command - returns current screen info.
 */
public class GetScreenCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "get_screen";
    }

    @Override
    protected CommandResult execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        if (client.player == null) {
            return CommandResult.error("Player not available");
        }

        try {
            return client.submit(() -> {
                ScreenHandler handler = client.player.currentScreenHandler;
                if (handler == null) {
                    return CommandResult.error("No screen handler");
                }

                JsonObject data = new JsonObject();
                data.addProperty("sync_id", handler.syncId);
                data.addProperty("type", handler.getClass().getSimpleName());

                JsonArray slots = new JsonArray();
                for (int i = 0; i < handler.slots.size(); i++) {
                    Slot slot = handler.slots.get(i);
                    slots.add(serializeItemStack(slot.getStack(), i));
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
            itemData.addProperty("id", Registries.ITEM.getId(stack.getItem()).toString());
            itemData.addProperty("count", stack.getCount());
            itemData.addProperty("max_count", stack.getMaxCount());
            itemData.addProperty("damage", stack.getDamage());
            itemData.addProperty("max_damage", stack.getMaxDamage());
            itemData.addProperty("name", stack.getItem().toString());
        }

        return itemData;
    }
}
