package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.Comparator;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;
import net.minecraft.world.level.levelgen.Heightmap;

/**
 * Samples surface biomes from chunks already loaded by the client.
 *
 * This command intentionally does not load or generate chunks. It provides a
 * cheap relocation sensor over terrain the bot has actually discovered.
 */
public class BiomeScanCommandHandler extends AsyncCommandHandler {

    private static final int DEFAULT_RADIUS = 128;
    private static final int MAX_RADIUS = 512;
    private static final int DEFAULT_STEP = 16;

    private record BiomeSample(String id, int x, int y, int z, double distance) {}

    @Override
    public String getCommandName() {
        return "scan_biomes";
    }

    @Override
    public CompletableFuture<CommandResult> execute(
            JsonObject params, Minecraft client, IBaritone baritone,
            Socket clientSocket) {
        int radius = params.has("radius") ? params.get("radius").getAsInt() : DEFAULT_RADIUS;
        int step = params.has("step") ? params.get("step").getAsInt() : DEFAULT_STEP;
        if (radius < 0 || radius > MAX_RADIUS) {
            return CompletableFuture.completedFuture(CommandResult.error(
                "radius must be between 0 and " + MAX_RADIUS));
        }
        if (step < 4 || step > 64) {
            return CompletableFuture.completedFuture(CommandResult.error(
                "step must be between 4 and 64"));
        }

        return executeOnMainThread(client, () -> scanLoadedBiomes(client, radius, step));
    }

    private CommandResult scanLoadedBiomes(Minecraft client, int radius, int step) {
        if (client.player == null || client.level == null) {
            return CommandResult.error("Player or world not available");
        }

        BlockPos origin = client.player.blockPosition();
        Map<String, BiomeSample> nearestByBiome = new HashMap<>();
        int loadedSamples = 0;
        int unloadedSamples = 0;

        for (int dx = -radius; dx <= radius; dx += step) {
            for (int dz = -radius; dz <= radius; dz += step) {
                double distance = Math.hypot(dx, dz);
                if (distance > radius) {
                    continue;
                }
                int x = origin.getX() + dx;
                int z = origin.getZ() + dz;
                if (!client.level.hasChunk(x >> 4, z >> 4)) {
                    unloadedSamples++;
                    continue;
                }

                int y = client.level.getHeight(
                    Heightmap.Types.MOTION_BLOCKING_NO_LEAVES, x, z) - 1;
                BlockPos samplePos = new BlockPos(x, y, z);
                String biomeId = client.level.getBiome(samplePos).unwrapKey()
                    .map(key -> key.identifier().toString())
                    .orElse("unknown");
                loadedSamples++;

                BiomeSample sample = new BiomeSample(biomeId, x, y, z, distance);
                nearestByBiome.merge(
                    biomeId, sample,
                    (existing, candidate) -> candidate.distance() < existing.distance()
                        ? candidate : existing);
            }
        }

        JsonArray biomes = new JsonArray();
        nearestByBiome.values().stream()
            .sorted(Comparator.comparingDouble(BiomeSample::distance))
            .forEach(sample -> biomes.add(toJson(sample)));

        String currentBiome = client.level.getBiome(origin).unwrapKey()
            .map(key -> key.identifier().toString())
            .orElse("unknown");
        JsonObject data = new JsonObject();
        data.addProperty("current_biome", currentBiome);
        data.addProperty("radius", radius);
        data.addProperty("step", step);
        data.addProperty("loaded_samples", loadedSamples);
        data.addProperty("unloaded_samples", unloadedSamples);
        data.add("biomes", biomes);
        return CommandResult.success(data);
    }

    private JsonObject toJson(BiomeSample sample) {
        JsonObject json = new JsonObject();
        json.addProperty("id", sample.id());
        json.addProperty("distance", sample.distance());
        JsonObject position = new JsonObject();
        position.addProperty("x", sample.x());
        position.addProperty("y", sample.y());
        position.addProperty("z", sample.z());
        json.add("position", position);
        return json;
    }
}
