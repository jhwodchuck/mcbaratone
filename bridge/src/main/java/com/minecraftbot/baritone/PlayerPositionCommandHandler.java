package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import net.minecraft.client.Minecraft;

/** Lightweight position-only state query for callers that do not need a full snapshot. */
public class PlayerPositionCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "get_player_pos";
    }

    @Override
    protected CommandResult execute(JsonObject params, Minecraft client,
            IBaritone baritone, Socket clientSocket) {
        if (client.player == null) {
            return CommandResult.error("Player not available");
        }
        JsonObject position = new JsonObject();
        position.addProperty("x", client.player.getX());
        position.addProperty("y", client.player.getY());
        position.addProperty("z", client.player.getZ());
        position.addProperty("yaw", client.player.getYRot());
        position.addProperty("pitch", client.player.getXRot());

        JsonObject data = new JsonObject();
        data.add("position", position);
        data.addProperty("x", client.player.getX());
        data.addProperty("y", client.player.getY());
        data.addProperty("z", client.player.getZ());
        return CommandResult.success(data);
    }
}
