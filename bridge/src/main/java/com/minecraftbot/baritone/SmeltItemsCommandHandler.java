package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.screen.AbstractFurnaceScreenHandler;
import net.minecraft.screen.ScreenHandler;
import net.minecraft.screen.slot.SlotActionType;

import java.net.Socket;
import java.util.concurrent.CompletableFuture;

public class SmeltItemsCommandHandler extends AsyncCommandHandler {

    @Override
    public String getCommandName() {
        return "smelt_items";
    }

    @Override
    public CompletableFuture<CommandResult> execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        return executeOnMainThread(client, () -> {
            if (client.player == null || client.interactionManager == null) {
                return CommandResult.error("Player not available");
            }

            ScreenHandler handler = client.player.currentScreenHandler;
            if (!(handler instanceof AbstractFurnaceScreenHandler)) {
                return CommandResult.error("Not in a furnace screen");
            }

            // Slot 0: Input, Slot 1: Fuel, Slot 2: Output
            int inputSlot = params.has("input_slot") ? params.get("input_slot").getAsInt() : -1;
            int fuelSlot = params.has("fuel_slot") ? params.get("fuel_slot").getAsInt() : -1;

            if (inputSlot != -1) {
                client.interactionManager.clickSlot(handler.syncId, inputSlot, 0, SlotActionType.QUICK_MOVE, client.player);
            }
            if (fuelSlot != -1) {
                client.interactionManager.clickSlot(handler.syncId, fuelSlot, 0, SlotActionType.QUICK_MOVE, client.player);
            }

            JsonObject data = new JsonObject();
            data.addProperty("moved", true);
            
            return CommandResult.success(data);
        });
    }
}
