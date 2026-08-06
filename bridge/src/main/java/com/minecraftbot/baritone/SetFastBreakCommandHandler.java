package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import net.minecraft.client.Minecraft;

/** Configure client-side mining acceleration for subsequent mining actions. */
public class SetFastBreakCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "set_fast_break";
    }

    @Override
    public boolean requiresPlayer() {
        return false;
    }

    @Override
    public boolean isOfflineSafe() {
        return true;
    }

    @Override
    protected CommandResult execute(
        JsonObject params,
        Minecraft client,
        IBaritone baritone,
        Socket clientSocket
    ) {
        if (!params.has("mode")) {
            return CommandResult.error("Missing mode (expected off, legit, or aggressive)");
        }

        final FastBreakMode mode;
        try {
            mode = FastBreakMode.parse(params.get("mode").getAsString());
        } catch (IllegalArgumentException exception) {
            return CommandResult.error(exception.getMessage());
        }

        ManualMiningController.getInstance().configureMode(mode);

        JsonObject data = new JsonObject();
        data.addProperty("mode", mode.wireName());
        data.addProperty("no_break_delay", mode != FastBreakMode.OFF);
        data.addProperty("early_stop", mode == FastBreakMode.AGGRESSIVE);
        return CommandResult.success(data);
    }
}
