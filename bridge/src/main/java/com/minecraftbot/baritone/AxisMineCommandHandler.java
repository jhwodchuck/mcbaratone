package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;

import java.net.Socket;

/**
 * Handler for the axis command - mines along an axis.
 */
public class AxisMineCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "axis";
    }

    @Override
    protected CommandResult execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        if (client.player == null) {
            return CommandResult.error("Player not available");
        }

        executeOnMainThread(client, () -> 
            client.player.networkHandler.sendChatMessage("#axis"));

        JsonObject data = new JsonObject();
        data.addProperty("started", true);
        return CommandResult.success(data);
    }
}
