package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.util.Hand;
import net.minecraft.util.hit.BlockHitResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Direction;
import net.minecraft.util.math.Vec3d;

import java.net.Socket;
import java.util.concurrent.CompletableFuture;

/**
 * Command handler for interact_block: Simulates a right-click on a block.
 */
public class InteractBlockCommandHandler implements CommandHandler {

    @Override
    public CompletableFuture<CommandResult> handle(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        try {
            CommandResult result = client.submit(() -> {
                if (client.player == null || client.interactionManager == null) {
                    return CommandResult.error("Player not available");
                }

                int x = params.get("x").getAsInt();
                int y = params.get("y").getAsInt();
                int z = params.get("z").getAsInt();
                String handStr = params.has("hand") ? params.get("hand").getAsString().toUpperCase() : "MAIN_HAND";

                Hand hand = "OFF_HAND".equals(handStr) ? Hand.OFF_HAND : Hand.MAIN_HAND;
                BlockPos pos = new BlockPos(x, y, z);

                // Create a hit result pointing at the center-top of the block
                Vec3d hitPos = new Vec3d(x + 0.5, y + 1.0, z + 0.5);
                BlockHitResult hitResult = new BlockHitResult(hitPos, Direction.UP, pos, false);

                client.interactionManager.interactBlock(client.player, hand, hitResult);

                JsonObject data = new JsonObject();
                data.addProperty("interacted", true);
                data.addProperty("x", x);
                data.addProperty("y", y);
                data.addProperty("z", z);
                return CommandResult.success(data);
            }).get();
            return CompletableFuture.completedFuture(result);
        } catch (Exception e) {
            return CompletableFuture.completedFuture(CommandResult.error("Interact failed: " + e.getMessage()));
        }
    }

    @Override
    public String getCommandName() {
        return "interact_block";
    }
}

