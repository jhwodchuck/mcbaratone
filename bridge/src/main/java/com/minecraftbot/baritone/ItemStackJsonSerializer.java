package com.minecraftbot.baritone;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import it.unimi.dsi.fastutil.objects.Object2IntMap;
import net.minecraft.core.Holder;
import net.minecraft.core.component.DataComponents;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.enchantment.Enchantment;
import net.minecraft.world.item.enchantment.ItemEnchantments;
import net.minecraft.world.item.trading.MerchantOffer;

/** Shared, read-only JSON serialization for bridge item and merchant evidence. */
final class ItemStackJsonSerializer {
    private ItemStackJsonSerializer() {
    }

    static JsonObject serialize(ItemStack stack, int slot) {
        JsonObject itemData = new JsonObject();
        itemData.addProperty("slot", slot);

        if (stack.isEmpty()) {
            itemData.addProperty("id", "minecraft:air");
            itemData.addProperty("count", 0);
            itemData.add("enchantments", new JsonArray());
            itemData.add("stored_enchantments", new JsonArray());
            itemData.add("components", new JsonObject());
            return itemData;
        }

        itemData.addProperty("id", BuiltInRegistries.ITEM.getKey(stack.getItem()).toString());
        itemData.addProperty("count", stack.getCount());
        itemData.addProperty("max_count", stack.getMaxStackSize());
        itemData.addProperty("damage", stack.getDamageValue());
        itemData.addProperty("max_damage", stack.getMaxDamage());
        itemData.addProperty("name", stack.getItem().toString());
        itemData.addProperty("display_name", stack.getHoverName().getString());

        JsonArray enchantments = serializeEnchantments(stack.get(DataComponents.ENCHANTMENTS));
        JsonArray storedEnchantments = serializeEnchantments(stack.get(DataComponents.STORED_ENCHANTMENTS));
        itemData.add("enchantments", enchantments);
        itemData.add("stored_enchantments", storedEnchantments);

        JsonObject components = new JsonObject();
        if (!enchantments.isEmpty()) {
            components.add("minecraft:enchantments", enchantments.deepCopy());
        }
        if (!storedEnchantments.isEmpty()) {
            components.add("minecraft:stored_enchantments", storedEnchantments.deepCopy());
        }
        itemData.add("components", components);
        return itemData;
    }

    static JsonObject serializeMerchantOffer(MerchantOffer offer, int index) {
        JsonObject data = new JsonObject();
        data.addProperty("index", index);
        data.addProperty("payment_a_slot", 0);
        data.addProperty("payment_b_slot", 1);
        data.addProperty("result_slot", 2);
        data.add("base_cost_a", serialize(offer.getBaseCostA(), 0));
        data.add("cost_a", serialize(offer.getCostA(), 0));
        data.add("cost_b", serialize(offer.getCostB(), 1));
        data.add("result", serialize(offer.getResult(), 2));
        data.addProperty("uses", offer.getUses());
        data.addProperty("max_uses", offer.getMaxUses());
        data.addProperty("demand", offer.getDemand());
        data.addProperty("special_price", offer.getSpecialPriceDiff());
        data.addProperty("price_multiplier", offer.getPriceMultiplier());
        data.addProperty("xp", offer.getXp());
        data.addProperty("reward_exp", offer.shouldRewardExp());
        data.addProperty("out_of_stock", offer.isOutOfStock());
        data.addProperty("needs_restock", offer.needsRestock());
        data.addProperty("available", !offer.isOutOfStock());
        return data;
    }

    static JsonArray serializeEnchantments(ItemEnchantments enchantments) {
        JsonArray values = new JsonArray();
        if (enchantments == null) {
            return values;
        }
        for (Object2IntMap.Entry<Holder<Enchantment>> entry : enchantments.entrySet()) {
            JsonObject value = new JsonObject();
            String id = entry.getKey().unwrapKey()
                .map(key -> key.identifier().toString())
                .orElseGet(entry.getKey()::getRegisteredName);
            value.addProperty("id", id);
            value.addProperty("level", entry.getIntValue());
            values.add(value);
        }
        return values;
    }
}
