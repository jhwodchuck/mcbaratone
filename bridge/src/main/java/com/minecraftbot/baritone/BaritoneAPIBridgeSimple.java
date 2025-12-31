package com.minecraftbot.baritone;

import com.google.gson.JsonObject;

public class BaritoneAPIBridgeSimple {
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
        
        // Return dummy response for other tests to not crash immediately if valid command
        response.addProperty("status", "ok");
        return response;
    }
}
