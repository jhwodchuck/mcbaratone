package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.registry.Registries;
import net.minecraft.util.math.BlockPos;
import net.minecraft.block.BlockState;
import net.minecraft.util.math.Direction;
import net.minecraft.world.World;

import java.net.Socket;
import java.util.function.IntPredicate;
import java.util.Map;
import java.util.TreeMap;

/**
 * Read-only command handler for inspecting a square build site area around the player.
 */
public class BuildSiteCommandHandler extends AbstractCommandHandler {
    private static final int DEFAULT_RADIUS = 12;
    private static final int MIN_RADIUS = 4;
    private static final int MAX_RADIUS = 24;
    private static final int MAX_SURFACE_SCAN_UP = 6;
    private static final int MAX_SURFACE_SCAN_DOWN = 16;
    private static final int HEADROOM_BLOCKS = 6;

    @Override
    public String getCommandName() {
        return "inspect_build_site";
    }

    @Override
    public boolean isOfflineSafe() {
        return true;
    }

    @Override
    protected CommandResult execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        if (client == null) {
            return CommandResult.error("Minecraft client not available");
        }

        if (client.world == null || client.player == null) {
            return CommandResult.error("World or player not available");
        }

        int requestedRadius = params.has("radius") ? params.get("radius").getAsInt() : DEFAULT_RADIUS;
        int radius = clampRadius(requestedRadius);

        try {
            return executeOnMainThread(client, () -> inspectBuildSite(client, radius)).get();
        } catch (Exception e) {
            return CommandResult.error("Failed to inspect build site: " + e.getMessage());
        }
    }

    private int clampRadius(int requestedRadius) {
        int radius = requestedRadius;
        if (radius <= 0) {
            radius = DEFAULT_RADIUS;
        }
        return Math.max(MIN_RADIUS, Math.min(MAX_RADIUS, radius));
    }

    private CommandResult inspectBuildSite(MinecraftClient client, int radius) {
        ClientPlayerEntity player = client.player;
        World world = client.world;
        BlockPos center = player.getBlockPos();

        int baseY = center.getY();
        int totalColumns = 0;
        int clearColumns = 0;
        int minDy = Integer.MAX_VALUE;
        int maxDy = Integer.MIN_VALUE;
        int waterColumns = 0;
        Map<String, Integer> surfaceCounts = new TreeMap<>();

        for (int dx = -radius; dx <= radius; dx++) {
            for (int dz = -radius; dz <= radius; dz++) {
                totalColumns++;

                BlockPos sample = new BlockPos(center.getX() + dx, baseY, center.getZ() + dz);
                BlockPos surface = findSurface(world, sample, baseY);
                int dy = surface.getY() - baseY;
                minDy = Math.min(minDy, dy);
                maxDy = Math.max(maxDy, dy);

                BlockState surfaceState = world.getBlockState(surface);
                String surfaceId = Registries.BLOCK.getId(surfaceState.getBlock()).toString();
                surfaceCounts.merge(surfaceId, 1, Integer::sum);

                if (hasFluidInSurfaceScan(world, sample, baseY)) {
                    waterColumns++;
                }

                if (isClearHeadroom(world, surface)) {
                    clearColumns++;
                }
            }
        }

        double clearPercent = totalColumns == 0 ? 0.0 : (clearColumns * 100.0) / totalColumns;

        JsonObject data = new JsonObject();
        data.addProperty("read_only", true);

        JsonObject origin = new JsonObject();
        origin.addProperty("x", center.getX());
        origin.addProperty("y", center.getY());
        origin.addProperty("z", center.getZ());
        data.add("player_origin", origin);

        data.addProperty("radius", radius);
        data.addProperty("min_dy", totalColumns == 0 ? 0 : minDy);
        data.addProperty("max_dy", totalColumns == 0 ? 0 : maxDy);
        data.addProperty("clear_percent", Math.round(clearPercent));
        data.addProperty("water_columns", waterColumns);
        data.addProperty("total_columns", totalColumns);

        JsonObject counts = new JsonObject();
        for (Map.Entry<String, Integer> entry : surfaceCounts.entrySet()) {
            counts.addProperty(entry.getKey(), entry.getValue());
        }
        data.add("surface_counts", counts);

        return CommandResult.success(data);
    }

    private boolean isClearHeadroom(World world, BlockPos surface) {
        for (int dy = 1; dy <= HEADROOM_BLOCKS; dy++) {
            BlockState state = world.getBlockState(surface.up(dy));
            if (!state.isAir()) {
                return false;
            }
        }
        return true;
    }

    private BlockPos findSurface(World world, BlockPos pos, int baseY) {
        for (int dy = MAX_SURFACE_SCAN_UP; dy >= -MAX_SURFACE_SCAN_DOWN; dy--) {
            BlockPos candidate = new BlockPos(pos.getX(), baseY + dy, pos.getZ());
            if (isSurfaceCandidate(world, candidate)) {
                return candidate;
            }
        }

        return new BlockPos(pos.getX(), baseY - 1, pos.getZ());
    }

    private boolean isSurfaceCandidate(World world, BlockPos pos) {
        BlockState state = world.getBlockState(pos);
        return !state.isAir()
                && state.getFluidState().isEmpty()
                && !state.isReplaceable()
                && state.isSideSolidFullSquare(world, pos, Direction.UP);
    }

    private boolean hasFluidInSurfaceScan(World world, BlockPos pos, int baseY) {
        IntPredicate hasFluidAtY = (y) -> {
            BlockState state = world.getBlockState(new BlockPos(pos.getX(), y, pos.getZ()));
            return !state.getFluidState().isEmpty();
        };

        return hasFluidInSurfaceScan(baseY, hasFluidAtY);
    }

    static boolean hasFluidInSurfaceScan(int baseY, IntPredicate hasFluidAtY) {
        for (int dy = MAX_SURFACE_SCAN_UP; dy >= -MAX_SURFACE_SCAN_DOWN; dy--) {
            if (hasFluidAtY.test(baseY + dy)) {
                return true;
            }
        }
        return false;
    }
}
