package net.minecraft.client;

import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.world.ClientWorld;

public class MinecraftClient {
    private static final MinecraftClient INSTANCE = new MinecraftClient();

    public ClientPlayerEntity player;
    public ClientWorld world;
    public java.io.File runDirectory = new java.io.File(".");

    public static MinecraftClient getInstance() {
        return INSTANCE;
    }

    public void execute(Runnable runnable) {
        if (runnable != null) {
            runnable.run();
        }
    }
}
