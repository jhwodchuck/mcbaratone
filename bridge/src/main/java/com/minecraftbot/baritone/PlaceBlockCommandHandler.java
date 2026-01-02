package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.block.BlockState;
import net.minecraft.client.MinecraftClient;
import net.minecraft.util.ActionResult;
import net.minecraft.util.Hand;
import net.minecraft.util.hit.BlockHitResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Direction;
import net.minecraft.util.math.Vec3d;

import java.net.Socket;
import java.util.concurrent.ExecutionException;

public class PlaceBlockCommandHandler implements CommandHandler {

    @Override
    public CommandResult handle(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        if (client.player == null || client.world == null) {
            return CommandResult.error("Player/World not available");
        }

        try {
            int x = params.get("x").getAsInt();
            int y = params.get("y").getAsInt();
            int z = params.get("z").getAsInt();
            
            BlockPos targetPos = new BlockPos(x, y, z);
            
            return client.submit(() -> {
                BlockState currentTargetState = client.world.getBlockState(targetPos);
                if (!currentTargetState.isReplaceable()) {
                    return CommandResult.error("Target position is already occupied: " + targetPos);
                }

                // Logic to find neighbor
                Direction placeFace = Direction.UP;
                BlockPos placeAgainst = targetPos.down();
                boolean foundNeighbor = false;
                
                for (Direction dir : Direction.values()) {
                    BlockPos adjacent = targetPos.offset(dir);
                    BlockState adjacentState = client.world.getBlockState(adjacent);
                    if (adjacentState.isReplaceable()) continue;

                    placeAgainst = adjacent;
                    placeFace = dir.getOpposite();
                    foundNeighbor = true;
                    break;
                }
                
                if (!foundNeighbor && client.world.getBlockState(targetPos.down()).isReplaceable()) {
                    return CommandResult.error("No solid block found to place against at " + targetPos);
                }
                
                Vec3d hitPos = Vec3d.ofCenter(placeAgainst).add(Vec3d.of(placeFace.getVector()).multiply(0.5d));
                
                // Look at hitPos
                double dx = hitPos.x - client.player.getX();
                double dy = hitPos.y - client.player.getEyeY();
                double dz = hitPos.z - client.player.getZ();
                double horizontalDist = Math.sqrt(dx * dx + dz * dz);
                float yaw = (float) Math.toDegrees(Math.atan2(-dx, dz));
                float pitch = (float) Math.toDegrees(-Math.atan2(dy, horizontalDist));
                
                client.player.setYaw(yaw);
                client.player.setPitch(pitch);
                
                BlockHitResult hitResult = new BlockHitResult(hitPos, placeFace, placeAgainst, false);
                
                ActionResult result = client.interactionManager.interactBlock(client.player, Hand.MAIN_HAND, hitResult);
                client.player.swingHand(Hand.MAIN_HAND);
                
                JsonObject data = new JsonObject();
                data.addProperty("placed", result.isAccepted());
                data.addProperty("status", result.toString());
                data.addProperty("x", x);
                data.addProperty("y", y);
                data.addProperty("z", z);
                
                if (result.isAccepted()) {
                    return CommandResult.success(data);
                } else {
                    return CommandResult.error("Placement interaction failed: " + result.toString());
                }
            }).get();

        } catch (InterruptedException | ExecutionException e) {
            return CommandResult.error("Place execution error: " + e.getMessage());
        } catch (Exception e) {
            return CommandResult.error("Place failed: " + e.getMessage());
        }
    }

    @Override
    public String getCommandName() {
        return "place_block";
    }
}
