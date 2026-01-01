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
        
        // Handle a few simple commands used by tests and clients. These are stubs
        // that return plausible responses so higher-level Python code can progress
        // during unit testing without a full Baritone backend.
        String cmd = request.get("command").getAsString();

        switch (cmd) {
            case "craft": {
                // params: { "recipe_id": "...", "count": N }
                JsonObject data = new JsonObject();
                data.addProperty("crafted", true);
                // Return a plausible output slot index
                data.addProperty("output_slot", 5);
                response.add("data", data);
                response.addProperty("status", "ok");
                return response;
            }
            case "smelt": {
                // params: { "input_item": "...", "count": N, "fuel_item": "..." }
                JsonObject data = new JsonObject();
                data.addProperty("smelted", true);
                // For simplicity echo back the requested count as output_count
                if (request.has("params") && request.getAsJsonObject("params").has("count")) {
                    int cnt = request.getAsJsonObject("params").get("count").getAsInt();
                    data.addProperty("output_count", cnt);
                } else {
                    data.addProperty("output_count", 1);
                }
                response.add("data", data);
                response.addProperty("status", "ok");
                return response;
            }
            default: {
                // Return dummy ok for unhandled commands to keep tests simple
                response.addProperty("status", "ok");
                return response;
            }
        }
    }
}
