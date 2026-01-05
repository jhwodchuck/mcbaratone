package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import baritone.api.pathing.goals.GoalBlock;
import baritone.api.pathing.goals.GoalNear;
import baritone.api.utils.BlockOptionalMeta;
import baritone.api.utils.BlockOptionalMetaLookup;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.util.math.BlockPos;

import java.net.Socket;

/**
 * Handler for the tunnel command - starts tunnel mining.
 */
public class TunnelCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "tunnel";
    }

    @Override
    protected CommandResult execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        if (client.player == null) {
            return CommandResult.error("Player not available");
        }

        int x = params.has("x") ? params.get("x").getAsInt() : 0;
        int y = params.has("y") ? params.get("y").getAsInt() : 64;
        int z = params.has("z") ? params.get("z").getAsInt() : 0;
        int radius = params.has("radius") ? params.get("radius").getAsInt() : 1;

        JsonObject data = new JsonObject();

        try {
            // Set up mining for common tunnel blocks
            BlockOptionalMetaLookup blocksToMine = new BlockOptionalMetaLookup(
                new BlockOptionalMeta("stone"),
                new BlockOptionalMeta("cobblestone"),
                new BlockOptionalMeta("dirt"),
                new BlockOptionalMeta("gravel"),
                new BlockOptionalMeta("andesite"),
                new BlockOptionalMeta("diorite"),
                new BlockOptionalMeta("granite")
            );

            // Start mining these blocks
            executeOnMainThread(client, () -> baritone.getMineProcess().mine(0, blocksToMine));

            // Set goal to tunnel destination
            BlockPos targetPos = new BlockPos(x, y, z);
            executeOnMainThread(client, () -> {
                if (radius > 1) {
                    baritone.getCustomGoalProcess().setGoalAndPath(new GoalNear(targetPos, radius));
                } else {
                    baritone.getCustomGoalProcess().setGoalAndPath(new GoalBlock(targetPos));
                }
            });

            data.addProperty("started", true);
            data.addProperty("target_x", x);
            data.addProperty("target_y", y);
            data.addProperty("target_z", z);
            data.addProperty("radius", radius);
            data.addProperty("note", "Tunnel started - mining common blocks while pathing to target");

        } catch (Exception e) {
            logger.error("Error starting tunnel", e);
            // Fallback to chat command
            String tunnelCommand = "#tunnel";
            if (params.has("width")) {
                tunnelCommand += " " + params.get("width").getAsInt();
            }
            final String finalCmd = tunnelCommand;
            executeOnMainThread(client, () -> client.player.networkHandler.sendChatMessage(finalCmd));
            data.addProperty("sent", true);
            data.addProperty("command", tunnelCommand);
            data.addProperty("note", "Using chat command fallback due to error: " + e.getMessage());
        }

        return CommandResult.success(data);
    }
}
