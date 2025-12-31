package com.minecraftbot.baritone;

import com.google.gson.JsonObject;

public class BridgeController {
    private final BridgeContext context;

    public BridgeController(BridgeContext context) {
        this.context = context;
    }

    public JsonObject handleCommand(JsonObject request) {
        JsonObject response = new JsonObject();
        
        if (request.has("id")) {
            response.addProperty("id", request.get("id").getAsString());
        }

        if (!request.has("command")) {
            response.addProperty("status", "error");
            response.addProperty("error", "Missing command");
            return response;
        }

        String command = request.get("command").getAsString();

        // Check player availability
        if (!context.isPlayerAvailable()) {
             response.addProperty("status", "error");
             response.addProperty("error", "Player not available");
             return response;
        }

        try {
            switch (command) {
                case "goto":
                    JsonObject paramsGoto = request.getAsJsonObject("params");
                    int x = paramsGoto.get("x").getAsInt();
                    int y = paramsGoto.get("y").getAsInt();
                    int z = paramsGoto.get("z").getAsInt();
                    int radius = paramsGoto.has("radius") ? paramsGoto.get("radius").getAsInt() : 0;
                    context.setGoal(x, y, z, radius);
                    response.addProperty("status", "ok");
                    break;
                case "mine":
                    JsonObject paramsMine = request.getAsJsonObject("params");
                    String blockType = paramsMine.get("block_type").getAsString();
                    context.mine(blockType);
                    response.addProperty("status", "ok");
                    break;
                case "chat":
                    JsonObject paramsChat = request.getAsJsonObject("params");
                    String message = paramsChat.get("message").getAsString();
                    context.sendChat(message);
                    response.addProperty("status", "ok");
                    break;
                default:
                    response.addProperty("status", "error");
                    response.addProperty("error", "Unknown command: " + command);
            }
        } catch (Exception e) {
            response.addProperty("status", "error");
            response.addProperty("error", e.getMessage());
        }

        return response;
    }
}
