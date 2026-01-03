package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Direction;
import java.net.Socket;

public class AttackBlockCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "attack_block";
    }

    @Override
    protected CommandResult execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        if (!params.has("x") || !params.has("y") || !params.has("z")) {
            return CommandResult.error("Missing coordinates");
        }

        int x = params.get("x").getAsInt();
        int y = params.get("y").getAsInt();
        int z = params.get("z").getAsInt();

        if (client.interactionManager == null) {
            return CommandResult.error("Interaction manager not ready");
        }
        
        // Ensure run on main thread
        client.execute(() -> {
            BlockPos pos = new BlockPos(x, y, z);
            // Attack the block (Left Click)
            client.interactionManager.attackBlock(pos, Direction.UP);
            client.player.swingHand(client.player.getActiveHand());
        });

        return CommandResult.success();
    }
}
