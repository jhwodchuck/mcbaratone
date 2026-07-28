package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.HashSet;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.level.block.state.BlockState;

public class FindBlocksCommandHandler implements CommandHandler {

    @Override
    public CompletableFuture<CommandResult> handle(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        if (client.player == null || client.level == null) {
            return CompletableFuture.completedFuture(CommandResult.error("Player/World not available"));
        }

        JsonArray blocksJson = params.getAsJsonArray("blocks");
        Set<String> targetBlocks = new HashSet<>();
        for (JsonElement e : blocksJson) {
            String id = e.getAsString();
            if (!id.contains(":")) id = "minecraft:" + id;
            targetBlocks.add(id);
        }

        int radius = params.has("radius") ? params.get("radius").getAsInt() : 32;
        int limit = params.has("limit") ? params.get("limit").getAsInt() : 100;
        if (radius > 128) radius = 128; // Sanity cap
        
        final int finalRadius = radius;
        final int finalLimit = limit;

        try {
            CommandResult result = client.submit(() -> {
                JsonArray foundList = new JsonArray();
                BlockPos center = client.player.blockPosition();
                
                int r = finalRadius;
                int count = 0;
                
                for (int x = -r; x <= r; x++) {
                    for (int y = -r; y <= r; y++) {
                        for (int z = -r; z <= r; z++) {
                            BlockPos pos = center.offset(x, y, z);
                            BlockState state = client.level.getBlockState(pos);
                            String id = BuiltInRegistries.BLOCK.getKey(state.getBlock()).toString();
                            
                            if (targetBlocks.contains(id)) {
                                JsonObject b = new JsonObject();
                                b.addProperty("x", pos.getX());
                                b.addProperty("y", pos.getY());
                                b.addProperty("z", pos.getZ());
                                b.addProperty("block", id);
                                b.addProperty("distance", Math.sqrt(pos.distSqr(center)));
                                foundList.add(b);
                                count++;
                                if (count >= finalLimit) break;
                            }
                        }
                        if (count >= finalLimit) break;
                    }
                    if (count >= finalLimit) break;
                }
                
                JsonObject data = new JsonObject();
                data.add("found", foundList);
                data.addProperty("count", count);
                return CommandResult.success(data);
            }).get();
            return CompletableFuture.completedFuture(result);
        } catch (Exception e) {
            return CompletableFuture.completedFuture(CommandResult.error("Find blocks failed: " + e.getMessage()));
        }
    }

    @Override
    public String getCommandName() {
        return "find_blocks";
    }
}
