package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;
import net.minecraft.util.Mth;

/**
 * Command handler for look: sets camera yaw/pitch directly.
 */
public class LookCommandHandler implements CommandHandler {

    @Override
    public CompletableFuture<CommandResult> handle(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        try {
            CommandResult result = client.submit(() -> {
                if (client.player == null) {
                    return CommandResult.error("Player not available");
                }

                float yaw = params.has("yaw") ? params.get("yaw").getAsFloat() : client.player.getYRot();
                float pitch = params.has("pitch") ? params.get("pitch").getAsFloat() : client.player.getXRot();
                pitch = Mth.clamp(pitch, -90.0f, 90.0f);

                client.player.setYRot(yaw);
                client.player.setXRot(pitch);

                JsonObject data = new JsonObject();
                data.addProperty("yaw", yaw);
                data.addProperty("pitch", pitch);
                return CommandResult.success(data);
            }).get();
            return CompletableFuture.completedFuture(result);
        } catch (Exception e) {
            return CompletableFuture.completedFuture(CommandResult.error("Look failed: " + e.getMessage()));
        }
    }

    @Override
    public String getCommandName() {
        return "look";
    }
}
