package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.gl.Framebuffer;
import net.minecraft.client.texture.NativeImage;
import net.minecraft.client.util.ScreenshotRecorder;
import net.minecraft.entity.player.PlayerEntity;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.File;
import java.io.IOException;
import java.net.Socket;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.concurrent.CompletableFuture;
import java.util.Set;

/**
 * Enhanced ScreenshotCommandHandler with advanced screenshot capabilities.
 * Supports multiple formats, metadata inclusion, custom naming, and organized
 * file storage.
 */
public class ScreenshotCommandHandler extends AsyncCommandHandler {

    private static final Logger LOGGER = LoggerFactory.getLogger(ScreenshotCommandHandler.class);
    private static final Set<String> SUPPORTED_FORMATS = Set.of("PNG", "JPEG", "BMP");
    private static final String SCREENSHOT_DIR = "screenshots";

    @Override
    public String getCommandName() {
        return "screenshot";
    }

    @Override
    public CompletableFuture<CommandResult> execute(JsonObject params, MinecraftClient client, IBaritone baritone,
            Socket clientSocket) {
        // Validate parameters
        CommandResult validation = validateScreenshotParameters(params);
        if (validation != null) {
            return CompletableFuture.completedFuture(validation);
        }

        ScreenshotOptions options = parseScreenshotOptions(params);

        return executeOnMainThread(client, () -> {
            if (client.player == null || client.world == null) {
                return CommandResult.error("Player or world not available");
            }

            try {
                // Take screenshot
                ScreenshotResult result = captureScreenshot(client, options);

                // Create response
                JsonObject data = new JsonObject();
                data.addProperty("saved", result.success);
                data.addProperty("file_path", result.filePath);
                data.addProperty("file_name", result.fileName);
                data.addProperty("format", options.format);

                if (result.success) {
                    // Add metadata
                    JsonObject metadata = createMetadata(client, options);
                    data.add("metadata", metadata);

                    LOGGER.info("Screenshot saved successfully: {}", result.filePath);
                } else {
                    data.addProperty("error", result.error);
                    LOGGER.error("Screenshot failed: {}", result.error);
                }

                return CommandResult.success(data);

            } catch (Exception e) {
                LOGGER.error("Screenshot capture failed", e);
                JsonObject data = new JsonObject();
                data.addProperty("saved", false);
                data.addProperty("error", "Screenshot capture failed: " + e.getMessage());
                return CommandResult.success(data);
            }
        });
    }

    /**
     * Validates screenshot parameters.
     */
    private CommandResult validateScreenshotParameters(JsonObject params) {
        if (params.has("format")) {
            String format = params.get("format").getAsString().toUpperCase();
            if (!SUPPORTED_FORMATS.contains(format)) {
                return CommandResult.error("Unsupported format: " + format + ". Supported: PNG, JPEG, BMP");
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

        options.customName = params.has("custom_name") ? params.get("custom_name").getAsString() : null;

        options.includeMetadata = params.has("include_metadata") ? params.get("include_metadata").getAsBoolean() : true;

        options.subdirectory = params.has("subdirectory") ? params.get("subdirectory").getAsString() : null;

        return options;
    }

    /**
     * Captures screenshot using Minecraft's screenshot API.
     */
    private ScreenshotResult captureScreenshot(MinecraftClient client, ScreenshotOptions options) {
        ScreenshotResult result = new ScreenshotResult();

        try {
            // Get framebuffer
            Framebuffer framebuffer = client.getFramebuffer();
            if (framebuffer == null) {
                result.success = false;
                result.error = "No framebuffer available";
                return result;
            }

            // Create screenshot directory
            Path screenshotDir = createScreenshotDirectory(options);
            if (screenshotDir == null) {
                result.success = false;
                result.error = "Failed to create screenshot directory";
                return result;
            }

            // Generate filename
            String fileName = generateFileName(options);
            Path filePath = screenshotDir.resolve(fileName);

            // Take screenshot using Minecraft API
            try {
                ScreenshotRecorder.saveScreenshot(
                        client.runDirectory,
                        framebuffer,
                        msg -> {
                        });

                // Verify file was created
                if (Files.exists(filePath)) {
                    result.success = true;
                    result.filePath = filePath.toString();
                    result.fileName = fileName;
                } else {
                    result.success = false;
                    result.error = "Screenshot file was not created";
                }
            } catch (Exception e) {
                result.success = false;
                result.error = "ScreenshotRecorder exception: " + e.getMessage();
            }

        } catch (Exception e) {
            result.success = false;
            result.error = "Exception during screenshot: " + e.getMessage();
            LOGGER.error("Screenshot capture exception", e);
        }

        return result;
    }

    /**
     * Creates the screenshot directory structure.
     */
    private Path createScreenshotDirectory(ScreenshotOptions options) {
        try {
            Path baseDir = Paths.get(SCREENSHOT_DIR);

            if (options.subdirectory != null && !options.subdirectory.trim().isEmpty()) {
                baseDir = baseDir.resolve(options.subdirectory.trim());
            }

            Files.createDirectories(baseDir);
            return baseDir;

        } catch (IOException e) {
            LOGGER.error("Failed to create screenshot directory", e);
            return null;
        }
    }

    /**
     * Generates filename with timestamp and custom name if provided.
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

        return String.format("%s_%s.%s", baseName, timestamp, options.format.toLowerCase());
    }

    /**
     * Creates metadata JSON object.
     */
    private JsonObject createMetadata(MinecraftClient client, ScreenshotOptions options) {
        JsonObject metadata = new JsonObject();

        // Timestamp
        metadata.addProperty("timestamp", LocalDateTime.now().toString());

        // Player information
        PlayerEntity player = client.player;
        if (player != null) {
            metadata.addProperty("player_name", player.getName().getString());
            metadata.addProperty("player_uuid", player.getUuid().toString());

            // Position
            JsonObject position = new JsonObject();
            position.addProperty("x", player.getX());
            position.addProperty("y", player.getY());
            position.addProperty("z", player.getZ());
            metadata.add("position", position);

            // Health and food
            metadata.addProperty("health", player.getHealth());
            metadata.addProperty("max_health", player.getMaxHealth());
            metadata.addProperty("hunger", player.getHungerManager().getFoodLevel());
        }

        // World information
        if (client.world != null) {
            metadata.addProperty("dimension", client.world.getRegistryKey().getValue().toString());
            metadata.addProperty("time", client.world.getTime());
            metadata.addProperty("difficulty", client.world.getDifficulty().getName());
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

    /**
     * Data class for screenshot results.
     */
    private static class ScreenshotResult {
        boolean success = false;
        String filePath = null;
        String fileName = null;
        String error = null;
    }
}
