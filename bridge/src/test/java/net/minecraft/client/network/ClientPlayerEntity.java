package net.minecraft.client.network;

import net.minecraft.util.math.BlockPos;

public class ClientPlayerEntity {
    public NetworkHandler networkHandler = new NetworkHandler();

    public BlockPos getBlockPos() {
        return new BlockPos(0, 64, 0);
    }

    public static class NetworkHandler {
        public void sendChatMessage(String message) {
            // no-op stub
        }
    }
}
