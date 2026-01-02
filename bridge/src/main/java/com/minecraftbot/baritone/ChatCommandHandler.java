package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import java.net.Socket;

public class ChatCommandHandler implements CommandHandler {
    @Override
    public CommandResult handle(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        if (client.player == null) return CommandResult.error("Player not available");
        
        try {
            String message = params.get("message").getAsString();
            client.submit(() -> {
                client.player.networkHandler.sendChatMessage(message);
                return null;
            }).get();
            return CommandResult.success();
        } catch (Exception e) {
            return CommandResult.error("Chat failed: " + e.getMessage());
        }
    }
    @Override
    public String getCommandName() { return "chat"; }
}
