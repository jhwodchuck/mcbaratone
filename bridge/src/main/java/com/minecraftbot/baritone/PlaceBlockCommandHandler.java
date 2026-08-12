package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;

/**
 * Simple block placement handler that avoids any packet-level manipulation.
 * This is a minimal implementation that just uses the client's interactBlock API.
 */
public class PlaceBlockCommandHandler extends AsyncCommandHandler {

    /**
     * Prefer ordinary top/side placement faces and use an overhead support
     * only as a last resort. Direction.values() puts UP before the horizontal
     * faces, which made floor holes beneath a wall click the inaccessible
     * underside of that wall even when four reachable side faces existed.
     */
    static final Direction[] PLACEMENT_SUPPORT_ORDER = {
        Direction.DOWN,
        Direction.NORTH,
        Direction.SOUTH,
        Direction.WEST,
        Direction.EAST,
        Direction.UP
    };

    @Override
    public CompletableFuture<CommandResult> execute(
            JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        if (client.player == null || client.level == null || client.gameMode == null) {
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
            
            return executeOnMainThread(client, () -> {
                BlockState currentTargetState = client.level.getBlockState(targetPos);
                if (!currentTargetState.canBeReplaced()) {
                    return CommandResult.error("Target position is already occupied: " + targetPos);
                }

                // Find a solid neighbor to place against
                Direction placeFace = Direction.UP;
                BlockPos placeAgainst = targetPos.below();
                boolean foundNeighbor = false;
                
                for (Direction dir : PLACEMENT_SUPPORT_ORDER) {
                    BlockPos adjacent = targetPos.relative(dir);
                    BlockState adjacentState = client.level.getBlockState(adjacent);
                    if (adjacentState.canBeReplaced()) continue;

                    placeAgainst = adjacent;
                    placeFace = dir.getOpposite();
                    foundNeighbor = true;
                    break;
                }
                
                if (!foundNeighbor && client.level.getBlockState(targetPos.below()).canBeReplaced()) {
                    return CommandResult.error("No solid block found to place against at " + targetPos);
                }
                
                // Calculate hit position (center of the face we're clicking)
                double centerX = placeAgainst.getX() + 0.5;
                double centerY = placeAgainst.getY() + 0.5;
                double centerZ = placeAgainst.getZ() + 0.5;
                
                double dirX = placeFace.getStepX();
                double dirY = placeFace.getStepY();
                double dirZ = placeFace.getStepZ();
                
                Vec3 hitPos = new Vec3(centerX + dirX * 0.5, centerY + dirY * 0.5, centerZ + dirZ * 0.5);
                
                // Verify item is in hand
                if (client.player.getMainHandItem().isEmpty()) {
                     return CommandResult.error("Main hand is empty!");
                }
                
                // Create hit result and interact - NO rotation, NO sneaking packets
                // Just let the game handle it naturally
                BlockHitResult hitResult = new BlockHitResult(hitPos, placeFace, placeAgainst, false);
                
                InteractionResult actionResult = client.gameMode.useItemOn(client.player, InteractionHand.MAIN_HAND, hitResult);
                client.player.swing(InteractionHand.MAIN_HAND);
                
                JsonObject data = new JsonObject();
                data.addProperty("placed", actionResult.consumesAction());
                data.addProperty("status", actionResult.toString());
                data.addProperty("accepted", actionResult.consumesAction());
                data.addProperty("x", x);
                data.addProperty("y", y);
                data.addProperty("z", z);
                data.addProperty("item", client.player.getMainHandItem().getHoverName().getString());
                
                if (actionResult.consumesAction()) {
                    return CommandResult.success(data);
                } else {
                    return new CommandResult(false, data,
                        "Placement rejected by Minecraft: " + actionResult
                            + "; item=" + client.player.getMainHandItem().getHoverName().getString()
                            + "; target=" + targetPos);
                }
            });
        } catch (Exception e) {
            return CompletableFuture.completedFuture(CommandResult.error("Place failed: " + e.getMessage()));
        }
    }

    @Override
    public String getCommandName() {
        return "place_block";
    }
}
