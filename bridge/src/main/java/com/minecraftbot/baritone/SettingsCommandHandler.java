package com.minecraftbot.baritone;

import baritone.api.BaritoneAPI;
import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;

import java.net.Socket;

/**
 * Handler for the settings command - modifies Baritone settings.
 */
public class SettingsCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "settings";
    }

    @Override
    protected CommandResult execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        JsonObject data = new JsonObject();

        if (params.has("get")) {
            String key = params.get("get").getAsString();
            try {
                baritone.api.Settings.Setting<?> setting = BaritoneAPI.getSettings().allSettings.stream()
                    .filter(s -> s.getName().equalsIgnoreCase(key))
                    .findFirst().orElse(null);

                if (setting != null) {
                    data.addProperty("key", setting.getName());
                    data.addProperty("value", setting.value.toString());
                } else {
                    return CommandResult.error("Setting not found: " + key);
                }
            } catch (Exception e) {
                return CommandResult.error("Failed to get setting: " + e.getMessage());
            }
        } else if (params.has("set")) {
            String key = params.get("set").getAsString();
            String val = params.get("value").getAsString();

            try {
                executeOnMainThread(client, () -> {
                    baritone.api.Settings.Setting<?> setting = BaritoneAPI.getSettings().allSettings.stream()
                        .filter(s -> s.getName().equalsIgnoreCase(key))
                        .findFirst().orElse(null);

                    if (setting != null) {
                        try {
                            modifySettingSafely(setting, val);
                            logger.info("Successfully set Baritone setting {} to {}", key, val);
                        } catch (Exception e) {
                            logger.error("Failed to set Baritone setting {}: {}", key, e.getMessage());
                        }
                    }
                });
                data.addProperty("status", "requested");
            } catch (Exception e) {
                return CommandResult.error("Failed to dispatch setting update: " + e.getMessage());
            }
        } else {
            return CommandResult.error("Missing 'get' or 'set' parameter");
        }

        return CommandResult.success(data);
    }

    @SuppressWarnings("unchecked")
    private void modifySettingSafely(baritone.api.Settings.Setting<?> setting, String val) {
        Class<?> type = (Class<?>) setting.getType();
        if (type == Boolean.class) {
            ((baritone.api.Settings.Setting<Boolean>) setting).value = Boolean.parseBoolean(val);
        } else if (type == Integer.class) {
            ((baritone.api.Settings.Setting<Integer>) setting).value = Integer.parseInt(val);
        } else if (type == Double.class) {
            ((baritone.api.Settings.Setting<Double>) setting).value = Double.parseDouble(val);
        } else if (type == Float.class) {
            ((baritone.api.Settings.Setting<Float>) setting).value = Float.parseFloat(val);
        } else if (type == Long.class) {
            ((baritone.api.Settings.Setting<Long>) setting).value = Long.parseLong(val);
        }
        // Other types would need more complex parsing
    }

    @Override
    public boolean requiresPlayer() {
        return false;
    }
}
