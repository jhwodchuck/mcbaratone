package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.net.Socket;
import net.minecraft.client.Minecraft;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.MerchantMenu;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.item.trading.MerchantOffers;

/**
 * Handler for the get_screen command - returns current screen info.
 */
public class GetScreenCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "get_screen";
    }

    @Override
    protected CommandResult execute(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        if (client.player == null) {
            return CommandResult.error("Player not available");
        }

        try {
            return client.submit(() -> {
                AbstractContainerMenu handler = client.player.containerMenu;
                if (handler == null) {
                    return CommandResult.error("No screen handler");
                }

                JsonObject data = new JsonObject();
                data.addProperty("sync_id", handler.containerId);
                data.addProperty("type", handler.getClass().getSimpleName());

                JsonArray slots = new JsonArray();
                for (int i = 0; i < handler.slots.size(); i++) {
                    Slot slot = handler.slots.get(i);
                    slots.add(ItemStackJsonSerializer.serialize(slot.getItem(), i));
                }
                data.add("slots", slots);
                data.addProperty("total_slots", handler.slots.size());

                if (handler instanceof MerchantMenu merchantMenu) {
                    MerchantOffers offers = merchantMenu.getOffers();
                    JsonArray merchantOffers = new JsonArray();
                    for (int i = 0; i < offers.size(); i++) {
                        merchantOffers.add(ItemStackJsonSerializer.serializeMerchantOffer(offers.get(i), i));
                    }
                    data.add("merchant_offers", merchantOffers);

                    JsonObject merchant = new JsonObject();
                    merchant.addProperty("trader_level", merchantMenu.getTraderLevel());
                    merchant.addProperty("trader_xp", merchantMenu.getTraderXp());
                    merchant.addProperty("future_trader_xp", merchantMenu.getFutureTraderXp());
                    merchant.addProperty("can_restock", merchantMenu.canRestock());
                    merchant.addProperty("show_progress_bar", merchantMenu.showProgressBar());
                    data.add("merchant", merchant);
                }

                return CommandResult.success(data);
            }).get();
        } catch (Exception e) {
            return CommandResult.error("Failed to read screen: " + e.getMessage());
        }
    }

}
