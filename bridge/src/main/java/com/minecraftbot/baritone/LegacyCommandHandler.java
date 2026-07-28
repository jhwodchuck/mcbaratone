package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import net.minecraft.client.Minecraft;

/**
 * Interface for handling legacy commands that haven't been migrated to the handler pattern.
 */
@FunctionalInterface
public interface LegacyCommandHandler {
    CommandResult handleLegacyCommand(String command, JsonObject params,
            Minecraft client, IBaritone baritone, Socket clientSocket);
}