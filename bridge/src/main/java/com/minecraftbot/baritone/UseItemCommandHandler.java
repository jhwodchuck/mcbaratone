package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import net.minecraft.client.Minecraft;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.EntityHitResult;
import net.minecraft.world.phys.HitResult;

/**
 * Handler for the use_item command.
 * Simulates a right-click (use) action, optionally held for a duration.
 * Used for eating food, using bows, interacting with items in hand, etc.
 */
public class UseItemCommandHandler extends AsyncCommandHandler {
    
    private static final ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor(runnable -> {
        Thread thread = new Thread(runnable, "bridge-use-item-release");
        thread.setDaemon(true);
        return thread;
    });

    @Override
    public CompletableFuture<CommandResult> execute(JsonObject params, Minecraft client,
            IBaritone baritone, Socket clientSocket) {
        int durationMs = params.has("duration_ms") ? params.get("duration_ms").getAsInt() : 0;
        if (durationMs < 0) {
            return CompletableFuture.completedFuture(
                CommandResult.error("duration_ms must be non-negative"));
        }

        return executeOnMainThread(client, () -> {
            if (client.player == null) {
                return CommandResult.error("Player not available");
            }

            JsonObject hit = describeCrosshairTarget(client);
            JsonObject data = new JsonObject();
            data.add("hit", hit);
            data.addProperty("hit_type", hit.get("type").getAsString());
            data.addProperty("held_item", BuiltInRegistries.ITEM.getKey(
                client.player.getMainHandItem().getItem()).toString());

            client.options.keyUse.setDown(true);
            if (durationMs > 0) {
                scheduleRelease(client, durationMs);
                data.addProperty("holding", true);
                data.addProperty("duration_ms", durationMs);
            } else {
                scheduleRelease(client, 50);
                data.addProperty("used", true);
            }
            return CommandResult.success(data);
        });
    }

    private void scheduleRelease(Minecraft client, int delayMs) {
        scheduler.schedule(
            () -> client.execute(() -> client.options.keyUse.setDown(false)),
            delayMs,
            TimeUnit.MILLISECONDS);
    }

    private JsonObject describeCrosshairTarget(Minecraft client) {
        JsonObject hitData = new JsonObject();
        HitResult hit = client.hitResult;
        if (hit == null) {
            hitData.addProperty("type", "unknown");
            return hitData;
        }

        hitData.addProperty("type", hit.getType().name().toLowerCase());
        hitData.addProperty("x", hit.getLocation().x);
        hitData.addProperty("y", hit.getLocation().y);
        hitData.addProperty("z", hit.getLocation().z);

        if (hit instanceof EntityHitResult entityHit) {
            hitData.addProperty("entity_id", entityHit.getEntity().getId());
            hitData.addProperty("entity_type", BuiltInRegistries.ENTITY_TYPE.getKey(
                entityHit.getEntity().getType()).toString());
        } else if (hit instanceof BlockHitResult blockHit) {
            hitData.addProperty("block_x", blockHit.getBlockPos().getX());
            hitData.addProperty("block_y", blockHit.getBlockPos().getY());
            hitData.addProperty("block_z", blockHit.getBlockPos().getZ());
            hitData.addProperty("side", blockHit.getDirection().name().toLowerCase());
            if (client.level != null) {
                hitData.addProperty("block", BuiltInRegistries.BLOCK.getKey(
                    client.level.getBlockState(blockHit.getBlockPos()).getBlock()).toString());
            }
        }
        return hitData;
    }

    @Override
    public String getCommandName() {
        return "use_item";
    }
}
