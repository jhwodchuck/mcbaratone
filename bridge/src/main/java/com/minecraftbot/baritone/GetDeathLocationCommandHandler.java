package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;

import java.net.Socket;

/**
 * Handler for the get_death_location command - returns last death coordinates.
 */
public class GetDeathLocationCommandHandler extends AbstractCommandHandler {

    // These are set by BaritoneAPIBridge when tracking death
    private double lastDeathX;
    private double lastDeathY;
    private double lastDeathZ;
    private String lastDeathDimension = "minecraft:overworld";
    private long lastDeathTime;

    @Override
    public String getCommandName() {
        return "get_death_location";
    }

    /**
     * Update the death location. Called by BaritoneAPIBridge when player dies.
     */
    public void updateDeathLocation(double x, double y, double z, String dimension, long timestamp) {
        this.lastDeathX = x;
        this.lastDeathY = y;
        this.lastDeathZ = z;
        this.lastDeathDimension = dimension;
        this.lastDeathTime = timestamp;
    }

    @Override
    protected CommandResult execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        JsonObject data = new JsonObject();

        if (lastDeathTime == 0) {
            data.addProperty("has_death_location", false);
        } else {
            data.addProperty("has_death_location", true);
            data.addProperty("x", lastDeathX);
            data.addProperty("y", lastDeathY);
            data.addProperty("z", lastDeathZ);
            data.addProperty("dimension", lastDeathDimension);
            data.addProperty("timestamp", lastDeathTime);
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
