package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.entity.player.PlayerInventory;
import net.minecraft.item.ItemStack;
import net.minecraft.registry.Registries;
import java.net.Socket;

public class GetInventoryCommandHandler implements CommandHandler {

    @Override
    public CommandResult handle(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        try {
            return client.submit(() -> {
                if (client.player == null) return CommandResult.error("Player not available");
                PlayerInventory inv = client.player.getInventory();

                JsonArray mainInventory = new JsonArray();
                for (int i = 0; i < 36; i++) {
                    mainInventory.add(serializeItemStack(inv.getStack(i), i));
                }

                JsonArray armorInventory = new JsonArray();
                for (int i = 0; i < 4; i++) {
                    armorInventory.add(serializeItemStack(inv.getStack(36 + i), 36 + i));
                }

                JsonArray offhandInventory = new JsonArray();
                offhandInventory.add(serializeItemStack(inv.getStack(40), 40));

                JsonObject data = new JsonObject();
                data.add("inventory", mainInventory);
                data.add("armor", armorInventory);
                data.add("offhand", offhandInventory);
                data.addProperty("selected_slot", inv.selectedSlot);

                return CommandResult.success(data);
            }).get();
        } catch (Exception e) {
             return CommandResult.error("GetInventory failed: " + e.getMessage());
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
            itemData.addProperty("name", stack.getItem().toString());
        }
        return itemData;
    }

    @Override
    public String getCommandName() { return "get_inventory"; }
}
