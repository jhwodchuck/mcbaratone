package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import net.minecraft.client.Minecraft;

/**
 * Command handler for respawning the player.
 */
public class RespawnCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "respawn";
    }

    @Override
    protected CommandResult execute(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        executeOnMainThread(client, () -> {
            if (client.player != null) {
                client.player.respawn();
            }
        });

        JsonObject data = new JsonObject();
        data.addProperty("requested", true);
        return CommandResult.success(data);
    }
}
