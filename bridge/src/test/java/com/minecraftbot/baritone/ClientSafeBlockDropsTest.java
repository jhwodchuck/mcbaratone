package com.minecraftbot.baritone;

import net.minecraft.world.item.Items;
import net.minecraft.world.level.block.Blocks;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ClientSafeBlockDropsTest {
    @BeforeAll
    static void initializeMinecraft() {
        net.minecraft.SharedConstants.tryDetectVersion();
        net.minecraft.server.Bootstrap.bootStrap();
    }

    @Test
    void mapsRepresentativeTransformedAndSelfDrops() {
        assertEquals(
            java.util.List.of(Items.COBBLESTONE),
            ClientSafeBlockDrops.itemsFor(Blocks.STONE));
        assertEquals(
            java.util.List.of(Items.RAW_IRON),
            ClientSafeBlockDrops.itemsFor(Blocks.IRON_ORE));
        assertEquals(
            java.util.List.of(Items.COBBLESTONE),
            ClientSafeBlockDrops.itemsFor(Blocks.COBBLESTONE));
        assertEquals(
            java.util.List.of(Items.OAK_LOG),
            ClientSafeBlockDrops.itemsFor(Blocks.OAK_LOG));
    }

    @Test
    void failsClosedForUnobtainableOrToolSensitiveBlocks() {
        assertTrue(ClientSafeBlockDrops.itemsFor(Blocks.GLASS).isEmpty());
        assertTrue(ClientSafeBlockDrops.itemsFor(Blocks.SPAWNER).isEmpty());
        assertTrue(ClientSafeBlockDrops.itemsFor(Blocks.BEDROCK).isEmpty());
        assertTrue(ClientSafeBlockDrops.itemsFor(Blocks.BUDDING_AMETHYST).isEmpty());
    }
}
