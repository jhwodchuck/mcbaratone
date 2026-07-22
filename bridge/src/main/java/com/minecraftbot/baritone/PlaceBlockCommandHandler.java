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
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;

/**
 * Simple block placement handler that avoids any packet-level manipulation.
 * This is a minimal implementation that just uses the client's interactBlock API.
 */
public class PlaceBlockCommandHandler implements CommandHandler {

    @Override
    public CompletableFuture<CommandResult> handle(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        if (client.player == null || client.world == null || client.interactionManager == null) {
            return CompletableFuture.completedFuture(
                CommandResult.error("Player, world, or interaction manager not available"));
        }

        try {
            JsonObject coordinates = params.has("position") && params.get("position").isJsonObject()
                ? params.getAsJsonObject("position") : params;
            if (!coordinates.has("x") || !coordinates.has("y") || !coordinates.has("z")) {
                return CompletableFuture.completedFuture(CommandResult.error(
                    "Missing placement coordinates; provide x/y/z or position: {x, y, z}"));
            }
            int x = coordinates.get("x").getAsInt();
            int y = coordinates.get("y").getAsInt();
            int z = coordinates.get("z").getAsInt();
            
            BlockPos targetPos = new BlockPos(x, y, z);
            
            CommandResult result = client.submit(() -> {
                BlockState currentTargetState = client.world.getBlockState(targetPos);
                if (!currentTargetState.isReplaceable()) {
                    return CommandResult.error("Target position is already occupied: " + targetPos);
                }

                // Find a solid neighbor to place against
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
                
                // Calculate hit position (center of the face we're clicking)
                double centerX = placeAgainst.getX() + 0.5;
                double centerY = placeAgainst.getY() + 0.5;
                double centerZ = placeAgainst.getZ() + 0.5;
                
                double dirX = placeFace.getOffsetX();
                double dirY = placeFace.getOffsetY();
                double dirZ = placeFace.getOffsetZ();
                
                Vec3d hitPos = new Vec3d(centerX + dirX * 0.5, centerY + dirY * 0.5, centerZ + dirZ * 0.5);
                
                // Verify item is in hand
                if (client.player.getMainHandStack().isEmpty()) {
                     return CommandResult.error("Main hand is empty!");
                }
                
                // Create hit result and interact - NO rotation, NO sneaking packets
                // Just let the game handle it naturally
                BlockHitResult hitResult = new BlockHitResult(hitPos, placeFace, placeAgainst, false);
                
                ActionResult actionResult = client.interactionManager.interactBlock(client.player, Hand.MAIN_HAND, hitResult);
                client.player.swingHand(Hand.MAIN_HAND);
                
                JsonObject data = new JsonObject();
                data.addProperty("placed", actionResult.isAccepted());
                data.addProperty("status", actionResult.toString());
                data.addProperty("accepted", actionResult.isAccepted());
                data.addProperty("x", x);
                data.addProperty("y", y);
                data.addProperty("z", z);
                data.addProperty("item", client.player.getMainHandStack().getName().getString());
                
                if (actionResult.isAccepted()) {
                    return CommandResult.success(data);
                } else {
                    return new CommandResult(false, data,
                        "Placement rejected by Minecraft: " + actionResult
                            + "; item=" + client.player.getMainHandStack().getName().getString()
                            + "; target=" + targetPos);
                }
            }).get();
            
            return CompletableFuture.completedFuture(result);
        } catch (InterruptedException | ExecutionException e) {
            return CompletableFuture.completedFuture(CommandResult.error("Place execution error: " + e.getMessage()));
        } catch (Exception e) {
            return CompletableFuture.completedFuture(CommandResult.error("Place failed: " + e.getMessage()));
        }
    }

    @Override
    public String getCommandName() {
        return "place_block";
    }
}

