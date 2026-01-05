package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;

import java.net.Socket;

/**
 * Handler for the throw_item command - throws item from inventory.
 */
public class ThrowItemCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "throw_item";
    }

    @Override
    protected CommandResult execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        if (client.player == null) {
            return CommandResult.error("Player not available");
        }

        int slot = params.has("slot") ? params.get("slot").getAsInt() : -1;
        boolean all = params.has("all") && params.get("all").getAsBoolean();

        executeOnMainThread(client, () -> {
            if (slot >= 0 && slot < 36) {
                // Throw specific slot
                client.player.getInventory().selectedSlot = slot % 9;
                if (all) {
                    client.player.dropSelectedItem(true);
                } else {
                    client.player.dropSelectedItem(false);
                }
            } else {
                // Throw currently held item
                if (all) {
                    client.player.dropSelectedItem(true);
                } else {
                    client.player.dropSelectedItem(false);
                }
            }
        });

        JsonObject data = new JsonObject();
        data.addProperty("thrown", true);
        data.addProperty("slot", slot);
        data.addProperty("all", all);
        return CommandResult.success(data);
    }
}
