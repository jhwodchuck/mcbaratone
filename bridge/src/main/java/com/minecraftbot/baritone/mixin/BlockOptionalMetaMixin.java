package com.minecraftbot.baritone.mixin;

import baritone.api.utils.BlockOptionalMeta;
import com.minecraftbot.baritone.ClientSafeBlockDrops;
import java.util.List;
import java.util.Map;
import net.minecraft.world.item.Item;
import net.minecraft.world.level.block.Block;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Overwrite;
import org.spongepowered.asm.mixin.Shadow;

/** Prevent Baritone's fake-server loot lookup from running on multiplayer clients. */
@Mixin(value = BlockOptionalMeta.class, remap = false)
public abstract class BlockOptionalMetaMixin {
    @Shadow(remap = false)
    private static Map<Block, List<Item>> drops;

    /**
     * @author mcbaratone
     * @reason Fabric Loot API 26.2 dereferences ServerLevel.getServer(), which
     *         is null for Baritone's client-side stub.
     */
    @Overwrite(remap = false)
    private static synchronized List<Item> drops(Block block) {
        return drops.computeIfAbsent(block, ClientSafeBlockDrops::itemsFor);
    }
}
