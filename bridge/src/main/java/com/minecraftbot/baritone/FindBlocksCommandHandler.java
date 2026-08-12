package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import baritone.api.utils.BlockOptionalMetaLookup;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.level.ChunkPos;
import net.minecraft.world.level.chunk.LevelChunk;
import net.minecraft.world.level.chunk.LevelChunkSection;
import net.minecraft.world.level.block.state.BlockState;

/**
 * Finds loaded blocks from bounded client-thread chunk-section snapshots, then
 * scans those copies off-thread without calling getBlockState millions of times.
 */
public class FindBlocksCommandHandler extends AsyncCommandHandler {

    private static final int MAX_RADIUS = 128;
    private static final int MAX_LIMIT = 4096;
    private static final int MAX_CANDIDATES = 65536;
    private static final int SCAN_TIMEOUT_SECONDS = 12;
    private static final ExecutorService SCAN_EXECUTOR =
        Executors.newSingleThreadExecutor(runnable -> {
            Thread thread = new Thread(runnable, "bridge-find-blocks");
            thread.setDaemon(true);
            return thread;
        });

    @Override
    public CompletableFuture<CommandResult> execute(JsonObject params,
            Minecraft client, IBaritone baritone, Socket clientSocket) {
        final ScanRequest request;
        try {
            request = ScanRequest.from(params);
        } catch (IllegalArgumentException exception) {
            return CompletableFuture.completedFuture(
                CommandResult.error(exception.getMessage()));
        }

        CompletableFuture<CommandResult> result = new CompletableFuture<>();
        AtomicReference<Future<?>> scanTask = new AtomicReference<>();
        CompletableFuture.delayedExecutor(
            SCAN_TIMEOUT_SECONDS, TimeUnit.SECONDS).execute(() -> {
                if (result.complete(CommandResult.error(
                        "find_blocks exceeded its bounded scan time"))) {
                    Future<?> pending = scanTask.get();
                    if (pending != null) {
                        pending.cancel(true);
                    }
                }
            });
        Runnable captureCenter = () -> {
            if (result.isDone()) {
                return;
            }
            if (client.player == null || client.level == null || baritone == null) {
                result.complete(CommandResult.error("Player/World not available"));
                return;
            }
            BlockPos center = request.center() == null
                ? client.player.blockPosition()
                : request.center();
            Future<?> pending = SCAN_EXECUTOR.submit(
                () -> scanLoadedChunks(
                    request, center, client, client.level, baritone, result));
            scanTask.set(pending);
            if (result.isDone()) {
                pending.cancel(true);
            }
        };
        try {
            client.execute(captureCenter);
        } catch (Exception exception) {
            result.complete(CommandResult.error(
                "Could not schedule block scan: " + exception.getMessage()));
        }
        return result;
    }

    private void scanLoadedChunks(ScanRequest request, BlockPos center,
            Minecraft client, ClientLevel capturedLevel, IBaritone baritone,
            CompletableFuture<CommandResult> result) {
        try {
            BlockOptionalMetaLookup lookup = new BlockOptionalMetaLookup(
                request.blocks().toArray(String[]::new));
            List<BlockPos> matches = new ArrayList<>();
            Map<BlockPos, BlockState> matchedStates = new HashMap<>();
            Set<BlockPos> seen = new HashSet<>();
            int scannedChunks = 0;
            int candidateLimit = candidateLimit(request.limit());
            boolean candidateBudgetReached = false;
            long deadline = System.nanoTime()
                + TimeUnit.SECONDS.toNanos(SCAN_TIMEOUT_SECONDS);
            List<ChunkPos> chunks = chunksWithin(center, request.radius());

            chunkLoop:
            for (ChunkPos chunk : chunks) {
                if (scanCancelled(
                        result, client, capturedLevel, baritone, deadline)) {
                    return;
                }
                ChunkSnapshot snapshot = snapshotChunk(
                    client, capturedLevel, chunk, lookup, result, deadline);
                if (snapshot == null) {
                    return;
                }
                if (!snapshot.loaded()) {
                    continue;
                }
                scannedChunks++;
                for (SectionSnapshot sectionSnapshot : snapshot.sections()) {
                    LevelChunkSection section = sectionSnapshot.section();
                    for (int localY = 0; localY < 16; localY++) {
                        for (int localZ = 0; localZ < 16; localZ++) {
                            for (int localX = 0; localX < 16; localX++) {
                                if (scanCancelled(
                                        result, client, capturedLevel,
                                        baritone, deadline)) {
                                    return;
                                }
                                BlockState state = section.getBlockState(
                                    localX, localY, localZ);
                                if (!lookup.has(state)) {
                                    continue;
                                }
                                BlockPos position = new BlockPos(
                                    chunk.getMinBlockX() + localX,
                                    sectionSnapshot.y() + localY,
                                    chunk.getMinBlockZ() + localZ);
                                if (withinCube(
                                        position, center, request.radius())
                                        && seen.add(position)) {
                                    matches.add(position);
                                    matchedStates.put(position, state);
                                    if (matches.size() >= candidateLimit) {
                                        candidateBudgetReached = true;
                                        break chunkLoop;
                                    }
                                }
                            }
                        }
                    }
                }
            }

            matches.sort(Comparator.comparingDouble(position -> position.distSqr(center)));
            JsonArray found = new JsonArray();
            int count = Math.min(request.limit(), matches.size());
            for (int index = 0; index < count; index++) {
                BlockPos position = matches.get(index);
                BlockState state = matchedStates.get(position);
                JsonObject block = new JsonObject();
                block.addProperty("x", position.getX());
                block.addProperty("y", position.getY());
                block.addProperty("z", position.getZ());
                block.addProperty("block", BuiltInRegistries.BLOCK.getKey(
                    state.getBlock()).toString());
                block.addProperty("distance", Math.sqrt(position.distSqr(center)));
                found.add(block);
            }

            JsonObject centerData = new JsonObject();
            centerData.addProperty("x", center.getX());
            centerData.addProperty("y", center.getY());
            centerData.addProperty("z", center.getZ());
            JsonObject data = new JsonObject();
            data.add("found", found);
            data.addProperty("count", count);
            data.addProperty(
                "complete",
                !candidateBudgetReached
                    && scannedChunks == chunks.size()
                    && client.level == capturedLevel);
            data.addProperty("scanned_chunks", scannedChunks);
            data.addProperty("requested_chunks", chunks.size());
            data.addProperty(
                "dimension",
                capturedLevel.dimension().identifier().toString());
            data.add("center", centerData);
            result.complete(CommandResult.success(data));
        } catch (Exception exception) {
            result.complete(CommandResult.error(
                "Find blocks failed: " + exception.getMessage()));
        }
    }

    static boolean withinCube(BlockPos position, BlockPos center, int radius) {
        return Math.abs(position.getX() - center.getX()) <= radius
            && Math.abs(position.getY() - center.getY()) <= radius
            && Math.abs(position.getZ() - center.getZ()) <= radius;
    }

    static List<ChunkPos> chunksWithin(BlockPos center, int radius) {
        int minChunkX = Math.floorDiv(center.getX() - radius, 16);
        int maxChunkX = Math.floorDiv(center.getX() + radius, 16);
        int minChunkZ = Math.floorDiv(center.getZ() - radius, 16);
        int maxChunkZ = Math.floorDiv(center.getZ() + radius, 16);
        List<ChunkPos> chunks = new ArrayList<>();
        for (int chunkX = minChunkX; chunkX <= maxChunkX; chunkX++) {
            for (int chunkZ = minChunkZ; chunkZ <= maxChunkZ; chunkZ++) {
                chunks.add(new ChunkPos(chunkX, chunkZ));
            }
        }
        chunks.sort(Comparator.comparingLong(chunk -> horizontalDistanceSqr(
            chunk, center)));
        return chunks;
    }

    static int candidateLimit(int requestedLimit) {
        return Math.min(MAX_CANDIDATES, Math.max(1024, requestedLimit * 16));
    }

    private static ChunkSnapshot snapshotChunk(
            Minecraft client,
            ClientLevel capturedLevel,
            ChunkPos chunk,
            BlockOptionalMetaLookup lookup,
            CompletableFuture<CommandResult> result,
            long deadline) {
        CompletableFuture<ChunkSnapshot> snapshot = new CompletableFuture<>();
        try {
            client.execute(() -> {
                if (result.isDone() || snapshot.isDone()
                        || client.level != capturedLevel) {
                    snapshot.cancel(false);
                    return;
                }
                LevelChunk loaded = capturedLevel.getChunkSource().getChunk(
                    chunk.x(), chunk.z(), null, false);
                if (loaded == null) {
                    snapshot.complete(new ChunkSnapshot(false, List.of()));
                    return;
                }
                List<SectionSnapshot> sections = new ArrayList<>();
                if (!loaded.isEmpty()) {
                    LevelChunkSection[] values = loaded.getSections();
                    for (int index = 0; index < values.length; index++) {
                        LevelChunkSection section = values[index];
                        if (section != null && !section.hasOnlyAir()
                                && section.maybeHas(lookup::has)) {
                            sections.add(new SectionSnapshot(
                                capturedLevel.getSectionYFromSectionIndex(index) << 4,
                                section.copy()));
                        }
                    }
                }
                snapshot.complete(new ChunkSnapshot(
                    true, List.copyOf(sections)));
            });
            long remaining = deadline - System.nanoTime();
            if (remaining <= 0) {
                snapshot.cancel(false);
                return null;
            }
            return snapshot.get(remaining, TimeUnit.NANOSECONDS);
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            snapshot.cancel(false);
            return null;
        } catch (Exception exception) {
            snapshot.cancel(false);
            return null;
        }
    }

    private record SectionSnapshot(int y, LevelChunkSection section) {}

    private record ChunkSnapshot(
        boolean loaded, List<SectionSnapshot> sections
    ) {}

    private static boolean scanCancelled(
            CompletableFuture<CommandResult> result,
            Minecraft client,
            ClientLevel capturedLevel,
            IBaritone baritone,
            long deadline) {
        return result.isDone()
            || Thread.currentThread().isInterrupted()
            || System.nanoTime() >= deadline
            || client.level != capturedLevel
            || baritone.getPlayerContext().world() != capturedLevel;
    }

    private static long horizontalDistanceSqr(ChunkPos chunk, BlockPos center) {
        long nearestX = Math.max(chunk.getMinBlockX(),
            Math.min(center.getX(), chunk.getMaxBlockX()));
        long nearestZ = Math.max(chunk.getMinBlockZ(),
            Math.min(center.getZ(), chunk.getMaxBlockZ()));
        long dx = nearestX - center.getX();
        long dz = nearestZ - center.getZ();
        return dx * dx + dz * dz;
    }

    @Override
    public String getCommandName() {
        return "find_blocks";
    }

    record ScanRequest(List<String> blocks, int radius, int limit,
            BlockPos center) {
        static ScanRequest from(JsonObject params) {
            if (params == null || !params.has("blocks")
                    || !params.get("blocks").isJsonArray()) {
                throw new IllegalArgumentException(
                    "blocks must be a non-empty array");
            }
            JsonArray rawBlocks = params.getAsJsonArray("blocks");
            if (rawBlocks.isEmpty()) {
                throw new IllegalArgumentException(
                    "blocks must be a non-empty array");
            }
            List<String> blocks = new ArrayList<>();
            for (JsonElement element : rawBlocks) {
                String block = element.getAsString();
                blocks.add(block.contains(":") ? block : "minecraft:" + block);
            }
            int radius = params.has("radius") ? params.get("radius").getAsInt() : 32;
            int limit = params.has("limit") ? params.get("limit").getAsInt() : 100;
            if (radius < 0 || radius > MAX_RADIUS) {
                throw new IllegalArgumentException(
                    "radius must be between 0 and " + MAX_RADIUS);
            }
            if (limit < 1 || limit > MAX_LIMIT) {
                throw new IllegalArgumentException(
                    "limit must be between 1 and " + MAX_LIMIT);
            }
            BlockPos center = null;
            if (params.has("center")) {
                if (!params.get("center").isJsonObject()) {
                    throw new IllegalArgumentException(
                        "center must contain integer x, y, and z");
                }
                JsonObject value = params.getAsJsonObject("center");
                if (!value.has("x") || !value.has("y") || !value.has("z")) {
                    throw new IllegalArgumentException(
                        "center must contain integer x, y, and z");
                }
                center = new BlockPos(
                    value.get("x").getAsInt(),
                    value.get("y").getAsInt(),
                    value.get("z").getAsInt());
            }
            return new ScanRequest(List.copyOf(blocks), radius, limit, center);
        }
    }
}
