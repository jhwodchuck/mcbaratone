package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import baritone.api.BaritoneAPI;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.util.math.BlockPos;

import java.net.Socket;
import java.util.concurrent.CompletableFuture;

/**
 * Handler for the farm command - starts farming process.
 */
public class FarmCommandHandler extends AsyncCommandHandler {

    @Override
    public String getCommandName() {
        return "farm";
    }

    @Override
    public CompletableFuture<CommandResult> execute(
            JsonObject params, MinecraftClient client, IBaritone baritone,
            Socket clientSocket) {
        if (params.has("crop")) {
            return CompletableFuture.completedFuture(CommandResult.error(
                "crop filtering is not supported by Baritone's farm process"));
        }
        int range = params.has("range") ? params.get("range").getAsInt() : 0;
        if (range < 0 || range > 512) {
            return CompletableFuture.completedFuture(
                CommandResult.error("range must be between 0 and 512"));
        }
        boolean anyCoordinate = params.has("x") || params.has("y") || params.has("z");
        boolean allCoordinates = params.has("x") && params.has("y") && params.has("z");
        if (anyCoordinate && !allCoordinates) {
            return CompletableFuture.completedFuture(CommandResult.error(
                "x, y, and z must be supplied together"));
        }
        BlockPos center = allCoordinates ? getBlockPos(params) : null;
        Boolean replant = params.has("replant")
            ? params.get("replant").getAsBoolean() : null;

        return executeOnMainThread(client, () -> {
            if (replant != null) {
                BaritoneAPI.getSettings().replantCrops.value = replant;
                BaritoneAPI.getSettings().replantNetherWart.value = replant;
            }
            if (center != null) {
                baritone.getFarmProcess().farm(range, center);
            } else {
                baritone.getFarmProcess().farm(range);
            }

            JsonObject data = new JsonObject();
            data.addProperty("started", true);
            data.addProperty("range", range);
            data.addProperty("replant", BaritoneAPI.getSettings().replantCrops.value);
            data.addProperty("crop_filter_supported", false);
            if (center != null) {
                JsonObject position = new JsonObject();
                position.addProperty("x", center.getX());
                position.addProperty("y", center.getY());
                position.addProperty("z", center.getZ());
                data.add("center", position);
            }
            return CommandResult.success(data);
        });
    }
}
