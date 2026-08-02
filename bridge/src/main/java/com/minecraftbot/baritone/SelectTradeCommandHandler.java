package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;
import java.net.Socket;
import java.time.Duration;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import net.minecraft.client.Minecraft;
import net.minecraft.network.protocol.game.ServerboundSelectTradePacket;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.inventory.MerchantMenu;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.trading.MerchantOffer;
import net.minecraft.world.item.trading.MerchantOffers;

/** Selects a merchant offer and optionally executes a bounded number of trades. */
public class SelectTradeCommandHandler extends AsyncCommandHandler {
    private static final int RESULT_SLOT = 2;
    private static final int MAX_REQUESTED_TRADES = 64;
    private static final long COMMAND_TIMEOUT_NANOS = Duration.ofSeconds(30).toNanos();
    private static final long CLIENT_TASK_TIMEOUT_SECONDS = 3;
    private static final long POLL_INTERVAL_MILLIS = 25;

    @Override
    public String getCommandName() {
        return "select_trade";
    }

    @Override
    public CompletableFuture<CommandResult> execute(
            JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        Integer index = readInteger(params, "index", true);
        Integer count = readInteger(params, "count", false);
        if (index == null) {
            return completedError("index is required and must be an integer");
        }
        if (count == null) {
            return completedError("count must be an integer when provided");
        }
        if (index < 0) {
            return completedError("index must be non-negative");
        }
        if (count < 0 || count > MAX_REQUESTED_TRADES) {
            return completedError("count must be between 0 and " + MAX_REQUESTED_TRADES);
        }
        if (client.player == null || client.gameMode == null || client.getConnection() == null) {
            return completedError("Player, interaction manager, or server connection not available");
        }

        final int selectedIndex = index;
        final int requestedCount = count;
        return CompletableFuture.supplyAsync(() -> runTrade(client, selectedIndex, requestedCount));
    }

    private CommandResult runTrade(Minecraft client, int index, int count) {
        long deadline = System.nanoTime() + COMMAND_TIMEOUT_NANOS;
        try {
            Initialization initialization = client.submit(
                () -> initializeOnClientThread(client, index, count)
            ).get(CLIENT_TASK_TIMEOUT_SECONDS, TimeUnit.SECONDS);
            if (initialization.error() != null) {
                return CommandResult.error(initialization.error());
            }

            TradeContext context = initialization.context();
            JsonObject data = context.data();
            JsonArray acquiredResults = data.getAsJsonArray("acquired_results");
            if (count == 0) {
                Snapshot selected = snapshot(client, context);
                if (!selected.valid()) {
                    return failed(data, 0, selected.uses(), selected.error());
                }
                data.addProperty("uses_after", selected.uses());
                data.addProperty("result_ready", selected.resultReady());
                return CommandResult.success(data);
            }

            for (int tradeNumber = 1; tradeNumber <= count; tradeNumber++) {
                int expectedUsesBefore = context.usesBefore() + tradeNumber - 1;
                Preparation preparation = client.submit(
                    () -> prepareOnClientThread(client, context, expectedUsesBefore)
                ).get(CLIENT_TASK_TIMEOUT_SECONDS, TimeUnit.SECONDS);
                if (!preparation.ready()) {
                    return failed(data, tradeNumber - 1, preparation.uses(), preparation.error());
                }

                Snapshot ready = awaitResultReady(client, context, expectedUsesBefore, deadline);
                if (!ready.valid() || !ready.resultReady()) {
                    String reason = ready.error() != null
                        ? ready.error()
                        : "Timed out waiting for matching merchant result; trade inputs may be unavailable";
                    return failed(data, tradeNumber - 1, ready.uses(), reason);
                }

                client.submit(() -> clickResultOnClientThread(client, context))
                    .get(CLIENT_TASK_TIMEOUT_SECONDS, TimeUnit.SECONDS);

                int requiredInventoryCount = context.inventoryCountBefore()
                    + tradeNumber * context.resultCount();
                Snapshot completed = awaitCompletedTrade(
                    client,
                    context,
                    expectedUsesBefore + 1,
                    requiredInventoryCount,
                    deadline
                );
                if (!completed.valid()
                        || completed.uses() != expectedUsesBefore + 1
                        || completed.inventoryCount() < requiredInventoryCount) {
                    String reason = completed.error() != null
                        ? completed.error()
                        : "Trade was not confirmed by both offer uses and matching inventory result evidence";
                    return failed(data, tradeNumber - 1, completed.uses(), reason);
                }

                JsonObject acquired = ready.resultEvidence().deepCopy();
                acquired.addProperty("trade_number", tradeNumber);
                acquired.addProperty("uses_before", expectedUsesBefore);
                acquired.addProperty("uses_after", completed.uses());
                acquired.addProperty(
                    "inventory_count_before",
                    context.inventoryCountBefore() + (tradeNumber - 1) * context.resultCount()
                );
                acquired.addProperty("inventory_count_after", completed.inventoryCount());
                acquiredResults.add(acquired);
                data.addProperty("completed_count", tradeNumber);
                data.addProperty("uses_after", completed.uses());
            }

            Snapshot finalState = snapshot(client, context);
            if (!finalState.valid()) {
                return failed(data, count, finalState.uses(), finalState.error());
            }
            data.add("selected_offer", finalState.offerEvidence());
            return CommandResult.success(data);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return CommandResult.error("Trade operation interrupted");
        } catch (Exception e) {
            return CommandResult.error("Trade operation failed: " + e.getMessage());
        }
    }

    private Initialization initializeOnClientThread(Minecraft client, int index, int count) {
        if (client.player == null || client.gameMode == null || client.getConnection() == null) {
            return Initialization.error("Player, interaction manager, or server connection not available");
        }
        if (!(client.player.containerMenu instanceof MerchantMenu menu)) {
            return Initialization.error("Current screen is not a MerchantMenu");
        }
        MerchantOffers offers = menu.getOffers();
        if (index >= offers.size()) {
            return Initialization.error(
                "Trade index " + index + " is outside available offers 0.." + (offers.size() - 1));
        }
        MerchantOffer offer = offers.get(index);
        if (offer.isOutOfStock()) {
            return Initialization.error("Trade index " + index + " is out of stock");
        }
        int usesBefore = offer.getUses();
        int remainingUses = offer.getMaxUses() - usesBefore;
        if (count > remainingUses) {
            return Initialization.error(
                "Requested " + count + " trades but only " + remainingUses + " uses remain");
        }

        ItemStack resultTemplate = offer.getResult().copy();
        if (resultTemplate.isEmpty()) {
            return Initialization.error("Selected offer has an empty result");
        }
        int inventoryCountBefore = countMatching(client.player.getInventory(), resultTemplate);
        JsonObject data = baseResult(menu, offer, index, count, usesBefore, inventoryCountBefore);
        selectAndAutofill(client, menu, index);
        data.addProperty("selection_packet_sent", true);
        data.addProperty("inputs_autofilled", true);
        return Initialization.success(new TradeContext(
            menu,
            index,
            usesBefore,
            offer.getBaseCostA().copy(),
            offer.getCostB().copy(),
            resultTemplate,
            resultTemplate.getCount(),
            inventoryCountBefore,
            data
        ));
    }

    private Preparation prepareOnClientThread(
            Minecraft client, TradeContext context, int expectedUses) {
        String stateError = validateCurrentState(client, context);
        if (stateError != null) {
            return new Preparation(false, safeUses(context), stateError);
        }
        MerchantOffer currentOffer = context.menu().getOffers().get(context.index());
        int uses = currentOffer.getUses();
        if (uses != expectedUses) {
            return new Preparation(false, uses, "Selected offer uses changed unexpectedly");
        }
        if (currentOffer.isOutOfStock()) {
            return new Preparation(false, uses, "Selected offer became out of stock");
        }
        selectAndAutofill(client, context.menu(), context.index());
        return new Preparation(true, uses, null);
    }

    private void clickResultOnClientThread(Minecraft client, TradeContext context) {
        String stateError = validateCurrentState(client, context);
        if (stateError != null) {
            throw new IllegalStateException(stateError);
        }
        ItemStack result = context.menu().getSlot(RESULT_SLOT).getItem();
        if (!matchesResult(result, context.resultTemplate())) {
            throw new IllegalStateException("Merchant result slot no longer matches selected offer");
        }
        client.gameMode.handleContainerInput(
            context.menu().containerId,
            RESULT_SLOT,
            0,
            ContainerInput.QUICK_MOVE,
            client.player
        );
    }

    private Snapshot awaitResultReady(
            Minecraft client, TradeContext context, int expectedUses, long deadline) throws Exception {
        Snapshot current = snapshot(client, context);
        while (System.nanoTime() < deadline) {
            if (!current.valid() || current.uses() != expectedUses || current.resultReady()) {
                return current;
            }
            Thread.sleep(POLL_INTERVAL_MILLIS);
            current = snapshot(client, context);
        }
        return current;
    }

    private Snapshot awaitCompletedTrade(
            Minecraft client,
            TradeContext context,
            int expectedUses,
            int requiredInventoryCount,
            long deadline) throws Exception {
        Snapshot current = snapshot(client, context);
        while (System.nanoTime() < deadline) {
            if (!current.valid()
                    || current.uses() > expectedUses
                    || confirmsCompletedTrade(
                        current.uses(),
                        expectedUses,
                        current.inventoryCount(),
                        requiredInventoryCount
                    )) {
                return current;
            }
            Thread.sleep(POLL_INTERVAL_MILLIS);
            current = snapshot(client, context);
        }
        return current;
    }

    private Snapshot snapshot(Minecraft client, TradeContext context) throws Exception {
        return client.submit(() -> snapshotOnClientThread(client, context))
            .get(CLIENT_TASK_TIMEOUT_SECONDS, TimeUnit.SECONDS);
    }

    private Snapshot snapshotOnClientThread(Minecraft client, TradeContext context) {
        String stateError = validateCurrentState(client, context);
        if (stateError != null) {
            return Snapshot.error(safeUses(context), stateError);
        }
        MerchantOffer offer = context.menu().getOffers().get(context.index());
        ItemStack result = context.menu().getSlot(RESULT_SLOT).getItem();
        return new Snapshot(
            true,
            offer.getUses(),
            countMatching(client.player.getInventory(), context.resultTemplate()),
            matchesResult(result, context.resultTemplate()),
            ItemStackJsonSerializer.serialize(result.copy(), RESULT_SLOT),
            ItemStackJsonSerializer.serializeMerchantOffer(offer, context.index()),
            null
        );
    }

    private String validateCurrentState(Minecraft client, TradeContext context) {
        if (client.player == null || client.gameMode == null || client.getConnection() == null) {
            return "Player, interaction manager, or server connection became unavailable";
        }
        if (client.player.containerMenu != context.menu()) {
            return "Merchant screen changed before operation completed";
        }
        MerchantOffers offers = context.menu().getOffers();
        if (context.index() >= offers.size()
                || !matchesSelectedOffer(offers.get(context.index()), context)) {
            return "Selected offer changed before operation completed";
        }
        return null;
    }

    private void selectAndAutofill(Minecraft client, MerchantMenu menu, int index) {
        menu.setSelectionHint(index);
        menu.tryMoveItems(index);
        client.getConnection().send(new ServerboundSelectTradePacket(index));
    }

    private int countMatching(Inventory inventory, ItemStack expected) {
        int count = 0;
        for (int slot = 0; slot < inventory.getContainerSize(); slot++) {
            ItemStack candidate = inventory.getItem(slot);
            if (ItemStack.isSameItemSameComponents(candidate, expected)) {
                count += candidate.getCount();
            }
        }
        return count;
    }

    private boolean matchesResult(ItemStack actual, ItemStack expected) {
        return !actual.isEmpty()
            && ItemStack.isSameItemSameComponents(actual, expected)
            && actual.getCount() >= expected.getCount();
    }

    private boolean matchesSelectedOffer(MerchantOffer actual, TradeContext expected) {
        return sameStackAndCount(actual.getBaseCostA(), expected.baseCostA())
            && sameStackAndCount(actual.getCostB(), expected.costB())
            && sameStackAndCount(actual.getResult(), expected.resultTemplate());
    }

    private boolean sameStackAndCount(ItemStack actual, ItemStack expected) {
        return ItemStack.isSameItemSameComponents(actual, expected)
            && actual.getCount() == expected.getCount();
    }

    private JsonObject baseResult(
            MerchantMenu menu,
            MerchantOffer offer,
            int index,
            int count,
            int usesBefore,
            int inventoryCountBefore) {
        JsonObject data = new JsonObject();
        data.addProperty("sync_id", menu.containerId);
        data.addProperty("selected_index", index);
        data.addProperty("requested_count", count);
        data.addProperty("completed_count", 0);
        data.addProperty("uses_before", usesBefore);
        data.addProperty("uses_after", usesBefore);
        data.addProperty("inventory_count_before", inventoryCountBefore);
        data.addProperty("selection_packet_sent", false);
        data.addProperty("inputs_autofilled", false);
        data.add("selected_offer", ItemStackJsonSerializer.serializeMerchantOffer(offer, index));
        data.add("acquired_results", new JsonArray());
        return data;
    }

    private CommandResult failed(JsonObject data, int completedCount, int usesAfter, String message) {
        data.addProperty("completed_count", completedCount);
        data.addProperty("uses_after", usesAfter);
        data.addProperty("failure_reason", message);
        return new CommandResult(false, data, message);
    }

    private int safeUses(TradeContext context) {
        try {
            MerchantOffers offers = context.menu().getOffers();
            if (context.index() < offers.size()) {
                return offers.get(context.index()).getUses();
            }
            return context.usesBefore();
        } catch (Exception ignored) {
            return context.usesBefore();
        }
    }

    private CompletableFuture<CommandResult> completedError(String message) {
        return CompletableFuture.completedFuture(CommandResult.error(message));
    }

    static Integer readInteger(JsonObject params, String field, boolean required) {
        if (!params.has(field)) {
            return required ? null : 0;
        }
        JsonElement element = params.get(field);
        if (!(element instanceof JsonPrimitive primitive) || !primitive.isNumber()) {
            return null;
        }
        double numeric = primitive.getAsDouble();
        if (!Double.isFinite(numeric) || numeric != Math.rint(numeric)
                || numeric < Integer.MIN_VALUE || numeric > Integer.MAX_VALUE) {
            return null;
        }
        return (int) numeric;
    }

    static boolean confirmsCompletedTrade(
            int actualUses, int expectedUses, int inventoryCount, int requiredInventoryCount) {
        return actualUses == expectedUses && inventoryCount >= requiredInventoryCount;
    }

    private record TradeContext(
        MerchantMenu menu,
        int index,
        int usesBefore,
        ItemStack baseCostA,
        ItemStack costB,
        ItemStack resultTemplate,
        int resultCount,
        int inventoryCountBefore,
        JsonObject data
    ) {
    }

    private record Initialization(TradeContext context, String error) {
        static Initialization success(TradeContext context) {
            return new Initialization(context, null);
        }

        static Initialization error(String error) {
            return new Initialization(null, error);
        }
    }

    private record Preparation(boolean ready, int uses, String error) {
    }

    private record Snapshot(
        boolean valid,
        int uses,
        int inventoryCount,
        boolean resultReady,
        JsonObject resultEvidence,
        JsonObject offerEvidence,
        String error
    ) {
        static Snapshot error(int uses, String error) {
            return new Snapshot(false, uses, 0, false, new JsonObject(), new JsonObject(), error);
        }
    }
}
