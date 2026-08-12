package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.Socket;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import net.minecraft.client.Minecraft;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.inventory.CraftingMenu;
import net.minecraft.world.inventory.InventoryMenu;
import net.minecraft.world.item.ItemStack;

/**
 * Bridge primitive that executes a full recipe click choreography in-process.
 *
 * Accepts [(ingredient_selector, grid_slot), ...] plus expected output from
 * the Python side and performs all slot clicks with per-click in-process screen
 * re-reads.  Collapses ~30 TCP round-trips per craft into a single command.
 *
 * Recipe knowledge stays in Python; this handler is a dumb click executor
 * with verification.
 *
 * Parameters:
 *   placements: [{selector: "minecraft:iron_ingot", grid_slot: 1}, ...]
 *   expected_output: "minecraft:shield"
 *   expected_count: 1  (optional, default 1)
 *   crafts: 1           (optional, default 1)
 */
public class PlaceRecipeCommandHandler extends AsyncCommandHandler {

    private static final Logger LOGGER = LoggerFactory.getLogger(PlaceRecipeCommandHandler.class);

    /** Maximum crafts per single command to avoid runaway timeouts. */
    private static final int MAX_CRAFTS = 64;

    /** Delay between craft iterations (ms). */
    private static final int CRAFT_ITER_DELAY_MS = 80;

    /** Maximum polls when waiting for the output slot to populate. */
    private static final int OUTPUT_POLL_MAX = 40;

    /**
     * Minimum output polls before collecting a locally visible result.
     * Multiplayer clients can predict the recipe output before the server has
     * acknowledged the ingredient clicks.  Shift-clicking that prediction too
     * early is rejected by the server and the ingredients later roll back.
     */
    private static final int OUTPUT_SETTLE_POLL_MIN = 5;

    /** Delay between output polls (ms). */
    private static final int OUTPUT_POLL_DELAY_MS = 50;

    /** Maximum polls while verifying QUICK_MOVE actually reached inventory. */
    private static final int COLLECTION_POLL_MAX = 40;

    // ---------------------------------------------------------------
    // Parsed placement instruction
    // ---------------------------------------------------------------

    private static class Placement {
        final String selector;
        final int gridSlot;

        Placement(String selector, int gridSlot) {
            this.selector = selector;
            this.gridSlot = gridSlot;
        }
    }

    /** Mutable state for one non-blocking recipe command. */
    private static class RecipeExecution {
        final Minecraft client;
        final AbstractContainerMenu handler;
        final int syncId;
        final int gridStart;
        final int gridEnd;
        final int invStart;
        final int invEnd;
        final List<Placement> placements;
        final String expectedOutput;
        final int expectedCount;
        final int crafts;
        final CompletableFuture<CommandResult> future;
        int craftsCompleted;
        int outputPolls;
        int collectionPolls;
        int inventoryBefore;

        RecipeExecution(Minecraft client, AbstractContainerMenu handler,
                        int gridEnd, int invStart, List<Placement> placements,
                        String expectedOutput, int expectedCount, int crafts,
                        CompletableFuture<CommandResult> future) {
            this.client = client;
            this.handler = handler;
            this.syncId = handler.containerId;
            this.gridStart = 1;
            this.gridEnd = gridEnd;
            this.invStart = invStart;
            this.invEnd = handler.slots.size();
            this.placements = placements;
            this.expectedOutput = expectedOutput;
            this.expectedCount = expectedCount;
            this.crafts = crafts;
            this.future = future;
        }
    }

    // ---------------------------------------------------------------
    // CommandHandler interface
    // ---------------------------------------------------------------

    @Override
    public String getCommandName() {
        return "place_recipe";
    }

    @Override
    public CompletableFuture<CommandResult> execute(
            JsonObject params, Minecraft client,
            IBaritone baritone, Socket clientSocket) {

        // ---- Parse parameters ----

        if (!params.has("placements") || !params.get("placements").isJsonArray()) {
            return CompletableFuture.completedFuture(
                    CommandResult.error("Missing or invalid 'placements' array"));
        }

        JsonArray placementsJson = params.getAsJsonArray("placements");
        if (placementsJson.size() == 0) {
            return CompletableFuture.completedFuture(
                    CommandResult.error("'placements' array is empty"));
        }

        List<Placement> placements = new ArrayList<>();
        for (JsonElement elem : placementsJson) {
            if (!elem.isJsonObject()) {
                return CompletableFuture.completedFuture(
                        CommandResult.error("Each placement must be a JSON object"));
            }
            JsonObject p = elem.getAsJsonObject();
            if (!p.has("selector") || !p.has("grid_slot")) {
                return CompletableFuture.completedFuture(
                        CommandResult.error("Each placement requires 'selector' and 'grid_slot'"));
            }
            placements.add(new Placement(
                    p.get("selector").getAsString(),
                    p.get("grid_slot").getAsInt()));
        }

        String expectedOutput = params.has("expected_output")
                ? params.get("expected_output").getAsString() : "";
        int expectedCount = params.has("expected_count")
                ? params.get("expected_count").getAsInt() : 1;
        int crafts = params.has("crafts")
                ? params.get("crafts").getAsInt() : 1;

        if (crafts <= 0 || crafts > MAX_CRAFTS) {
            return CompletableFuture.completedFuture(
                    CommandResult.error("'crafts' must be between 1 and " + MAX_CRAFTS));
        }

        // Slot updates from a multiplayer server arrive on the Minecraft main
        // thread.  Never sleep or poll while holding that thread: doing so
        // prevents the recipe output packet from being applied and makes every
        // otherwise-valid craft appear to produce air.
        CompletableFuture<CommandResult> result = new CompletableFuture<>();
        client.execute(() -> {
            if (result.isDone()) {
                return;
            }
            try {
                if (client.player == null || client.gameMode == null) {
                    result.complete(CommandResult.error(
                            "Player or interaction manager not available"));
                    return;
                }

                AbstractContainerMenu handler = client.player.containerMenu;
                if (handler == null) {
                    result.complete(CommandResult.error("No screen handler open"));
                    return;
                }

                boolean isTable = handler instanceof CraftingMenu;
                boolean isPlayer = handler instanceof InventoryMenu;
                if (!isTable && !isPlayer) {
                    result.complete(CommandResult.error(
                            "Expected crafting screen, got "
                                    + handler.getClass().getSimpleName()));
                    return;
                }

                RecipeExecution execution = new RecipeExecution(
                        client,
                        handler,
                        isTable ? 10 : 5,
                        isTable ? 10 : 9,
                        placements,
                        expectedOutput,
                        expectedCount,
                        crafts,
                        result);
                execution.inventoryBefore = countInventoryItem(
                        client, expectedOutput);
                startCraft(execution);
            } catch (Exception e) {
                result.completeExceptionally(e);
            }
        });
        return result;
    }

    /** Clear the grid, place one recipe, then yield for server output sync. */
    private void startCraft(RecipeExecution execution) {
        if (execution.future.isDone()) return;
        if (!screenStillOpen(execution)) {
            execution.future.complete(CommandResult.error(
                    "Crafting screen changed while placing recipe"));
            return;
        }

        for (int slot = execution.gridStart; slot < execution.gridEnd; slot++) {
            ItemStack gridStack = execution.handler.getSlot(slot).getItem();
            if (!gridStack.isEmpty()) {
                execution.client.gameMode.handleContainerInput(
                        execution.syncId, slot, 0, ContainerInput.QUICK_MOVE,
                        execution.client.player);
            }
        }

        for (Placement placement : execution.placements) {
            int sourceSlot = findSource(
                    execution.handler,
                    placement.selector,
                    execution.invStart,
                    execution.invEnd);
            if (sourceSlot == -1) {
                execution.future.complete(CommandResult.error(
                        "Missing ingredient '" + placement.selector
                                + "' for grid slot " + placement.gridSlot));
                return;
            }

            execution.client.gameMode.handleContainerInput(
                    execution.syncId, sourceSlot, 0, ContainerInput.PICKUP,
                    execution.client.player);
            execution.client.gameMode.handleContainerInput(
                    execution.syncId, placement.gridSlot, 1, ContainerInput.PICKUP,
                    execution.client.player);
            execution.client.gameMode.handleContainerInput(
                    execution.syncId, sourceSlot, 0, ContainerInput.PICKUP,
                    execution.client.player);
        }

        execution.outputPolls = 0;
        scheduleOnMainThread(execution, () -> pollOutput(execution),
                OUTPUT_POLL_DELAY_MS);
    }

    /** Poll once, yielding the main thread again when the server has not replied. */
    private void pollOutput(RecipeExecution execution) {
        if (execution.future.isDone()) return;
        if (!screenStillOpen(execution)) {
            execution.future.complete(CommandResult.error(
                    "Crafting screen changed while waiting for recipe output"));
            return;
        }

        ItemStack outputStack = execution.handler.getSlot(0).getItem();
        String actualOutputId = outputStack.isEmpty()
                ? "minecraft:air"
                : BuiltInRegistries.ITEM.getKey(outputStack.getItem()).toString();
        int actualOutputCount = outputStack.isEmpty() ? 0 : outputStack.getCount();
        boolean outputReady = !outputStack.isEmpty()
                && (execution.expectedOutput.isEmpty()
                    || (actualOutputId.equals(execution.expectedOutput)
                        && actualOutputCount >= execution.expectedCount));

        if (outputReady && execution.outputPolls >= OUTPUT_SETTLE_POLL_MIN) {
            execution.client.gameMode.handleContainerInput(
                    execution.syncId, 0, 0, ContainerInput.QUICK_MOVE,
                    execution.client.player);
            execution.collectionPolls = 0;
            scheduleOnMainThread(execution, () -> verifyCollection(execution),
                    CRAFT_ITER_DELAY_MS);
            return;
        }

        execution.outputPolls++;
        if (execution.outputPolls < OUTPUT_POLL_MAX) {
            scheduleOnMainThread(execution, () -> pollOutput(execution),
                    OUTPUT_POLL_DELAY_MS);
            return;
        }

        JsonObject errData = new JsonObject();
        errData.addProperty("error", "no_output");
        errData.addProperty("expected_output", execution.expectedOutput);
        errData.addProperty("expected_count", execution.expectedCount);
        errData.addProperty("actual_output", actualOutputId);
        errData.addProperty("actual_count", actualOutputCount);
        errData.addProperty("crafts_completed", execution.craftsCompleted);
        LOGGER.warn(
                "place_recipe: output verification failed at craft {}/{}: expected {}x{}, got {}x{}",
                execution.craftsCompleted + 1,
                execution.crafts,
                execution.expectedOutput,
                execution.expectedCount,
                actualOutputId,
                actualOutputCount);
        execution.future.complete(CommandResult.success(errData));
    }

    /** Verify the crafted result left slot zero and entered PlayerInventory. */
    private void verifyCollection(RecipeExecution execution) {
        if (execution.future.isDone()) return;
        if (!screenStillOpen(execution)) {
            execution.future.complete(CommandResult.error(
                    "Crafting screen changed while collecting recipe output"));
            return;
        }

        ItemStack outputStack = execution.handler.getSlot(0).getItem();
        int expectedInventoryCount = execution.inventoryBefore
                + (execution.craftsCompleted + 1) * execution.expectedCount;
        int actualInventoryCount = countInventoryItem(
                execution.client, execution.expectedOutput);
        boolean outputCleared = outputStack.isEmpty();
        boolean inventoryAdvanced = execution.expectedOutput.isEmpty()
                ? outputCleared
                : actualInventoryCount >= expectedInventoryCount;

        if (outputCleared && inventoryAdvanced) {
            execution.craftsCompleted++;
            if (execution.craftsCompleted < execution.crafts) {
                scheduleOnMainThread(execution, () -> startCraft(execution),
                        CRAFT_ITER_DELAY_MS);
            } else {
                completeSuccess(execution);
            }
            return;
        }

        execution.collectionPolls++;
        if (execution.collectionPolls < COLLECTION_POLL_MAX) {
            scheduleOnMainThread(execution, () -> verifyCollection(execution),
                    OUTPUT_POLL_DELAY_MS);
            return;
        }

        JsonObject data = new JsonObject();
        data.addProperty("crafted", false);
        data.addProperty("error", "output_not_collected");
        data.addProperty("expected_output", execution.expectedOutput);
        data.addProperty("expected_inventory_count", expectedInventoryCount);
        data.addProperty("actual_inventory_count", actualInventoryCount);
        data.addProperty("output_slot_empty", outputCleared);
        data.addProperty("crafts_completed", execution.craftsCompleted);
        LOGGER.warn(
                "place_recipe: output could not enter inventory: expected {} count {}, got {}; output empty={}",
                execution.expectedOutput,
                expectedInventoryCount,
                actualInventoryCount,
                outputCleared);
        execution.future.complete(CommandResult.success(data));
    }

    private void completeSuccess(RecipeExecution execution) {
        JsonObject data = new JsonObject();
        data.addProperty("crafted", true);
        data.addProperty("crafts_completed", execution.craftsCompleted);
        data.addProperty(
                "item",
                execution.expectedOutput.isEmpty()
                        ? "unknown" : execution.expectedOutput);
        data.addProperty(
                "count",
                execution.craftsCompleted * execution.expectedCount);
        execution.future.complete(CommandResult.success(data));
    }

    private int countInventoryItem(Minecraft client, String itemId) {
        if (client.player == null || itemId == null || itemId.isEmpty()) {
            return 0;
        }
        int count = 0;
        for (int i = 0; i < client.player.getInventory().getContainerSize(); i++) {
            ItemStack stack = client.player.getInventory().getItem(i);
            if (stack.isEmpty()) continue;
            String stackId = BuiltInRegistries.ITEM.getKey(stack.getItem()).toString();
            if (itemId.equals(stackId)) {
                count += stack.getCount();
            }
        }
        return count;
    }

    private boolean screenStillOpen(RecipeExecution execution) {
        return execution.client.player != null
                && execution.client.gameMode != null
                && execution.client.player.containerMenu == execution.handler
                && execution.handler.containerId == execution.syncId;
    }

    /** Run a later step on the Minecraft main thread without blocking it. */
    private void scheduleOnMainThread(
            RecipeExecution execution, Runnable task, int delayMs) {
        CompletableFuture.delayedExecutor(delayMs, TimeUnit.MILLISECONDS)
                .execute(() -> execution.client.execute(() -> {
                    if (execution.future.isDone()) return;
                    try {
                        task.run();
                    } catch (Exception e) {
                        execution.future.completeExceptionally(e);
                    }
                }));
    }

    // ---------------------------------------------------------------
    // Selector matching
    // ---------------------------------------------------------------

    /**
     * Match an item ID against a selector string.
     * Supports tag-like selectors (#planks, #logs) and exact IDs.
     */
    private static boolean matchesSelector(String selector, String itemId) {
        if (selector.startsWith("#")) {
            switch (selector) {
                case "#planks":
                    return itemId.endsWith("_planks");
                case "#logs":
                    return itemId.endsWith("_log") || itemId.endsWith("_wood");
                case "#wool":
                    return itemId.endsWith("_wool");
                case "#stone":
                    return itemId.equals("minecraft:cobblestone")
                            || itemId.equals("minecraft:cobbled_deepslate")
                            || itemId.equals("minecraft:blackstone");
                default:
                    // Unknown tag — fall through to exact match
                    return itemId.equals(selector);
            }
        }
        return itemId.equals(selector);
    }

    // ---------------------------------------------------------------
    // Source slot finder
    // ---------------------------------------------------------------

    /**
     * Find the first inventory slot in [startSlot, endSlot) matching the selector.
     * Returns -1 if not found.
     */
    private static int findSource(AbstractContainerMenu handler, String selector,
                                   int startSlot, int endSlot) {
        for (int i = startSlot; i < endSlot; i++) {
            ItemStack stack = handler.getSlot(i).getItem();
            if (stack.isEmpty()) continue;

            String itemId = BuiltInRegistries.ITEM.getKey(stack.getItem()).toString();
            if (matchesSelector(selector, itemId)) {
                return i;
            }
        }
        return -1;
    }

}
