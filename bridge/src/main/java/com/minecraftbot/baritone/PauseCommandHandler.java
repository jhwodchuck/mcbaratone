package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;

import java.net.Socket;

/**
 * Handler for the pause command - pauses current Baritone process.
 */
public class PauseCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "pause";
    }

    @Override
    protected CommandResult execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        executeOnMainThread(client, () -> baritone.getBuilderProcess().pause());

        JsonObject data = new JsonObject();
        data.addProperty("paused", true);
        return CommandResult.success(data);
    }
}
