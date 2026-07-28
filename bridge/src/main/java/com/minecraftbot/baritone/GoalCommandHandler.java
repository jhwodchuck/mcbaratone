package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import baritone.api.pathing.goals.GoalYLevel;
import com.google.gson.JsonObject;
import java.net.Socket;
import net.minecraft.client.Minecraft;

/**
 * Handler for the goal command - sets pathfinding goal.
 */
public class GoalCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "goal";
    }

    @Override
    protected CommandResult execute(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        String type = params.has("type") ? params.get("type").getAsString() : "yLevel";
        int value = params.has("value") ? params.get("value").getAsInt() : 64;

        JsonObject data = new JsonObject();

        if ("yLevel".equals(type)) {
            executeOnMainThread(client, () -> 
                baritone.getCustomGoalProcess().setGoalAndPath(new GoalYLevel(value)));
            data.addProperty("started", true);
            data.addProperty("type", type);
            data.addProperty("value", value);
        } else {
            return CommandResult.error("Goal type not supported: " + type);
        }

        return CommandResult.success(data);
    }
}
