package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import net.minecraft.client.Minecraft;

/**
 * Handler for the stop command - stops all Baritone processes.
 */
public class StopCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "stop";
    }

    @Override
    protected CommandResult execute(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        executeOnMainThread(client, () -> baritone.getPathingBehavior().cancelEverything());

        JsonObject data = new JsonObject();
        data.addProperty("stopped", true);
        return CommandResult.success(data);
    }
}
