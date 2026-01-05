package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.item.ItemStack;
import net.minecraft.item.Items;
import net.minecraft.util.ActionResult;
import net.minecraft.util.Hand;
import net.minecraft.util.hit.BlockHitResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Direction;
import net.minecraft.util.math.Vec3d;
import net.minecraft.block.BlockState;

import java.net.Socket;
import java.util.concurrent.CompletableFuture;

public class PlaceTorchesCommandHandler extends AsyncCommandHandler {

    @Override
    public String getCommandName() {
        return "place_torches";
    }

    @Override
    public CompletableFuture<CommandResult> execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        if (!params.has("x") || !params.has("y") || !params.has("z")) {
            return CompletableFuture.completedFuture(CommandResult.error("Coordinates required for torch placement"));
        }

        int x = params.get("x").getAsInt();
        int y = params.get("y").getAsInt();
        int z = params.get("z").getAsInt();
        
        return executeOnMainThread(client, () -> {
            if (client.player == null || client.world == null) {
                return CommandResult.error("Player/World not available");
            }

            // Find torches in hotbar
            int slot = -1;
            for (int i = 0; i < 9; i++) {
                ItemStack stack = client.player.getInventory().getStack(i);
                if (stack.getItem() == Items.TORCH || stack.getItem() == Items.SOUL_TORCH) {
                    slot = i;
                    break;
                }
            }

            if (slot == -1) {
                return CommandResult.error("No torches in hotbar");
            }

            // Select slot
            client.player.getInventory().selectedSlot = slot;
            client.player.networkHandler.sendPacket(new net.minecraft.network.packet.c2s.play.UpdateSelectedSlotC2SPacket(slot));

            // Place logic (copied from PlaceBlockCommandHandler/migration)
            BlockPos targetPos = new BlockPos(x, y, z);
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
            
            // Calculate hit position
            double centerX = placeAgainst.getX() + 0.5;
            double centerY = placeAgainst.getY() + 0.5;
            double centerZ = placeAgainst.getZ() + 0.5;
            
            double dirX = placeFace.getOffsetX();
            double dirY = placeFace.getOffsetY();
            double dirZ = placeFace.getOffsetZ();
            
            Vec3d hitPos = new Vec3d(centerX + dirX * 0.5, centerY + dirY * 0.5, centerZ + dirZ * 0.5);
            
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
                return CommandResult.error("Placement failed: " + result.toString());
            }
        });
    }
}
