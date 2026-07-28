package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import net.minecraft.client.Minecraft;

/**
 * Handler for the axis command - mines along an axis.
 */
public class AxisMineCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "axis";
    }

    @Override
    protected CommandResult execute(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        if (client.player == null) {
            return CommandResult.error("Player not available");
        }

        executeOnMainThread(client, () -> 
            client.player.connection.sendChat("#axis"));

        JsonObject data = new JsonObject();
        data.addProperty("started", true);
        return CommandResult.success(data);
    }
}
