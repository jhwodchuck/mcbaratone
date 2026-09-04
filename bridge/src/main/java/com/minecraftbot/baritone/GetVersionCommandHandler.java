package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.Minecraft;
import java.net.Socket;
import java.io.InputStream;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.UUID;

/**
 * Handler for get_version command.
 * Returns bridge mod version and metadata to verify installed jar.
 */
public class GetVersionCommandHandler extends AbstractCommandHandler {

    private static final int PROTOCOL_VERSION = 2;
    private static final String BRIDGE_INSTANCE_ID = UUID.randomUUID().toString();
    private static final String ARTIFACT_SHA256 = calculateArtifactSha256();

    @Override
    public String getCommandName() {
        return "get_version";
    }

    public static String getBridgeInstanceId() {
        return BRIDGE_INSTANCE_ID;
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
            data.addProperty("protocol_version", PROTOCOL_VERSION);
            data.addProperty("schema_version", PROTOCOL_VERSION);
            data.addProperty("bridge_instance_id", BRIDGE_INSTANCE_ID);
            data.addProperty("artifact_sha256", ARTIFACT_SHA256);
            addModVersion(data, loader, "minecraft", "minecraft_version");
            addModVersion(data, loader, "fabricloader", "fabric_loader_version");
            addModVersion(data, loader, "fabric-api", "fabric_api_version");
            addModVersion(data, loader, "baritone", "baritone_version");

            JsonObject capabilities = new JsonObject();
            capabilities.addProperty("structured_errors", true);
            capabilities.addProperty("observed_mutations_v1", true);
            capabilities.addProperty("mutation_request_deduplication_v1", true);
            capabilities.addProperty("water_bucket_postconditions", true);
            capabilities.addProperty("request_seq_echo", true);
            capabilities.addProperty("event_cursors", true);
            capabilities.addProperty("fresh_live_reads", true);
            capabilities.addProperty("mutation_auto_retry", false);
            capabilities.addProperty("shared_death_tracker", true);
            capabilities.addProperty("legacy_event_drain", true);
            data.add("capabilities", capabilities);
            return CommandResult.success(data);
        } catch (Exception e) {
            return CommandResult.error("Failed to read mod version: " + e.getMessage());
        }
    }

    private static void addModVersion(
            JsonObject data,
            FabricLoader loader,
            String modId,
            String fieldName) {
        loader.getModContainer(modId).ifPresent(container -> data.addProperty(
                fieldName,
                container.getMetadata().getVersion().getFriendlyString()));
    }

    private static String calculateArtifactSha256() {
        try {
            URI location = GetVersionCommandHandler.class
                    .getProtectionDomain()
                    .getCodeSource()
                    .getLocation()
                    .toURI();
            Path artifact = Path.of(location);
            if (!Files.isRegularFile(artifact)) {
                return "development-classes";
            }
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            try (InputStream input = Files.newInputStream(artifact)) {
                byte[] buffer = new byte[8192];
                int read;
                while ((read = input.read(buffer)) != -1) {
                    digest.update(buffer, 0, read);
                }
            }
            return HexFormat.of().formatHex(digest.digest());
        } catch (Exception e) {
            return "unavailable";
        }
    }
}
