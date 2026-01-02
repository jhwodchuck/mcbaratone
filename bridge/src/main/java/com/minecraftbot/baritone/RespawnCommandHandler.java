package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.gui.screen.DeathScreen;
import java.net.Socket;

/**
 * Command handler for respawning the player.
 */
public class RespawnCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "respawn";
    }

    @Override
    protected CommandResult execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        executeOnMainThread(client, () -> {
            if (client.player != null) {
                client.player.requestRespawn();
            }
        });

        JsonObject data = new JsonObject();
        data.addProperty("requested", true);
        return CommandResult.success(data);
    }
}
