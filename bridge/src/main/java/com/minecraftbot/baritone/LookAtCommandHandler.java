package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.util.math.MathHelper;

import java.net.Socket;
import java.util.concurrent.CompletableFuture;

/**
 * Command handler for look_at: Points the camera at specific coordinates.
 */
public class LookAtCommandHandler implements CommandHandler {

    @Override
    public CompletableFuture<CommandResult> handle(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        try {
            CommandResult result = client.submit(() -> {
                if (client.player == null) {
                    return CommandResult.error("Player not available");
                }

                double tx = params.get("x").getAsDouble();
                double ty = params.get("y").getAsDouble();
                double tz = params.get("z").getAsDouble();

                double dx = tx - client.player.getX();
                double dy = ty - (client.player.getY() + client.player.getEyeHeight(client.player.getPose()));
                double dz = tz - client.player.getZ();

                double dist = Math.sqrt(dx * dx + dz * dz);
                float yaw = (float) (MathHelper.atan2(dz, dx) * (180.0 / Math.PI)) - 90.0f;
                float pitch = (float) -(MathHelper.atan2(dy, dist) * (180.0 / Math.PI));

                client.player.setYaw(yaw);
                client.player.setPitch(pitch);

                JsonObject data = new JsonObject();
                data.addProperty("yaw", yaw);
                data.addProperty("pitch", pitch);
                return CommandResult.success(data);
            }).get();
            return CompletableFuture.completedFuture(result);
        } catch (Exception e) {
            return CompletableFuture.completedFuture(CommandResult.error("Look at failed: " + e.getMessage()));
        }
    }

    @Override
    public String getCommandName() {
        return "look_at";
    }
}
