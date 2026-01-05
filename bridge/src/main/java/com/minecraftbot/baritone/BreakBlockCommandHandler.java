package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.util.math.BlockPos;

import java.net.Socket;

/**
 * Handler for the break_block command - breaks block at position using Baritone.
 */
public class BreakBlockCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "break_block";
    }

    @Override
    protected CommandResult execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        CommandResult validation = validateCoordinates(params);
        if (validation != null) {
            return validation;
        }

        BlockPos pos = getBlockPos(params);

        executeOnMainThread(client, () -> {
            // Use Baritone's builder process to break the block
            baritone.getBuilderProcess().clearArea(pos, pos);
        });

        JsonObject data = new JsonObject();
        data.addProperty("started", true);
        data.addProperty("x", pos.getX());
        data.addProperty("y", pos.getY());
        data.addProperty("z", pos.getZ());
        return CommandResult.success(data);
    }
}
