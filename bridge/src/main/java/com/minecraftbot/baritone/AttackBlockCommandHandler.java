package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;

public class AttackBlockCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "attack_block";
    }

    @Override
    protected CommandResult execute(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        if (!params.has("x") || !params.has("y") || !params.has("z")) {
            return CommandResult.error("Missing coordinates");
        }

        int x = params.get("x").getAsInt();
        int y = params.get("y").getAsInt();
        int z = params.get("z").getAsInt();

        if (client.gameMode == null) {
            return CommandResult.error("Interaction manager not ready");
        }
        
        // Ensure run on main thread
        client.execute(() -> {
            BlockPos pos = new BlockPos(x, y, z);
            // Attack the block (Left Click)
            client.gameMode.startDestroyBlock(pos, Direction.UP);
            client.player.swing(client.player.getUsedItemHand());
        });

        return CommandResult.success();
    }
}
