package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.net.Socket;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.level.block.state.BlockState;

/**
 * Command handler for getting a view of nearby blocks.
 */
public class GetViewCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "get_view";
    }

    @Override
    protected CommandResult execute(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        if (client.level == null || client.player == null) {
            return CommandResult.error("World or player not available");
        }

        int radiusParam = params.has("radius") ? params.get("radius").getAsInt() : 5;
        if (radiusParam > 32) radiusParam = 32; // Safety limit
        final int radius = radiusParam;

        try {
            return client.submit(() -> {
                BlockPos playerPos = client.player.blockPosition();
                JsonArray voxelsList = new JsonArray();

                for (int x = -radius; x <= radius; x++) {
                    for (int y = -radius; y <= radius; y++) {
                        for (int z = -radius; z <= radius; z++) {
                            BlockPos pos = playerPos.offset(x, y, z);
                            BlockState state = client.level.getBlockState(pos);
                            
                            if (!state.isAir()) {
                                JsonObject voxel = new JsonObject();
                                voxel.addProperty("x", pos.getX());
                                voxel.addProperty("y", pos.getY());
                                voxel.addProperty("z", pos.getZ());
                                voxel.addProperty("id", BuiltInRegistries.BLOCK.getKey(state.getBlock()).toString());
                                voxelsList.add(voxel);
                            }
                        }
                    }
                }

                JsonObject data = new JsonObject();
                data.add("voxels", voxelsList);
                return CommandResult.success(data);
            }).get();
        } catch (Exception e) {
            logger.error("Error in get_view: ", e);
            return CommandResult.error("Failed to get view: " + e.getMessage());
        }
    }
}
