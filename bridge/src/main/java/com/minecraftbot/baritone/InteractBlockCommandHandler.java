package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

/**
 * Command handler for interact_block: Simulates a right-click on a block.
 */
public class InteractBlockCommandHandler implements CommandHandler {

    @Override
    public CompletableFuture<CommandResult> handle(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        try {
            CommandResult result = client.submit(() -> {
                if (client.player == null || client.gameMode == null) {
                    return CommandResult.error("Player not available");
                }

                int x = params.get("x").getAsInt();
                int y = params.get("y").getAsInt();
                int z = params.get("z").getAsInt();
                String handStr = params.has("hand") ? params.get("hand").getAsString().toUpperCase() : "MAIN_HAND";

                InteractionHand hand = "OFF_HAND".equals(handStr) ? InteractionHand.OFF_HAND : InteractionHand.MAIN_HAND;
                BlockPos pos = new BlockPos(x, y, z);

                // Raycast from the player's eye position toward the target block center
                Vec3 start = new Vec3(client.player.getX(), client.player.getEyeY(), client.player.getZ());
                Vec3 end = new Vec3(x + 0.5, y + 0.5, z + 0.5);
                ClipContext context = new ClipContext(
                    start,
                    end,
                    ClipContext.Block.OUTLINE,
                    ClipContext.Fluid.NONE,
                    client.player
                );
                BlockHitResult hitResult = client.level.clip(context);
                if (hitResult.getType() == HitResult.Type.MISS || !hitResult.getBlockPos().equals(pos)) {
                    // Fallback to a direct hit on the target block if raycast misses
                    Vec3 hitPos = new Vec3(x + 0.5, y + 0.5, z + 0.5);
                    hitResult = new BlockHitResult(hitPos, Direction.UP, pos, false);
                }

                var actionResult = client.gameMode.useItemOn(client.player, hand, hitResult);
                client.player.swing(hand);

                JsonObject data = new JsonObject();
                data.addProperty("interacted", true);
                data.addProperty("x", x);
                data.addProperty("y", y);
                data.addProperty("z", z);
                data.addProperty("result", actionResult.toString());
                data.addProperty("hit_x", hitResult.getBlockPos().getX());
                data.addProperty("hit_y", hitResult.getBlockPos().getY());
                data.addProperty("hit_z", hitResult.getBlockPos().getZ());
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

