package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.block.BlockState;
import net.minecraft.client.MinecraftClient;
import net.minecraft.registry.Registries;
import net.minecraft.util.math.BlockPos;

import java.net.Socket;
import java.util.concurrent.CompletableFuture;

public class GetBlockCommandHandler implements CommandHandler {

    @Override
    public CompletableFuture<CommandResult> handle(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        if (client.world == null) {
            return CompletableFuture.completedFuture(CommandResult.error("World not available"));
        }

        try {
            int x = params.get("x").getAsInt();
            int y = params.get("y").getAsInt();
            int z = params.get("z").getAsInt();
            BlockPos pos = new BlockPos(x, y, z);
            
            CommandResult result = client.submit(() -> {
                BlockState state = client.world.getBlockState(pos);
                JsonObject data = new JsonObject();
                String id = "";
                if (state != null && state.getBlock() != null) {
                    id = Registries.BLOCK.getId(state.getBlock()).toString();
                    
                    // Add state properties
                    JsonObject properties = new JsonObject();
                    state.getEntries().forEach((property, value) -> {
                        properties.addProperty(property.getName(), value.toString());
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
