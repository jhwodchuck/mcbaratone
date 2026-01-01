package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import java.net.Socket;

/**
 * Interface for handling legacy commands that haven't been migrated to the handler pattern.
 */
@FunctionalInterface
public interface LegacyCommandHandler {
    CommandResult handleLegacyCommand(String command, JsonObject params,
            MinecraftClient client, IBaritone baritone, Socket clientSocket);
}