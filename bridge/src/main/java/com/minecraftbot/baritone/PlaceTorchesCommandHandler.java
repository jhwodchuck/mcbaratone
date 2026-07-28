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
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;

public class PlaceTorchesCommandHandler extends AsyncCommandHandler {

    @Override
    public String getCommandName() {
        return "place_torches";
    }

    @Override
    public CompletableFuture<CommandResult> execute(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        if (!params.has("x") || !params.has("y") || !params.has("z")) {
            return CompletableFuture.completedFuture(CommandResult.error("Coordinates required for torch placement"));
        }

        int x = params.get("x").getAsInt();
        int y = params.get("y").getAsInt();
        int z = params.get("z").getAsInt();
        
        return executeOnMainThread(client, () -> {
            if (client.player == null || client.level == null) {
                return CommandResult.error("Player/World not available");
            }

            // Find torches in hotbar
            int slot = -1;
            for (int i = 0; i < 9; i++) {
                ItemStack stack = client.player.getInventory().getItem(i);
                if (stack.getItem() == Items.TORCH || stack.getItem() == Items.SOUL_TORCH) {
                    slot = i;
                    break;
                }
            }

            if (slot == -1) {
                return CommandResult.error("No torches in hotbar");
            }

            // Select slot
            client.player.getInventory().selected = slot;
            client.player.connection.send(new net.minecraft.network.protocol.game.ServerboundSetCarriedItemPacket(slot));

            // Place logic (copied from PlaceBlockCommandHandler/migration)
            BlockPos targetPos = new BlockPos(x, y, z);
            BlockState currentTargetState = client.level.getBlockState(targetPos);
            
            if (!currentTargetState.canBeReplaced()) {
                 return CommandResult.error("Target position is already occupied: " + targetPos);
            }

            // Find a solid neighbor to place against
            Direction placeFace = Direction.UP;
            BlockPos placeAgainst = targetPos.below();
            boolean foundNeighbor = false;
            
            for (Direction dir : Direction.values()) {
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
            
            // Calculate hit position
            double centerX = placeAgainst.getX() + 0.5;
            double centerY = placeAgainst.getY() + 0.5;
            double centerZ = placeAgainst.getZ() + 0.5;
            
            double dirX = placeFace.getStepX();
            double dirY = placeFace.getStepY();
            double dirZ = placeFace.getStepZ();
            
            Vec3 hitPos = new Vec3(centerX + dirX * 0.5, centerY + dirY * 0.5, centerZ + dirZ * 0.5);
            
            BlockHitResult hitResult = new BlockHitResult(hitPos, placeFace, placeAgainst, false);
            
            InteractionResult result = client.gameMode.useItemOn(client.player, InteractionHand.MAIN_HAND, hitResult);
            client.player.swing(InteractionHand.MAIN_HAND);
            
            JsonObject data = new JsonObject();
            data.addProperty("placed", result.consumesAction());
            data.addProperty("status", result.toString());
            data.addProperty("x", x);
            data.addProperty("y", y);
            data.addProperty("z", z);
            
            if (result.consumesAction()) {
                return CommandResult.success(data);
            } else {
                return CommandResult.error("Placement failed: " + result.toString());
            }
        });
    }
}
