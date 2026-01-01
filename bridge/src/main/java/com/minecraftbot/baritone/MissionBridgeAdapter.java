package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;

interface MissionBridgeAdapter {
    JsonObject collectTelemetry(MinecraftClient client, IBaritone baritone);
    void publishMissionEvent(String reason, JsonObject payload);
    void handleSettings(JsonObject params, JsonObject data);
    void handleSelection(IBaritone baritone, JsonObject params, JsonObject data);
    void handleMine(IBaritone baritone, JsonObject params, JsonObject data);
    void handleGetInventory(MinecraftClient client, JsonObject data);
    void handleGetState(MinecraftClient client, IBaritone baritone, JsonObject data);
}
