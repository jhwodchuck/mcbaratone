package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import com.mojang.blaze3d.pipeline.RenderTarget;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.Socket;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;
import net.minecraft.client.Screenshot;
import net.minecraft.world.entity.player.Player;
import java.util.Set;

/**
 * Enhanced ScreenshotCommandHandler with advanced screenshot capabilities.
 * Supports multiple formats, metadata inclusion, custom naming, and organized
 * file storage.
 */
public class ScreenshotCommandHandler extends AsyncCommandHandler {

    private static final Logger LOGGER = LoggerFactory.getLogger(ScreenshotCommandHandler.class);
    // The vanilla ScreenshotRecorder only ever writes PNG.
    private static final Set<String> SUPPORTED_FORMATS = Set.of("PNG");
    private static final String SCREENSHOT_DIR = "screenshots";
    // Distinguishes screenshots requested within the same wall-clock second.
    private static final java.util.concurrent.atomic.AtomicLong SEQUENCE = new java.util.concurrent.atomic.AtomicLong();

    @Override
    public String getCommandName() {
        return "screenshot";
    }

    @Override
    public CompletableFuture<CommandResult> execute(JsonObject params, Minecraft client, IBaritone baritone,
            Socket clientSocket) {
        // Validate parameters
        CommandResult validation = validateScreenshotParameters(params);
        if (validation != null) {
            return CompletableFuture.completedFuture(validation);
        }

        ScreenshotOptions options = parseScreenshotOptions(params);
        String fileName = generateFileName(options);

        CompletableFuture<CommandResult> future = new CompletableFuture<>();

        executeOnMainThread(client, () -> {
            try {
                if (client.player == null || client.level == null) {
                    future.complete(CommandResult.error("Player or world not available"));
                    return;
                }

                RenderTarget framebuffer = client.gameRenderer.mainRenderTarget();
                if (framebuffer == null) {
                    future.complete(CommandResult.error("No framebuffer available"));
                    return;
                }

                // Passing an explicit fileName pins the output to
                // <runDirectory>/screenshots/<fileName>; the recorder writes on an IO
                // worker and invokes the message callback when the attempt finishes,
                // so completion (not this call) is when the file can be verified.
                Path expected = client.gameDirectory.toPath().resolve(SCREENSHOT_DIR).resolve(fileName);
                Screenshot.grab(client.gameDirectory, fileName, framebuffer, 1, msg -> {
                    try {
                        JsonObject data = new JsonObject();
                        boolean exists = Files.exists(expected);
                        data.addProperty("saved", exists);
                        data.addProperty("path", expected.toString());
                        data.addProperty("file_path", expected.toString());
                        data.addProperty("file_name", fileName);
                        data.addProperty("format", "PNG");
                        if (exists) {
                            if (options.includeMetadata) {
                                data.add("metadata", createMetadata(client, options));
                            }
                            LOGGER.info("Screenshot saved successfully: {}", expected);
                        } else {
                            String detail = msg != null ? msg.getString() : "unknown";
                            data.addProperty("error", "Screenshot file was not created: " + detail);
                            LOGGER.error("Screenshot failed: {}", detail);
                        }
                        future.complete(CommandResult.success(data));
                    } catch (Exception e) {
                        future.complete(CommandResult.error("Screenshot completion failed: " + e.getMessage()));
                    }
                });
            } catch (Exception e) {
                LOGGER.error("Screenshot capture failed", e);
                future.complete(CommandResult.error("Screenshot capture failed: " + e.getMessage()));
            }
        });

        return future.completeOnTimeout(
                CommandResult.error("Screenshot timed out waiting for file write"),
                10, java.util.concurrent.TimeUnit.SECONDS);
    }

    /**
     * Validates screenshot parameters.
     */
    private CommandResult validateScreenshotParameters(JsonObject params) {
        if (params.has("format")) {
            String format = params.get("format").getAsString().toUpperCase();
            if (!SUPPORTED_FORMATS.contains(format)) {
                return CommandResult.error("Unsupported format: " + format + ". Supported: PNG");
            }
        }

        if (params.has("custom_name") && params.get("custom_name").getAsString().trim().isEmpty()) {
            return CommandResult.error("Custom name cannot be empty");
        }

        return null; // Valid
    }

    /**
     * Parses screenshot options from parameters.
     */
    private ScreenshotOptions parseScreenshotOptions(JsonObject params) {
        ScreenshotOptions options = new ScreenshotOptions();

        options.format = params.has("format") ? params.get("format").getAsString().toUpperCase() : "PNG";

        // The Python facade sends "filename"; "custom_name" kept for compatibility.
        if (params.has("filename")) {
            options.customName = params.get("filename").getAsString();
        } else if (params.has("custom_name")) {
            options.customName = params.get("custom_name").getAsString();
        }

        options.includeMetadata = params.has("include_metadata") ? params.get("include_metadata").getAsBoolean() : true;

        options.subdirectory = params.has("subdirectory") ? params.get("subdirectory").getAsString() : null;

        return options;
    }

    /**
     * Generates a unique filename: the vanilla recorder overwrites silently when
     * given an explicit name, and phase transitions can request several
     * screenshots within one second.
     */
    private String generateFileName(ScreenshotOptions options) {
        String timestamp = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd_HH-mm-ss"));

        String baseName = "screenshot";
        if (options.customName != null && !options.customName.trim().isEmpty()) {
            // Sanitize custom name
            String sanitized = options.customName.trim()
                    .replaceAll("[^a-zA-Z0-9_-]", "_")
                    .replaceAll("__+", "_");
            if (!sanitized.isEmpty()) {
                baseName = sanitized;
            }
        }

        return String.format("%s_%s_%d.png", baseName, timestamp, SEQUENCE.incrementAndGet());
    }

    /**
     * Creates metadata JSON object.
     */
    private JsonObject createMetadata(Minecraft client, ScreenshotOptions options) {
        JsonObject metadata = new JsonObject();

        // Timestamp
        metadata.addProperty("timestamp", LocalDateTime.now().toString());

        // Player information
        Player player = client.player;
        if (player != null) {
            metadata.addProperty("player_name", player.getName().getString());
            metadata.addProperty("player_uuid", player.getUUID().toString());

            // Position
            JsonObject position = new JsonObject();
            position.addProperty("x", player.getX());
            position.addProperty("y", player.getY());
            position.addProperty("z", player.getZ());
            metadata.add("position", position);

            // Health and food
            metadata.addProperty("health", player.getHealth());
            metadata.addProperty("max_health", player.getMaxHealth());
            metadata.addProperty("hunger", player.getFoodData().getFoodLevel());
        }

        // World information
        if (client.level != null) {
            metadata.addProperty("dimension", client.level.dimension().identifier().toString());
            metadata.addProperty("time", client.level.getGameTime());
            metadata.addProperty("difficulty", client.level.getDifficulty().getSerializedName());
        }

        // Game version and mod info
        metadata.addProperty("minecraft_version", "1.21.4");
        metadata.addProperty("baritone_version", "1.15.0");

        // Capture settings
        metadata.addProperty("format", options.format);
        if (options.customName != null) {
            metadata.addProperty("custom_name", options.customName);
        }

        return metadata;
    }

    /**
     * Data class for screenshot options.
     */
    private static class ScreenshotOptions {
        String format = "PNG";
        String customName = null;
        boolean includeMetadata = true;
        String subdirectory = null;
    }

}
