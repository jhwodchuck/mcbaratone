package com.minecraftbot.baritone;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import it.unimi.dsi.fastutil.objects.AbstractObject2IntMap;
import java.util.Optional;
import java.util.Set;
import net.minecraft.core.Holder;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.Identifier;
import net.minecraft.resources.ResourceKey;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.enchantment.Enchantment;
import net.minecraft.world.item.enchantment.ItemEnchantments;
import net.minecraft.world.item.trading.MerchantOffer;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

class ItemStackJsonSerializerTest {
    @BeforeAll
    static void initializeMinecraft() {
        net.minecraft.SharedConstants.tryDetectVersion();
        net.minecraft.server.Bootstrap.bootStrap();
    }

    @Test
    void serializesStoredEnchantmentComponentWithStableIdentifier() {
        Holder.Reference<Enchantment> holder = enchantmentHolder("mending");
        ItemEnchantments enchantments = mock(ItemEnchantments.class);
        when(enchantments.entrySet()).thenReturn(
            Set.of(new AbstractObject2IntMap.BasicEntry<>(holder, 1))
        );

        JsonArray stored = ItemStackJsonSerializer.serializeEnchantments(enchantments);

        assertEquals("minecraft:mending", stored.get(0).getAsJsonObject().get("id").getAsString());
        assertEquals(1, stored.get(0).getAsJsonObject().get("level").getAsInt());
    }

    @Test
    void preservesEmptyItemFieldsAndEmitsStableEmptyEvidence() {
        JsonObject serialized = ItemStackJsonSerializer.serialize(ItemStack.EMPTY, 3);

        assertEquals("minecraft:air", serialized.get("id").getAsString());
        assertEquals(0, serialized.get("count").getAsInt());
        assertEquals(0, serialized.getAsJsonArray("enchantments").size());
        assertEquals(0, serialized.getAsJsonArray("stored_enchantments").size());
        assertEquals(0, serialized.getAsJsonObject("components").size());
    }

    @Test
    void serializesDetailedMerchantOfferAndResultSlotEvidence() {
        MerchantOffer offer = mock(MerchantOffer.class);
        when(offer.getBaseCostA()).thenReturn(ItemStack.EMPTY);
        when(offer.getCostA()).thenReturn(ItemStack.EMPTY);
        when(offer.getCostB()).thenReturn(ItemStack.EMPTY);
        when(offer.getResult()).thenReturn(ItemStack.EMPTY);
        when(offer.getMaxUses()).thenReturn(12);
        when(offer.getXp()).thenReturn(5);
        when(offer.getPriceMultiplier()).thenReturn(0.2F);

        JsonObject serialized = ItemStackJsonSerializer.serializeMerchantOffer(offer, 4);

        assertEquals(4, serialized.get("index").getAsInt());
        assertEquals(0, serialized.get("payment_a_slot").getAsInt());
        assertEquals(1, serialized.get("payment_b_slot").getAsInt());
        assertEquals(2, serialized.get("result_slot").getAsInt());
        assertEquals("minecraft:air", serialized.getAsJsonObject("cost_a").get("id").getAsString());
        assertEquals("minecraft:air", serialized.getAsJsonObject("cost_b").get("id").getAsString());
        assertEquals("minecraft:air", serialized.getAsJsonObject("result").get("id").getAsString());
        assertEquals(0, serialized.get("uses").getAsInt());
        assertEquals(12, serialized.get("max_uses").getAsInt());
        assertEquals(5, serialized.get("xp").getAsInt());
        assertTrue(serialized.get("available").getAsBoolean());
        assertFalse(serialized.get("out_of_stock").getAsBoolean());
    }

    @SuppressWarnings("unchecked")
    private static Holder.Reference<Enchantment> enchantmentHolder(String enchantmentName) {
        Holder.Reference<Enchantment> holder = mock(Holder.Reference.class);
        ResourceKey<Enchantment> key = ResourceKey.create(
            Registries.ENCHANTMENT,
            Identifier.withDefaultNamespace(enchantmentName)
        );
        when(holder.unwrapKey()).thenReturn(Optional.of(key));
        return holder;
    }
}
