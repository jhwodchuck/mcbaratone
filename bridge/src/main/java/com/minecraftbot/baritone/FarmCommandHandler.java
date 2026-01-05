package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;

import java.net.Socket;

/**
 * Handler for the farm command - starts farming process.
 */
public class FarmCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "farm";
    }

    @Override
    protected CommandResult execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        int range = params.has("range") ? params.get("range").getAsInt() : 0;

        executeOnMainThread(client, () -> baritone.getFarmProcess().farm(range));

        JsonObject data = new JsonObject();
        data.addProperty("started", true);
        data.addProperty("range", range);
        return CommandResult.success(data);
    }
}
