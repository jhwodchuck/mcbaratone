package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import net.minecraft.client.Minecraft;
import net.minecraft.world.entity.player.Inventory;

public class GetInventoryCommandHandler implements CommandHandler {

    @Override
    public CompletableFuture<CommandResult> handle(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        CompletableFuture<CommandResult> result = new CompletableFuture<>();

        if (client == null) {
            result.complete(CommandResult.error("Client not available"));
            return result;
        }

        try {
            client.execute(() -> {
                try {
                    if (client.player == null) {
                        result.complete(CommandResult.error("Player not available"));
                        return;
                    }

                    Inventory inv = client.player.getInventory();

                    JsonArray mainInventory = new JsonArray();
                    for (int i = 0; i < 36; i++) {
                        mainInventory.add(ItemStackJsonSerializer.serialize(inv.getItem(i), i));
                    }

                    JsonArray armorInventory = new JsonArray();
                    for (int i = 0; i < 4; i++) {
                        armorInventory.add(ItemStackJsonSerializer.serialize(inv.getItem(36 + i), 36 + i));
                    }

                    JsonArray offhandInventory = new JsonArray();
                    offhandInventory.add(ItemStackJsonSerializer.serialize(inv.getItem(40), 40));

                    JsonObject data = new JsonObject();
                    data.add("inventory", mainInventory);
                    data.add("armor", armorInventory);
                    data.add("offhand", offhandInventory);
                    data.addProperty("selected_slot", inv.selected);
                    data.addProperty("snapshot_valid", true);
                    data.addProperty("observed_tick", client.level.getGameTime());

                    result.complete(CommandResult.success(data));
                } catch (Exception e) {
                    result.complete(CommandResult.error("GetInventory failed: " + e.getMessage()));
                }
            });
        } catch (Exception e) {
            result.complete(CommandResult.error("GetInventory failed: " + e.getMessage()));
        }

        return result.completeOnTimeout(
            CommandResult.error("Timeout waiting for inventory data"),
            5,
            TimeUnit.SECONDS
        );
    }

    @Override
    public String getCommandName() { return "get_inventory"; }
}
