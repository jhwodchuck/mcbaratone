package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.level.block.state.BlockState;

public class GetBlockCommandHandler implements CommandHandler {

    @Override
    public CompletableFuture<CommandResult> handle(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        if (client.level == null) {
            return CompletableFuture.completedFuture(CommandResult.error("World not available"));
        }

        try {
            int x = params.get("x").getAsInt();
            int y = params.get("y").getAsInt();
            int z = params.get("z").getAsInt();
            BlockPos pos = new BlockPos(x, y, z);
            
            CommandResult result = client.submit(() -> {
                BlockState state = client.level.getBlockState(pos);
                JsonObject data = new JsonObject();
                String id = "";
                if (state != null && state.getBlock() != null) {
                    id = BuiltInRegistries.BLOCK.getKey(state.getBlock()).toString();
                    
                    // Add state properties
                    JsonObject properties = new JsonObject();
                    state.getValues().forEach(propertyValue -> {
                        properties.addProperty(
                                propertyValue.property().getName(),
                                propertyValue.valueName());
                    });
                    data.add("state", properties);
                }
                data.addProperty("id", id);
                return CommandResult.success(data);
            }).get();
            
            return CompletableFuture.completedFuture(result);
        } catch (Exception e) {
            return CompletableFuture.completedFuture(CommandResult.error("GetBlock failed: " + e.getMessage()));
        }
    }

    @Override
    public String getCommandName() {
        return "get_block";
    }
}
