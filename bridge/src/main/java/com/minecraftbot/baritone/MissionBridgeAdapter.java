package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;

interface MissionBridgeAdapter {
    JsonObject collectTelemetry(Minecraft client, IBaritone baritone);
    void publishMissionEvent(String reason, JsonObject payload);
    void handleSettings(JsonObject params, JsonObject data);
    void handleSelection(IBaritone baritone, JsonObject params, JsonObject data);
    void handleMine(IBaritone baritone, JsonObject params, JsonObject data);
    void handleGetInventory(Minecraft client, JsonObject data);
    void handleGetState(Minecraft client, IBaritone baritone, JsonObject data);
}
