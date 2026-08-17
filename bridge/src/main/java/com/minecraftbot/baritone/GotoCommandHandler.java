package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import baritone.api.pathing.goals.GoalBlock;
import baritone.api.pathing.goals.GoalNear;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;

/**
 * Command handler for movement commands: goto, come, follow.
 */
public class GotoCommandHandler extends AsyncCommandHandler {

    @Override
    public String getCommandName() {
        return "goto"; // Primary command, but handles multiple
    }

    @Override
    public CompletableFuture<CommandResult> execute(
            JsonObject params,
            Minecraft client,
            IBaritone baritone,
            Socket clientSocket) {
        String action = params.has("action") ? params.get("action").getAsString() : "goto";

        switch (action) {
            case "goto":
                return handleGoto(params, client, baritone);
            case "come":
                return handleCome(params, client, baritone);
            case "follow":
                return handleFollow(params, client, baritone);
            default:
                return CompletableFuture.completedFuture(
                    CommandResult.error("Unknown goto action: " + action));
        }
    }

    private CompletableFuture<CommandResult> handleGoto(
            JsonObject params, Minecraft client, IBaritone baritone) {
        CommandResult validation = validateCoordinates(params);
        if (validation != null) {
            return CompletableFuture.completedFuture(validation);
        }

        int x = params.get("x").getAsInt();
        int y = params.get("y").getAsInt();
        int z = params.get("z").getAsInt();
        int radius = params.has("radius") ? params.get("radius").getAsInt() : 0;

        return executeOnMainThread(client, () -> {
            if (radius > 0) {
                baritone.getCustomGoalProcess().setGoalAndPath(new GoalNear(new BlockPos(x, y, z), radius));
            } else {
                baritone.getCustomGoalProcess().setGoalAndPath(new GoalBlock(x, y, z));
            }

            JsonObject data = appliedData(client, baritone);
            data.addProperty("x", x);
            data.addProperty("y", y);
            data.addProperty("z", z);
            data.addProperty("radius", radius);
            return CommandResult.success(data);
        });
    }

    private CompletableFuture<CommandResult> handleCome(
            JsonObject params, Minecraft client, IBaritone baritone) {
        return executeOnMainThread(client, () -> {
            if (client.player == null) {
                return CommandResult.error("Player not available");
            }
            BlockPos targetPos = client.player.blockPosition();
            baritone.getCustomGoalProcess().setGoalAndPath(new GoalBlock(targetPos));
            JsonObject data = appliedData(client, baritone);
            data.addProperty("target_x", targetPos.getX());
            data.addProperty("target_y", targetPos.getY());
            data.addProperty("target_z", targetPos.getZ());
            return CommandResult.success(data);
        });
    }

    private CompletableFuture<CommandResult> handleFollow(
            JsonObject params, Minecraft client, IBaritone baritone) {
        String entity = params.has("entity") ? params.get("entity").getAsString() : "player";
        return executeOnMainThread(client, () -> {
            if (client.player == null) {
                return CommandResult.error("Player not available");
            }
            client.player.connection.sendChat("#follow " + entity);
            JsonObject data = appliedData(client);
            data.addProperty("sent", true);
            data.addProperty("entity", entity);
            return CommandResult.success(data);
        });
    }

    private JsonObject appliedData(Minecraft client) {
        return appliedData(client, null);
    }

    /**
     * Report that the goal was set, and -- when a Baritone handle is available
     * -- whether a route to it actually exists yet.
     *
     * <p>"started"/"accepted"/"applied" only ever meant "the goal was handed to
     * Baritone". They are true even when no path can be found, which made a
     * wedged bot indistinguishable from a walking one over the wire: A1Bot
     * spent 15+ hours frozen while every goto returned started=true. The goal
     * really was applied, so those flags stay as they are for compatibility;
     * "path_available" is the field that says whether anything will come of it.
     * Callers should treat a persistent path_available=false as a refusal
     * rather than waiting out a movement timeout.
     */
    private JsonObject appliedData(Minecraft client, IBaritone baritone) {
        JsonObject data = new JsonObject();
        data.addProperty("started", true);
        data.addProperty("accepted", true);
        data.addProperty("applied", true);
        if (client.level != null) {
            data.addProperty("applied_tick", client.level.getGameTime());
        }
        if (baritone != null) {
            try {
                data.addProperty(
                    "path_available", baritone.getPathingBehavior().getPath() != null);
                data.addProperty(
                    "is_pathing", baritone.getPathingBehavior().isPathing());
            } catch (Exception e) {
                // A pathing handle that cannot be read is not a reason to fail
                // a goal that was genuinely set.
                data.addProperty("path_available", false);
            }
        }
        return data;
    }
}
