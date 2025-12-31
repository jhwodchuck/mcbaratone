package com.minecraftbot.baritone;

public interface BridgeContext {
    void sendChat(String message);
    void setGoal(int x, int y, int z, int radius);
    void mine(String blockType);
    boolean isPlayerAvailable();
}
