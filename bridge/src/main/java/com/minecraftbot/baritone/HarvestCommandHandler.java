package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;

import java.net.Socket;
import java.util.concurrent.CompletableFuture;

public class HarvestCommandHandler extends AsyncCommandHandler {

    @Override
    public String getCommandName() {
        return "harvest";
    }

    @Override
    public CompletableFuture<CommandResult> execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        return executeOnMainThread(client, () -> {
            client.player.networkHandler.sendChatMessage("#farm");
            
            JsonObject data = new JsonObject();
            data.addProperty("started", true);
            data.addProperty("note", "Harvest command executed via #farm");
            
            return CommandResult.success(data);
        });
    }
}
