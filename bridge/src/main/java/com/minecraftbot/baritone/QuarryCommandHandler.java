package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;

/**
 * Handler for the quarry command - excavates area.
 */
public class QuarryCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "quarry";
    }

    @Override
    protected CommandResult execute(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        if (client.player == null) {
            return CommandResult.error("Player not available");
        }

        int size = params.has("size") ? params.get("size").getAsInt() : 10;
        int depth = params.has("depth") ? params.get("depth").getAsInt() : 5;

        BlockPos center = client.player.blockPosition();
        BlockPos corner1 = center.offset(size / 2, 0, size / 2);
        BlockPos corner2 = center.offset(-size / 2, -depth, -size / 2);

        executeOnMainThread(client, () -> 
            baritone.getBuilderProcess().clearArea(corner1, corner2));

        JsonObject data = new JsonObject();
        data.addProperty("started", true);
        data.addProperty("size", size);
        data.addProperty("depth", depth);
        data.addProperty("note", "Clearing area of size " + size + " and depth " + depth);
        return CommandResult.success(data);
    }
}
