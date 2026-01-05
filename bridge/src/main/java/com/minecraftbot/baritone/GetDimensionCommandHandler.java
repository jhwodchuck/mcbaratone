package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;

import java.net.Socket;

/**
 * Handler for the get_dimension command - returns current dimension.
 */
public class GetDimensionCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "get_dimension";
    }

    @Override
    protected CommandResult execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        if (client.world == null) {
            return CommandResult.error("World not available");
        }

        JsonObject data = new JsonObject();
        data.addProperty("dimension", client.world.getRegistryKey().getValue().toString());
        return CommandResult.success(data);
    }
}
