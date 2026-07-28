package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import net.minecraft.client.Minecraft;

/**
 * Handler for the strip command - strip mining operation.
 */
public class StripMineCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "strip";
    }

    @Override
    protected CommandResult execute(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        if (client.player == null) {
            return CommandResult.error("Player not available");
        }

        executeOnMainThread(client, () -> 
            client.player.connection.sendChat("#strip"));

        JsonObject data = new JsonObject();
        data.addProperty("started", true);
        return CommandResult.success(data);
    }
}
