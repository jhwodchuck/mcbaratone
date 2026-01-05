package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.net.Socket;
import java.util.Base64;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;

/**
 * CommandHandler for schematic upload operations.
 * Handles commands: schematic_init, schematic_chunk, schematic_commit,
 * upload_progress, upload_list, upload_cancel, upload_stats
 */
public class SchematicUploadHandler implements CommandHandler {
    
    private static final Logger LOGGER = LoggerFactory.getLogger("schematic-upload-handler");
    
    private final UploadManager uploadManager;
    
    /**
     * Create a SchematicUploadHandler.
     * 
     * @param uploadManager The upload manager for handling file uploads
     */
    public SchematicUploadHandler(UploadManager uploadManager) {
        this.uploadManager = uploadManager;
    }
    
    /**
     * Check if this handler can process the given command.
     * 
     * @param command The command name
     * @return true if this handler handles the command
     */
    public boolean canHandle(String command) {
        return command != null && (
            command.startsWith("schematic_") || 
            command.startsWith("upload_")
        );
    }
    
    @Override
    public String getCommandName() {
        return "schematic_upload"; // Handler name for multiple commands
    }
    
    @Override
    public boolean isOfflineSafe() {
        return true; // Upload commands work without player
    }
    
    @Override
    public CompletableFuture<CommandResult> handle(JsonObject params, MinecraftClient client, 
            IBaritone baritone, Socket clientSocket) {
        return CompletableFuture.supplyAsync(() -> {
            try {
                String command = params.has("command") ? params.get("command").getAsString() : "";
                JsonObject data = new JsonObject();
                
                switch (command) {
                    case "schematic_init":
                        return handleSchematicInit(params, clientSocket);
                    case "schematic_chunk":
                        return handleSchematicChunk(params);
                    case "schematic_commit":
                        return handleSchematicCommit(params);
                    case "upload_progress":
                        return handleUploadProgress(params);
                    case "upload_list":
                        return handleUploadList();
                    case "upload_cancel":
                        return handleUploadCancel(params);
                    case "upload_stats":
                        return handleUploadStats();
                    default:
                        return CommandResult.error("Unknown upload command: " + command);
                }
            } catch (Exception e) {
                LOGGER.error("Error in schematic upload handler", e);
                return CommandResult.error("Upload operation failed: " + e.getMessage());
            }
        });
    }
    
    /**
     * Initialize a new schematic upload.
     */
    private CommandResult handleSchematicInit(JsonObject params, Socket clientSocket) {
        try {
            if (!params.has("name")) {
                return CommandResult.error("Missing required parameter: name");
            }
            
            String name = params.get("name").getAsString();
            long expectedSize = params.has("size") ? params.get("size").getAsLong() : -1;
            UploadManager.UploadPriority priority = params.has("priority") ?
                UploadManager.UploadPriority.valueOf(params.get("priority").getAsString().toUpperCase()) :
                UploadManager.UploadPriority.NORMAL;
            
            boolean started = uploadManager.startUpload(name, expectedSize, clientSocket, priority);
            
            JsonObject data = new JsonObject();
            if (started) {
                data.addProperty("ready", true);
                return CommandResult.success(data);
            } else {
                return CommandResult.error("Failed to start upload - queue full or upload already exists");
            }
        } catch (Exception e) {
            return CommandResult.error("Failed to initialize upload: " + e.getMessage());
        }
    }
    
    /**
     * Process a chunk of upload data.
     */
    private CommandResult handleSchematicChunk(JsonObject params) {
        try {
            if (!params.has("name") || !params.has("data")) {
                return CommandResult.error("Missing required parameters: name, data");
            }
            
            String name = params.get("name").getAsString();
            String b64 = params.get("data").getAsString();
            byte[] bytes = Base64.getDecoder().decode(b64);
            
            boolean success = uploadManager.processChunk(name, bytes);
            
            JsonObject data = new JsonObject();
            if (success) {
                data.addProperty("received", bytes.length);
                return CommandResult.success(data);
            } else {
                return CommandResult.error("Failed to process chunk for upload: " + name);
            }
        } catch (IllegalArgumentException e) {
            return CommandResult.error("Invalid base64 data: " + e.getMessage());
        } catch (Exception e) {
            return CommandResult.error("Failed to process chunk: " + e.getMessage());
        }
    }
    
    /**
     * Complete an upload and save the schematic.
     */
    private CommandResult handleSchematicCommit(JsonObject params) {
        try {
            if (!params.has("name")) {
                return CommandResult.error("Missing required parameter: name");
            }
            
            String name = params.get("name").getAsString();
            String expectedSha256 = params.has("sha256") ? params.get("sha256").getAsString() : null;
            
            boolean success = uploadManager.completeUpload(name, expectedSha256);
            
            JsonObject data = new JsonObject();
            if (success) {
                data.addProperty("saved", true);
                // Add SHA256 hash if available
                Map<String, Object> progress = uploadManager.getUploadProgress(name);
                if (progress.containsKey("sha256")) {
                    data.addProperty("sha256", progress.get("sha256").toString());
                }
                return CommandResult.success(data);
            } else {
                return CommandResult.error("Failed to complete upload: " + name);
            }
        } catch (Exception e) {
            return CommandResult.error("Failed to commit upload: " + e.getMessage());
        }
    }
    
    /**
     * Get progress of a specific upload.
     */
    private CommandResult handleUploadProgress(JsonObject params) {
        try {
            if (!params.has("name")) {
                return CommandResult.error("Missing required parameter: name");
            }
            
            String name = params.get("name").getAsString();
            Map<String, Object> progress = uploadManager.getUploadProgress(name);
            
            if (progress.containsKey("error")) {
                return CommandResult.error(progress.get("error").toString());
            }
            
            JsonObject data = new JsonObject();
            data.addProperty("name", progress.get("name").toString());
            data.addProperty("status", progress.get("status").toString());
            data.addProperty("progress_percentage", (Double) progress.get("progress_percentage"));
            data.addProperty("received_bytes", (Long) progress.get("received_bytes"));
            data.addProperty("expected_size", (Long) progress.get("expected_size"));
            data.addProperty("start_time", (Long) progress.get("start_time"));
            data.addProperty("last_activity", (Long) progress.get("last_activity"));
            data.addProperty("priority", progress.get("priority").toString());
            
            return CommandResult.success(data);
        } catch (Exception e) {
            return CommandResult.error("Failed to get upload progress: " + e.getMessage());
        }
    }
    
    /**
     * List all active uploads.
     */
    private CommandResult handleUploadList() {
        try {
            List<Map<String, Object>> uploads = uploadManager.getActiveUploads();
            
            JsonArray uploadArray = new JsonArray();
            for (Map<String, Object> upload : uploads) {
                JsonObject uploadObj = new JsonObject();
                uploadObj.addProperty("name", upload.get("name").toString());
                uploadObj.addProperty("status", upload.get("status").toString());
                uploadObj.addProperty("progress_percentage", (Double) upload.get("progress_percentage"));
                uploadObj.addProperty("received_bytes", (Long) upload.get("received_bytes"));
                uploadObj.addProperty("expected_size", (Long) upload.get("expected_size"));
                uploadObj.addProperty("priority", upload.get("priority").toString());
                uploadObj.addProperty("owner", upload.get("owner").toString());
                uploadArray.add(uploadObj);
            }
            
            JsonObject data = new JsonObject();
            data.add("uploads", uploadArray);
            return CommandResult.success(data);
        } catch (Exception e) {
            return CommandResult.error("Failed to list uploads: " + e.getMessage());
        }
    }
    
    /**
     * Cancel an upload.
     */
    private CommandResult handleUploadCancel(JsonObject params) {
        try {
            if (!params.has("name")) {
                return CommandResult.error("Missing required parameter: name");
            }
            
            String name = params.get("name").getAsString();
            boolean success = uploadManager.cancelUpload(name);
            
            JsonObject data = new JsonObject();
            data.addProperty("cancelled", success);
            if (!success) {
                data.addProperty("error", "Upload not found or could not be cancelled");
            }
            return CommandResult.success(data);
        } catch (Exception e) {
            return CommandResult.error("Failed to cancel upload: " + e.getMessage());
        }
    }
    
    /**
     * Get upload statistics.
     */
    private CommandResult handleUploadStats() {
        try {
            Map<String, Object> stats = uploadManager.getStatistics();
            
            JsonObject data = new JsonObject();
            data.addProperty("active_uploads", (Integer) stats.get("active_uploads"));
            data.addProperty("queued_uploads", (Integer) stats.get("queued_uploads"));
            data.addProperty("total_completed", (Long) stats.get("total_completed"));
            data.addProperty("total_failed", (Long) stats.get("total_failed"));
            data.addProperty("total_bytes_uploaded", (Long) stats.get("total_bytes_uploaded"));
            data.addProperty("max_concurrent_uploads", (Integer) stats.get("max_concurrent_uploads"));
            data.addProperty("max_queue_size", (Integer) stats.get("max_queue_size"));
            
            return CommandResult.success(data);
        } catch (Exception e) {
            return CommandResult.error("Failed to get upload stats: " + e.getMessage());
        }
    }
}
