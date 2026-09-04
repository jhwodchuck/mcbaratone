package com.minecraftbot.baritone;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.inventory.AbstractContainerMenu;
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
        menu.slots.forEach(slot -> slots.add(item(slot.getItem())));
        result.add("slots", slots);
        result.add("cursor", item(menu.getCarried()));
        return result;
    }
}
