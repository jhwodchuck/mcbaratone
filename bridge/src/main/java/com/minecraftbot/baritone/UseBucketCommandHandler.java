package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.level.material.Fluids;
import net.minecraft.world.phys.*;

/** Water-only bucket use through vanilla item use and a verified real ray. */
public class UseBucketCommandHandler extends AsyncCommandHandler {
    public String getCommandName() { return "use_bucket"; }
    public CompletableFuture<CommandResult> execute(JsonObject params, Minecraft client, IBaritone baritone, Socket socket) {
        CommandResult invalid = validateCoordinates(params);
        if (invalid != null) return CompletableFuture.completedFuture(invalid);
        BlockPos target = getBlockPos(params);
        boolean pickup = params.has("operation") && "pickup".equals(params.get("operation").getAsString());
        if (!params.has("operation") || !(pickup || "place".equals(params.get("operation").getAsString())))
            return CompletableFuture.completedFuture(CommandResult.error("operation must be place or pickup"));
        return ObservedMutation.run(client::execute, () -> client.level.getGameTime(), () -> {
            if (client.player == null || client.level == null || client.gameMode == null)
                return CommandResult.error("Player/world unavailable");
            if (!client.player.getMainHandItem().is(pickup ? Items.BUCKET : Items.WATER_BUCKET))
                return CommandResult.error("Wrong bucket in main hand");
            var fluid = client.level.getFluidState(target);
            if (pickup && !(fluid.is(Fluids.WATER) && fluid.isSource()))
                return CommandResult.error("Pickup target is not a water source");
            if (!pickup && !client.level.getBlockState(target).isAir())
                return CommandResult.error("Water placement target must be observed air");
            Vec3 eye = client.player.getEyePosition();
            Vec3 aim = null;
            if (pickup) aim = Vec3.atCenterOf(target);
            else for (Direction dir : PlaceBlockCommandHandler.PLACEMENT_SUPPORT_ORDER) {
                BlockPos support = target.relative(dir);
                if (client.level.getBlockState(support).canBeReplaced()) continue;
                // Aim just inside the support face so vanilla raycasting hits it.
                Vec3 point = Vec3.atCenterOf(support).add(-dir.getStepX() * .499,
                    -dir.getStepY() * .499, -dir.getStepZ() * .499);
                BlockHitResult hit = client.level.clip(new ClipContext(eye, point,
                    ClipContext.Block.OUTLINE, ClipContext.Fluid.NONE, client.player));
                if (hit.getType() == HitResult.Type.BLOCK && hit.getBlockPos().equals(support)
                    && hit.getDirection() == dir.getOpposite()) { aim = point; break; }
            }
            if (aim == null || eye.distanceTo(aim) > client.player.blockInteractionRange())
                return CommandResult.error("No visible reachable bucket target/support face");
            BlockHitResult hit = client.level.clip(new ClipContext(eye, aim, ClipContext.Block.OUTLINE,
                pickup ? ClipContext.Fluid.SOURCE_ONLY : ClipContext.Fluid.NONE, client.player));
            if (hit.getType() != HitResult.Type.BLOCK || (pickup && !hit.getBlockPos().equals(target)))
                return CommandResult.error("Bucket ray obstructed");
            Vec3 delta = aim.subtract(eye);
            client.player.setYRot((float)(Math.atan2(delta.z, delta.x) * 180 / Math.PI - 90));
            client.player.setXRot((float)(-Math.atan2(delta.y, Math.sqrt(delta.x * delta.x + delta.z * delta.z)) * 180 / Math.PI));
            var interaction = client.gameMode.useItem(client.player, InteractionHand.MAIN_HAND);
            if (!interaction.consumesAction()) return CommandResult.error("Bucket use rejected: " + interaction);
            JsonObject data = new JsonObject(); data.addProperty("accepted", true);
            data.addProperty("operation", pickup ? "pickup" : "place");
            data.addProperty("x", target.getX()); data.addProperty("y", target.getY()); data.addProperty("z", target.getZ());
            return CommandResult.success(data);
        }, data -> {
            var fluid = client.level.getFluidState(target);
            boolean source = fluid.is(Fluids.WATER) && fluid.isSource();
            data.addProperty("water_source_observed", source);
            return pickup ? !source && client.player.getMainHandItem().is(Items.WATER_BUCKET)
                : source && client.player.getMainHandItem().is(Items.BUCKET);
        }, "bucket_effect_verified");
    }
}
