package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;

import java.net.Socket;
import java.util.concurrent.CompletableFuture;

public class TunnelWideCommandHandler extends AsyncCommandHandler {

    @Override
    public String getCommandName() {
        return "tunnel_wide";
    }

    @Override
    public CompletableFuture<CommandResult> execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        if (!params.has("x") || !params.has("z")) {
            return CompletableFuture.completedFuture(CommandResult.error("Missing x or z coordinates"));
        }

        int x = params.get("x").getAsInt();
        int z = params.get("z").getAsInt();
        int width = params.has("width") ? params.get("width").getAsInt() : 3;
        int height = params.has("height") ? params.get("height").getAsInt() : 2;
        
        return executeOnMainThread(client, () -> {
            if (client.player == null) {
                return CommandResult.error("Player not available");
            }

            double dist = Math.sqrt(client.player.squaredDistanceTo(x, client.player.getY(), z));
            int depth = (int) Math.ceil(dist);

            String cmd = "#tunnel " + height + " " + width + " " + depth;
            
            client.player.networkHandler.sendChatMessage("#look at " + x + " " + client.player.getY() + " " + z);
            client.player.networkHandler.sendChatMessage(cmd);

            JsonObject data = new JsonObject();
            data.addProperty("started", true);
            data.addProperty("command", cmd);
            
            return CommandResult.success(data);
        });
    }
}
