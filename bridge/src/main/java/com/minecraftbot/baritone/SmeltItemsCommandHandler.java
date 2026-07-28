package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.AbstractFurnaceMenu;
import net.minecraft.world.inventory.ContainerInput;

public class SmeltItemsCommandHandler extends AsyncCommandHandler {

    @Override
    public String getCommandName() {
        return "smelt_items";
    }

    @Override
    public CompletableFuture<CommandResult> execute(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        return executeOnMainThread(client, () -> {
            if (client.player == null || client.gameMode == null) {
                return CommandResult.error("Player not available");
            }

            AbstractContainerMenu handler = client.player.containerMenu;
            if (!(handler instanceof AbstractFurnaceMenu)) {
                return CommandResult.error("Not in a furnace screen");
            }

            // Slot 0: Input, Slot 1: Fuel, Slot 2: Output
            int inputSlot = params.has("input_slot") ? params.get("input_slot").getAsInt() : -1;
            int fuelSlot = params.has("fuel_slot") ? params.get("fuel_slot").getAsInt() : -1;

            if (inputSlot != -1) {
                client.gameMode.handleContainerInput(handler.containerId, inputSlot, 0, ContainerInput.QUICK_MOVE, client.player);
            }
            if (fuelSlot != -1) {
                client.gameMode.handleContainerInput(handler.containerId, fuelSlot, 0, ContainerInput.QUICK_MOVE, client.player);
            }

            JsonObject data = new JsonObject();
            data.addProperty("moved", true);
            
            return CommandResult.success(data);
        });
    }
}
