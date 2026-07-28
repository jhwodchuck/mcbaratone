package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import net.minecraft.client.Minecraft;

/**
 * Handler for the get_dimension command - returns current dimension.
 */
public class GetDimensionCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "get_dimension";
    }

    @Override
    protected CommandResult execute(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        if (client.level == null) {
            return CommandResult.error("World not available");
        }

        JsonObject data = new JsonObject();
        data.addProperty("dimension", client.level.dimension().identifier().toString());
        return CommandResult.success(data);
    }
}
