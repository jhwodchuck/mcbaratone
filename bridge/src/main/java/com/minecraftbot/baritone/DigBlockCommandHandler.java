package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;

import java.net.Socket;

/**
 * Handler for the dig_block command - progressive survival-mode block breaking
 * that bypasses Baritone's underfoot-mining refusal.
 *
 * Unlike break_block (Baritone builder process) and attack_block (a single
 * discrete swing), this hands the target to {@link ManualMiningController},
 * which advances the break each client tick like a held left-click until the
 * block is gone or the tick budget expires. Returns immediately; the caller
 * polls get_block to observe the block turning to air.
 */
public class DigBlockCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "dig_block";
    }

    @Override
    protected CommandResult execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        if (!params.has("x") || !params.has("y") || !params.has("z")) {
            return CommandResult.error("Missing coordinates");
        }

        int x = params.get("x").getAsInt();
        int y = params.get("y").getAsInt();
        int z = params.get("z").getAsInt();
        String face = params.has("face") ? params.get("face").getAsString() : "UP";
        // Default ~10s at 20 tps; a single block breaks far sooner, this is a
        // safety ceiling so a stuck target never mines indefinitely.
        int maxTicks = params.has("max_ticks") ? params.get("max_ticks").getAsInt() : 200;

        ManualMiningController.getInstance().startMining(x, y, z, face, maxTicks);

        JsonObject data = new JsonObject();
        data.addProperty("started", true);
        data.addProperty("x", x);
        data.addProperty("y", y);
        data.addProperty("z", z);
        return CommandResult.success(data);
    }
}
