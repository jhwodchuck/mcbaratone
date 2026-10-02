package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;
import net.minecraft.network.protocol.game.ServerboundSetCarriedItemPacket;

public class SelectSlotCommandHandler extends AsyncCommandHandler {
    private final java.util.function.Consumer<Minecraft> inventoryLease;

    public SelectSlotCommandHandler() {
        this(InventoryMutationLease::hold);
    }

    SelectSlotCommandHandler(java.util.function.Consumer<Minecraft> inventoryLease) {
        this.inventoryLease = inventoryLease;
    }

    private static final int ACTION_SCHEDULE_TIMEOUT_SECONDS = 2;

    @Override
    public CompletableFuture<CommandResult> execute(JsonObject params, Minecraft client,
            IBaritone baritone, Socket clientSocket) {
        final int slot;
        try {
            slot = params.get("slot").getAsInt();
        } catch (Exception e) {
            return CompletableFuture.completedFuture(CommandResult.error("Select slot failed: " + e.getMessage()));
        }
        if (!validSlot(slot)) {
            return CompletableFuture.completedFuture(
                CommandResult.error("Invalid slot (0-8): " + slot));
        }

        return executeOnMainThread(client, () -> {
            if (client.player == null || client.player.connection == null) {
                return CommandResult.error("Player not available");
            }
            // Protect the selected item until the following use/placement action.
            inventoryLease.accept(client);
            client.player.getInventory().selected = slot;
            client.player.connection.send(new ServerboundSetCarriedItemPacket(slot));
            JsonObject data = new JsonObject();
            data.addProperty("selected_slot", slot);
            return CommandResult.success(data);
        }).completeOnTimeout(
            CommandResult.error("Timed out before slot selection reached the client thread"),
            ACTION_SCHEDULE_TIMEOUT_SECONDS,
            java.util.concurrent.TimeUnit.SECONDS);
    }

    static boolean validSlot(int slot) {
        return slot >= 0 && slot <= 8;
    }

    @Override
    public String getCommandName() {
        return "select_slot";
    }
}
