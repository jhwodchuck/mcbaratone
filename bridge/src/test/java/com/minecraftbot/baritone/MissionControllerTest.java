package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.util.math.BlockPos;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.net.Socket;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

public class MissionControllerTest {

    private static class TestBridge implements MissionBridgeAdapter {
        JsonObject lastPublished;

        @Override
        public JsonObject collectTelemetry(MinecraftClient client, IBaritone baritone) {
            return new JsonObject();
        }

        @Override
        public void publishMissionEvent(String reason, JsonObject payload) {
            JsonObject data = new JsonObject();
            data.addProperty("reason", reason);
            data.add("payload", payload);
            lastPublished = data;
        }

        @Override
        public void handleSettings(JsonObject params, JsonObject data) {
            data.addProperty("ok", true);
        }

        @Override
        public void handleSelection(IBaritone baritone, JsonObject params, JsonObject data) {
            // no-op
        }

        @Override
        public void handleMine(IBaritone baritone, JsonObject params, JsonObject data) {
            data.addProperty("started", true);
        }

        @Override
        public void handleGetInventory(MinecraftClient client, JsonObject data) {
            data.add("inventory", new JsonArray());
        }

        @Override
        public void handleGetState(MinecraftClient client, IBaritone baritone, JsonObject data) {
            data.addProperty("dimension", "test");
        }
    }

    private static class TestBaritone implements IBaritone {
        @Override
        public BuilderProcess getBuilderProcess() {
            return new BuilderProcess() {
                @Override
                public void clearArea(BlockPos corner1, BlockPos corner2) {
                    // no-op
                }
            };
        }

        @Override
        public ExploreProcess getExploreProcess() {
            return new ExploreProcess() {
                @Override
                public void explore(int x, int z) {
                    // no-op
                }
            };
        }
    }

    private final Socket ownerSocket = new Socket();
    private final Socket intruderSocket = new Socket();
    private final IBaritone baritone = new TestBaritone();
    private TestBridge bridge;
    private MissionController controller;

    @BeforeEach
    public void setUp() {
        bridge = new TestBridge();
        controller = new MissionController(bridge);
    }

    @Test
    public void missionQueueLocksToFirstClient() {
        JsonArray actions = new JsonArray();
        actions.add("bootstrap");
        JsonObject params = new JsonObject();
        params.add("actions", actions);

        JsonObject firstResponse = new JsonObject();
        JsonObject secondResponse = new JsonObject();

        MinecraftClient client = MinecraftClient.getInstance();

        controller.tryHandle("mission_queue", params, firstResponse, client, baritone, ownerSocket);
        JsonObject intruderRequest = new JsonObject();
        JsonArray intruderActions = new JsonArray();
        intruderActions.add("establish_base");
        intruderRequest.add("actions", intruderActions);

        controller.tryHandle("mission_queue", intruderRequest, secondResponse, client, baritone, intruderSocket);

        assertTrue(firstResponse.has("queue"));
        assertEquals("Mission is currently controlled by another client", secondResponse.get("error").getAsString());
        assertEquals("locked", secondResponse.get("error_code").getAsString());
    }

    @Test
    public void bootstrapMacroPublishesMissionEvent() {
        JsonObject params = new JsonObject();
        params.addProperty("name", "bootstrap");
        JsonObject response = new JsonObject();

        MinecraftClient client = MinecraftClient.getInstance();
        controller.tryHandle("mission_macro", params, response, client, baritone, ownerSocket);

        assertEquals("macro", bridge.lastPublished.get("reason").getAsString());
    }
}
