package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;

public class TunnelWideCommandHandler extends AsyncCommandHandler {

    @Override
    public String getCommandName() {
        return "tunnel_wide";
    }

    @Override
    public CompletableFuture<CommandResult> execute(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
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

            double dist = Math.sqrt(client.player.distanceToSqr(x, client.player.getY(), z));
            int depth = (int) Math.ceil(dist);

            String cmd = "#tunnel " + height + " " + width + " " + depth;
            
            client.player.connection.sendChat("#look at " + x + " " + client.player.getY() + " " + z);
            client.player.connection.sendChat(cmd);

            JsonObject data = new JsonObject();
            data.addProperty("started", true);
            data.addProperty("command", cmd);
            
            return CommandResult.success(data);
        });
    }
}
