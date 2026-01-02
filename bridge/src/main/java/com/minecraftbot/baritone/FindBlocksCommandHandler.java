package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.block.BlockState;
import net.minecraft.client.MinecraftClient;
import net.minecraft.registry.Registries;
import net.minecraft.util.Identifier;
import net.minecraft.util.math.BlockPos;

import java.net.Socket;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.atomic.AtomicReference;

public class FindBlocksCommandHandler implements CommandHandler {

    @Override
    public CommandResult handle(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        if (client.player == null || client.world == null) {
            return CommandResult.error("Player/World not available");
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

        AtomicReference<JsonArray> resultRef = new AtomicReference<>(new JsonArray());

        // Run on main thread to access world safely
        try {
            return client.submit(() -> {
                JsonArray foundList = new JsonArray();
                BlockPos center = client.player.getBlockPos();
                
                int r = finalRadius;
                int l = finalLimit;
                int count = 0;
                
                // Spiral search or simple iteration? Simple iteration is fine for small radii.
                // For performance, we might want to optimize, but this is for bridge queries mostly.
                for (int x = -r; x <= r; x++) {
                    for (int y = -r; y <= r; y++) {
                        for (int z = -r; z <= r; z++) {
                            BlockPos pos = center.add(x, y, z);
                            BlockState state = client.world.getBlockState(pos);
                            String id = Registries.BLOCK.getId(state.getBlock()).toString();
                            
                            if (targetBlocks.contains(id)) {
                                JsonObject b = new JsonObject();
                                b.addProperty("x", pos.getX());
                                b.addProperty("y", pos.getY());
                                b.addProperty("z", pos.getZ());
                                b.addProperty("block", id);
                                b.addProperty("distance", Math.sqrt(pos.getSquaredDistance(center)));
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
        } catch (Exception e) {
            return CommandResult.error("Find blocks failed: " + e.getMessage());
        }
    }

    @Override
    public String getCommandName() {
        return "find_blocks";
    }
}
