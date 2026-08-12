package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import net.minecraft.client.Minecraft;

/**
 * Handler for the get_death_location command - returns last death coordinates.
 */
public class GetDeathLocationCommandHandler extends AbstractCommandHandler {

    private final DeathTracker deathTracker;

    public GetDeathLocationCommandHandler() {
        this(new DeathTracker());
    }

    public GetDeathLocationCommandHandler(DeathTracker deathTracker) {
        this.deathTracker = deathTracker;
    }

    @Override
    public String getCommandName() {
        return "get_death_location";
    }

    @Override
    protected CommandResult execute(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        JsonObject data = new JsonObject();

        DeathTracker.DeathSnapshot death = deathTracker.getLastDeath();
        if (death == null) {
            data.addProperty("has_death_location", false);
        } else {
            data.addProperty("has_death_location", true);
            data.addProperty("death_id", death.deathId());
            data.addProperty("x", death.x());
            data.addProperty("y", death.y());
            data.addProperty("z", death.z());
            data.addProperty("dimension", death.dimension());
            data.addProperty("game_tick", death.gameTick());
            data.addProperty("timestamp", death.timestampMs());
        }

        return CommandResult.success(data);
    }

    @Override
    public boolean requiresPlayer() {
        return false;
    }

    @Override
    public boolean isOfflineSafe() {
        return true;
    }
}
