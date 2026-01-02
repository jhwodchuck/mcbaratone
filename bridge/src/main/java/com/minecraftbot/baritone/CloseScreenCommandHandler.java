package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;

import java.net.Socket;

/**
 * Command handler for closing the current screen.
 */
public class CloseScreenCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "close_screen";
    }

    @Override
    protected CommandResult execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        if (client.player == null) {
            return CommandResult.error("Player not available");
        }

        client.execute(() -> {
            client.player.closeHandledScreen();
        });

        return CommandResult.success();
    }
}
