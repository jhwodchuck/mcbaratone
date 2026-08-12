package com.minecraftbot.baritone;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.tags.BlockTags;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;

/**
 * Client-safe item identities for Baritone block targets.
 *
 * <p>Baritone normally builds this set by running server loot tables against a
 * fake {@code ServerLevel}. Fabric Loot API 26.2 requires a real server and
 * throws on multiplayer clients. These identities deliberately avoid loot
 * quantities and random/enchantment-sensitive outcomes; Baritone only needs
 * stable item hashes for mine-process inventory matching.</p>
 */
public final class ClientSafeBlockDrops {
    private ClientSafeBlockDrops() {
    }

    public static List<Item> itemsFor(Block block) {
        Set<Item> items = new LinkedHashSet<>();

        if (block == Blocks.STONE) add(items, Items.COBBLESTONE);
        else if (block == Blocks.DEEPSLATE) add(items, Items.COBBLED_DEEPSLATE);
        else if (block == Blocks.COBBLESTONE) add(items, Items.COBBLESTONE);
        else if (block == Blocks.MOSSY_COBBLESTONE) add(items, Items.MOSSY_COBBLESTONE);
        else if (block == Blocks.COBBLED_DEEPSLATE) add(items, Items.COBBLED_DEEPSLATE);
        else if (block == Blocks.COAL_ORE || block == Blocks.DEEPSLATE_COAL_ORE) add(items, Items.COAL);
        else if (block == Blocks.IRON_ORE || block == Blocks.DEEPSLATE_IRON_ORE) add(items, Items.RAW_IRON);
        else if (block == Blocks.COPPER_ORE || block == Blocks.DEEPSLATE_COPPER_ORE) add(items, Items.RAW_COPPER);
        else if (block == Blocks.GOLD_ORE || block == Blocks.DEEPSLATE_GOLD_ORE) add(items, Items.RAW_GOLD);
        else if (block == Blocks.DIAMOND_ORE || block == Blocks.DEEPSLATE_DIAMOND_ORE) add(items, Items.DIAMOND);
        else if (block == Blocks.EMERALD_ORE || block == Blocks.DEEPSLATE_EMERALD_ORE) add(items, Items.EMERALD);
        else if (block == Blocks.LAPIS_ORE || block == Blocks.DEEPSLATE_LAPIS_ORE) add(items, Items.LAPIS_LAZULI);
        else if (block == Blocks.REDSTONE_ORE || block == Blocks.DEEPSLATE_REDSTONE_ORE) add(items, Items.REDSTONE);
        else if (block == Blocks.NETHER_QUARTZ_ORE) add(items, Items.QUARTZ);
        else if (block == Blocks.NETHER_GOLD_ORE) add(items, Items.GOLD_NUGGET);
        else if (block == Blocks.AMETHYST_CLUSTER) add(items, Items.AMETHYST_SHARD);
        else if (block == Blocks.CLAY) add(items, Items.CLAY_BALL);
        else if (block == Blocks.GLOWSTONE) add(items, Items.GLOWSTONE_DUST);
        else if (block == Blocks.MELON) add(items, Items.MELON_SLICE);
        else if (block == Blocks.SEA_LANTERN) add(items, Items.PRISMARINE_CRYSTALS);
        else if (block == Blocks.SHORT_GRASS || block == Blocks.TALL_GRASS) add(items, Items.WHEAT_SEEDS);
        else if (block == Blocks.SUGAR_CANE) add(items, Items.SUGAR_CANE);
        else if (block == Blocks.GRASS_BLOCK
                || block == Blocks.PODZOL
                || block == Blocks.MYCELIUM
                || block == Blocks.ROOTED_DIRT) add(items, Items.DIRT);
        else if (block == Blocks.BOOKSHELF) add(items, Items.BOOK);
        else if (block == Blocks.GRAVEL) {
            add(items, Items.GRAVEL);
            add(items, Items.FLINT);
        } else if (isKnownNoDropOrToolSensitive(block)) {
            // Fail closed rather than inventing a Silk Touch or unobtainable
            // drop identity that could make Baritone's item counts lie.
        } else if (isKnownSelfDrop(block)) {
            add(items, block.asItem());
        }

        return new ArrayList<>(items);
    }

    private static void add(Set<Item> items, Item item) {
        if (item != null && item != Items.AIR) {
            items.add(item);
        }
    }

    private static boolean isKnownSelfDrop(Block block) {
        var state = block.defaultBlockState();
        String path = BuiltInRegistries.BLOCK.getKey(block).getPath();
        if (state.is(BlockTags.LOGS)
                || state.is(BlockTags.PLANKS)
                || state.is(BlockTags.WOOL)
                || state.is(BlockTags.BEDS)
                || state.is(BlockTags.ANVIL)
                || state.is(BlockTags.RAILS)
                || state.is(BlockTags.TERRACOTTA)
                || state.is(BlockTags.GLAZED_TERRACOTTA)
                || state.is(BlockTags.CONCRETE)
                || state.is(BlockTags.CONCRETE_POWDERS)
                || state.is(BlockTags.SAND)
                || state.is(BlockTags.BASE_STONE_OVERWORLD)
                || state.is(BlockTags.BASE_STONE_NETHER)) {
            return true;
        }

        if (path.endsWith("_log")
                || path.endsWith("_wood")
                || path.endsWith("_stem")
                || path.endsWith("_hyphae")
                || path.endsWith("_planks")
                || path.endsWith("_bed")) {
            return true;
        }

        if (block == Blocks.DIRT
                || block == Blocks.COARSE_DIRT
                || block == Blocks.MUD
                || block == Blocks.PACKED_MUD
                || block == Blocks.SOUL_SAND
                || block == Blocks.SOUL_SOIL
                || block == Blocks.OBSIDIAN
                || block == Blocks.CRYING_OBSIDIAN
                || block == Blocks.END_STONE
                || block == Blocks.CALCITE
                || block == Blocks.TUFF
                || block == Blocks.DRIPSTONE_BLOCK
                || block == Blocks.PRISMARINE
                || block == Blocks.PRISMARINE_BRICKS
                || block == Blocks.DARK_PRISMARINE) {
            return true;
        }

        return path.endsWith("_bricks")
            || path.endsWith("_brick_slab")
            || path.endsWith("_brick_stairs")
            || path.endsWith("_brick_wall");
    }

    private static boolean isKnownNoDropOrToolSensitive(Block block) {
        var state = block.defaultBlockState();
        return block == Blocks.BEDROCK
            || block == Blocks.SPAWNER
            || block == Blocks.BUDDING_AMETHYST
            || block == Blocks.GLASS
            || state.is(BlockTags.ICE)
            || state.is(BlockTags.LEAVES);
    }
}
