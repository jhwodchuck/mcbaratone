package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;

import java.net.Socket;

/**
 * Handler for the explore command - starts exploration at given coordinates.
 */
public class ExploreCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "explore";
    }

    @Override
    protected CommandResult execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        int x = params.has("x") ? params.get("x").getAsInt() : 0;
        int z = params.has("z") ? params.get("z").getAsInt() : 0;

        executeOnMainThread(client, () -> baritone.getExploreProcess().explore(x, z));

        JsonObject data = new JsonObject();
        data.addProperty("started", true);
        data.addProperty("x", x);
        data.addProperty("z", z);
        return CommandResult.success(data);
    }
}
