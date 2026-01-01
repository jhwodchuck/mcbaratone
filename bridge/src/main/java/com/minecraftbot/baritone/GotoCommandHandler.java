package com.minecraftbot.baritone;

import baritone.api.BaritoneAPI;
import baritone.api.IBaritone;
import baritone.api.pathing.goals.GoalBlock;
import baritone.api.pathing.goals.GoalNear;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.util.math.BlockPos;

import java.net.Socket;

/**
 * Command handler for movement commands: goto, come, follow.
 */
public class GotoCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "goto"; // Primary command, but handles multiple
    }

    @Override
    protected CommandResult execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        String action = params.has("action") ? params.get("action").getAsString() : "goto";

        switch (action) {
            case "goto":
                return handleGoto(params, baritone);
            case "come":
                return handleCome(params, client, baritone);
            case "follow":
                return handleFollow(params, baritone);
            default:
                return CommandResult.error("Unknown goto action: " + action);
        }
    }

    private CommandResult handleGoto(JsonObject params, IBaritone baritone) {
        CommandResult validation = validateCoordinates(params);
        if (validation != null) {
            return validation;
        }

        int x = params.get("x").getAsInt();
        int y = params.get("y").getAsInt();
        int z = params.get("z").getAsInt();
        int radius = params.has("radius") ? params.get("radius").getAsInt() : 0;

        executeOnMainThread(MinecraftClient.getInstance(), () -> {
            if (radius > 0) {
                baritone.getCustomGoalProcess().setGoalAndPath(new GoalNear(new BlockPos(x, y, z), radius));
            } else {
                baritone.getCustomGoalProcess().setGoalAndPath(new GoalBlock(x, y, z));
            }
        });

        JsonObject data = new JsonObject();
        data.addProperty("started", true);
        data.addProperty("x", x);
        data.addProperty("y", y);
        data.addProperty("z", z);
        data.addProperty("radius", radius);
        return CommandResult.success(data);
    }

    private CommandResult handleCome(JsonObject params, MinecraftClient client, IBaritone baritone) {
        if (client.player == null) {
            return CommandResult.error("Player not available");
        }

        try {
            BlockPos targetPos = client.player.getBlockPos();

            executeOnMainThread(client, () -> {
                baritone.getCustomGoalProcess().setGoalAndPath(new GoalBlock(targetPos));
            });

            JsonObject data = new JsonObject();
            data.addProperty("started", true);
            data.addProperty("target_x", targetPos.getX());
            data.addProperty("target_y", targetPos.getY());
            data.addProperty("target_z", targetPos.getZ());
            return CommandResult.success(data);
        } catch (Exception e) {
            logger.error("Error executing come command", e);
            return CommandResult.error("Failed to execute come command: " + e.getMessage());
        }
    }

    private CommandResult handleFollow(JsonObject params, IBaritone baritone) {
        String entity = params.has("entity") ? params.get("entity").getAsString() : "player";

        executeOnMainThread(MinecraftClient.getInstance(), () ->
            MinecraftClient.getInstance().player.networkHandler.sendChatMessage("#follow " + entity));

        JsonObject data = new JsonObject();
        data.addProperty("sent", true);
        data.addProperty("entity", entity);
        return CommandResult.success(data);
    }
}