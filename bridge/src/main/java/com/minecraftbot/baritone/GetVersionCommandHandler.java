package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.Minecraft;
import java.net.Socket;

/**
 * Handler for get_version command.
 * Returns bridge mod version and metadata to verify installed jar.
 */
public class GetVersionCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "get_version";
    }

    @Override
    protected CommandResult execute(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        JsonObject data = new JsonObject();
        try {
            var loader = FabricLoader.getInstance();
            var container = loader.getModContainer("baritone-api-bridge");
            if (container.isPresent()) {
                var metadata = container.get().getMetadata();
                data.addProperty("mod_id", metadata.getId());
                data.addProperty("mod_name", metadata.getName());
                data.addProperty("mod_version", metadata.getVersion().getFriendlyString());
            } else {
                data.addProperty("mod_id", "baritone-api-bridge");
                data.addProperty("mod_version", "unknown");
            }
            return CommandResult.success(data);
        } catch (Exception e) {
            return CommandResult.error("Failed to read mod version: " + e.getMessage());
        }
    }
}
