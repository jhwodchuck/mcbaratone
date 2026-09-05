package com.minecraftbot.baritone;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.item.ItemStack;

final class ContainerEvidence {
    static JsonObject item(ItemStack stack) {
        return ItemStackJsonSerializer.serialize(stack, -1);
    }
    static JsonObject snapshot(AbstractContainerMenu menu) {
        JsonObject result = new JsonObject();
        result.addProperty("sync_id", menu.containerId);
        result.addProperty("menu_type", menu.getClass().getSimpleName());
        JsonArray slots = new JsonArray();
        int slotIndex = 0;
        for (Slot slot : menu.slots) {
            slots.add(ItemStackJsonSerializer.serialize(slot.getItem(), slotIndex++));
        }
        result.add("slots", slots);
        result.add("cursor", item(menu.getCarried()));
        return result;
    }
}
