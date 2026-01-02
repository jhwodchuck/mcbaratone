package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.network.packet.c2s.play.UpdateSelectedSlotC2SPacket;
import java.net.Socket;

public class SelectSlotCommandHandler implements CommandHandler {

    @Override
    public CommandResult handle(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        if (client.player == null) return CommandResult.error("Player not available");
        
        try {
            int slot = params.get("slot").getAsInt();
            if (slot < 0 || slot > 8) {
                return CommandResult.error("Invalid slot (0-8): " + slot);
            }
            
            client.submit(() -> {
                client.player.getInventory().selectedSlot = slot;
                client.player.networkHandler.sendPacket(new UpdateSelectedSlotC2SPacket(slot));
                return null;
            }).get();
            
            return CommandResult.success();
        } catch (Exception e) {
            return CommandResult.error("Select slot failed: " + e.getMessage());
        }
    }

    @Override
    public String getCommandName() {
        return "select_slot";
    }
}
