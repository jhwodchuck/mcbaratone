package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.util.Hand;
import net.minecraft.util.hit.BlockHitResult;
import net.minecraft.util.hit.HitResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Direction;
import net.minecraft.util.math.Vec3d;
import net.minecraft.world.RaycastContext;

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

                // Raycast from the player's eye position toward the target block center
                Vec3d start = new Vec3d(client.player.getX(), client.player.getEyeY(), client.player.getZ());
                Vec3d end = new Vec3d(x + 0.5, y + 0.5, z + 0.5);
                RaycastContext context = new RaycastContext(
                    start,
                    end,
                    RaycastContext.ShapeType.OUTLINE,
                    RaycastContext.FluidHandling.NONE,
                    client.player
                );
                BlockHitResult hitResult = client.world.raycast(context);
                if (hitResult.getType() == HitResult.Type.MISS || !hitResult.getBlockPos().equals(pos)) {
                    // Fallback to a direct hit on the target block if raycast misses
                    Vec3d hitPos = new Vec3d(x + 0.5, y + 0.5, z + 0.5);
                    hitResult = new BlockHitResult(hitPos, Direction.UP, pos, false);
                }

                var actionResult = client.interactionManager.interactBlock(client.player, hand, hitResult);
                client.player.swingHand(hand);

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

