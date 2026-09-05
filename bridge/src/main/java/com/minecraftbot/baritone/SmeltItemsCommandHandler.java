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
        final AbstractContainerMenu[] menu = {null};
        final net.minecraft.world.item.ItemStack[] sources = new net.minecraft.world.item.ItemStack[2];
        final int[] slots = {-1, -1};
        final int[] destinationCounts = new int[2];
        return ObservedMutation.run(client::execute, () -> client.level.getGameTime(), () -> {
            if (client.player == null || client.gameMode == null) {
                return CommandResult.error("Player not available");
            }

            AbstractContainerMenu handler = client.player.containerMenu;
            if (!(handler instanceof AbstractFurnaceMenu)) {
                return CommandResult.error("Not in a furnace screen");
            }
            if (!handler.getCarried().isEmpty()) return CommandResult.error("Cursor must be empty before furnace transfer");

            // Slot 0: Input, Slot 1: Fuel, Slot 2: Output
            int inputSlot = params.has("input_slot") ? params.get("input_slot").getAsInt() : -1;
            int fuelSlot = params.has("fuel_slot") ? params.get("fuel_slot").getAsInt() : -1;

            if (inputSlot == -1 && fuelSlot == -1) return CommandResult.error("No source slots supplied");
            if (inputSlot != -1 && inputSlot == fuelSlot) return CommandResult.error("Source slots must differ");
            if (params.has("sync_id") && params.get("sync_id").getAsInt() != handler.containerId)
                return CommandResult.error("Stale furnace sync_id");
            menu[0] = handler;
            slots[0] = inputSlot; slots[1] = fuelSlot;
            for (int i = 0; i < 2; i++) {
                int source = slots[i];
                if (source == -1) continue;
                if (source < 3 || source >= handler.slots.size()) return CommandResult.error("Invalid furnace player source slot");
                sources[i] = handler.getSlot(source).getItem().copy();
                if (sources[i].isEmpty() || !handler.getSlot(i).mayPlace(sources[i]))
                    return CommandResult.error("Source item is not valid for furnace destination " + i);
                var destination = handler.getSlot(i).getItem();
                if (!destination.isEmpty() && !net.minecraft.world.item.ItemStack.isSameItemSameComponents(destination, sources[i]))
                    return CommandResult.error("Furnace destination contains another item");
                destinationCounts[i] = destination.getCount();
                if (destinationCounts[i] >= handler.getSlot(i).getMaxStackSize(sources[i]))
                    return CommandResult.error("Furnace destination is full");
            }
            JsonObject before = ContainerEvidence.snapshot(handler);
            for (int i = 0; i < 2; i++) if (slots[i] != -1) {
                // QUICK_MOVE routes smeltable fuels (logs) to input before fuel.
                // Explicit destinations avoid reporting a misplaced stack as fuel.
                client.gameMode.handleContainerInput(handler.containerId, slots[i], 0, ContainerInput.PICKUP, client.player);
                client.gameMode.handleContainerInput(handler.containerId, i, 0, ContainerInput.PICKUP, client.player);
                if (!handler.getCarried().isEmpty())
                    client.gameMode.handleContainerInput(handler.containerId, slots[i], 0, ContainerInput.PICKUP, client.player);
            }

            JsonObject data = new JsonObject();
            data.addProperty("moved", false);
            data.add("before", before);
            
            return CommandResult.success(data);
        }, data -> {
            if (client.player.containerMenu != menu[0]) throw new IllegalStateException("Furnace menu changed");
            data.add("after", ContainerEvidence.snapshot(menu[0]));
            boolean complete = true;
            for (int i = 0; i < 2; i++) {
                if (slots[i] == -1) continue;
                var source = menu[0].getSlot(slots[i]).getItem();
                var target = menu[0].getSlot(i).getItem();
                boolean landed = net.minecraft.world.item.ItemStack.isSameItemSameComponents(target, sources[i])
                    && target.getCount() > destinationCounts[i];
                boolean burning = i == 1 && ((AbstractFurnaceMenu) menu[0]).isLit();
                boolean moved = source.getCount() < sources[i].getCount() && (landed || burning)
                    && menu[0].getCarried().isEmpty();
                data.addProperty(i == 0 ? "input_moved" : "fuel_moved", moved);
                complete &= moved;
            }
            return complete;
        }, "moved");
    }
}
