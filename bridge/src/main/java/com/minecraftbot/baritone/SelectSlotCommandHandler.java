package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;
import net.minecraft.network.protocol.game.ServerboundSetCarriedItemPacket;

public class SelectSlotCommandHandler implements CommandHandler {

    @Override
    public CompletableFuture<CommandResult> handle(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        if (client.player == null) {
            return CompletableFuture.completedFuture(CommandResult.error("Player not available"));
        }
        
        try {
            int slot = params.get("slot").getAsInt();
            if (slot < 0 || slot > 8) {
                return CompletableFuture.completedFuture(CommandResult.error("Invalid slot (0-8): " + slot));
            }
            
            client.submit(() -> {
                client.player.getInventory().selected = slot;
                client.player.connection.send(new ServerboundSetCarriedItemPacket(slot));
                return null;
            }).get();
            
            return CompletableFuture.completedFuture(CommandResult.success());
        } catch (Exception e) {
            return CompletableFuture.completedFuture(CommandResult.error("Select slot failed: " + e.getMessage()));
        }
    }

    @Override
    public String getCommandName() {
        return "select_slot";
    }
}
