package com.minecraftbot.baritone;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.util.List;
import net.minecraft.core.BlockPos;
import net.minecraft.world.level.ChunkPos;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class FindBlocksCommandHandlerTest {

    @Test
    void parsesExplicitCenterAndBounds() {
        JsonObject params = request(3, 64);
        JsonObject center = new JsonObject();
        center.addProperty("x", 80);
        center.addProperty("y", 75);
        center.addProperty("z", -32);
        params.add("center", center);

        FindBlocksCommandHandler.ScanRequest request =
            FindBlocksCommandHandler.ScanRequest.from(params);

        assertEquals(new BlockPos(80, 75, -32), request.center());
        assertEquals(3, request.radius());
        assertEquals(64, request.limit());
        assertTrue(FindBlocksCommandHandler.withinCube(
            new BlockPos(83, 78, -29), request.center(), request.radius()));
        assertFalse(FindBlocksCommandHandler.withinCube(
            new BlockPos(84, 75, -32), request.center(), request.radius()));
    }

    @Test
    void rejectsUnboundedRequests() {
        assertThrows(
            IllegalArgumentException.class,
            () -> FindBlocksCommandHandler.ScanRequest.from(request(129, 1)));
        assertThrows(
            IllegalArgumentException.class,
            () -> FindBlocksCommandHandler.ScanRequest.from(request(1, 0)));
    }

    @Test
    void chunkPlanStartsWithTheCenteredChunk() {
        BlockPos center = new BlockPos(80, 70, -32);
        List<ChunkPos> chunks = FindBlocksCommandHandler.chunksWithin(center, 24);

        assertEquals(new ChunkPos(5, -2), chunks.getFirst());
        assertTrue(chunks.contains(new ChunkPos(3, -4)));
        assertTrue(chunks.contains(new ChunkPos(6, -1)));
        assertEquals(1024, FindBlocksCommandHandler.candidateLimit(1));
        assertEquals(65536, FindBlocksCommandHandler.candidateLimit(4096));
    }

    private static JsonObject request(int radius, int limit) {
        JsonArray blocks = new JsonArray();
        blocks.add("minecraft:iron_bars");
        JsonObject params = new JsonObject();
        params.add("blocks", blocks);
        params.addProperty("radius", radius);
        params.addProperty("limit", limit);
        return params;
    }
}
