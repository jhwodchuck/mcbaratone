package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.item.ItemStack;
import net.minecraft.registry.Registries;
import net.minecraft.screen.CraftingScreenHandler;
import net.minecraft.screen.PlayerScreenHandler;
import net.minecraft.screen.ScreenHandler;
import net.minecraft.screen.slot.SlotActionType;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.Socket;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CompletableFuture;

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

    /** Delay between individual slot clicks for server sync (ms). */
    private static final int CLICK_DELAY_MS = 20;

    /** Delay between craft iterations (ms). */
    private static final int CRAFT_ITER_DELAY_MS = 80;

    /** Maximum polls when waiting for the output slot to populate. */
    private static final int OUTPUT_POLL_MAX = 10;

    /** Delay between output polls (ms). */
    private static final int OUTPUT_POLL_DELAY_MS = 50;

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

    // ---------------------------------------------------------------
    // CommandHandler interface
    // ---------------------------------------------------------------

    @Override
    public String getCommandName() {
        return "place_recipe";
    }

    @Override
    public CompletableFuture<CommandResult> execute(
            JsonObject params, MinecraftClient client,
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

        // ---- Execute on main thread ----

        return executeOnMainThread(client, () -> {
            if (client.player == null || client.interactionManager == null) {
                return CommandResult.error("Player or interaction manager not available");
            }

            ScreenHandler handler = client.player.currentScreenHandler;
            if (handler == null) {
                return CommandResult.error("No screen handler open");
            }

            boolean isTable = handler instanceof CraftingScreenHandler;
            boolean isPlayer = handler instanceof PlayerScreenHandler;
            if (!isTable && !isPlayer) {
                return CommandResult.error(
                        "Expected crafting screen, got " + handler.getClass().getSimpleName());
            }

            int syncId = handler.syncId;
            int gridStart = 1;
            int gridEnd = isTable ? 10 : 5;   // exclusive: slots 1-9 for table, 1-4 for player
            int invStart = isTable ? 10 : 9;   // first inventory slot in the screen
            int invEnd = handler.slots.size();

            // ---- Clear stale grid ----
            for (int slot = gridStart; slot < gridEnd; slot++) {
                ItemStack gridStack = handler.getSlot(slot).getStack();
                if (!gridStack.isEmpty()) {
                    client.interactionManager.clickSlot(
                            syncId, slot, 0, SlotActionType.QUICK_MOVE, client.player);
                    sleep(CLICK_DELAY_MS);
                }
            }

            int craftsCompleted = 0;

            for (int craftIdx = 0; craftIdx < crafts; craftIdx++) {
                // ---- Place each ingredient ----
                boolean placementOk = true;
                for (Placement p : placements) {
                    int sourceSlot = findSource(handler, p.selector, invStart, invEnd);
                    if (sourceSlot == -1) {
                        // Also search grid slots that might have leftovers from a
                        // previous ingredient placement (shouldn't happen after clear,
                        // but defensive)
                        JsonObject errData = new JsonObject();
                        errData.addProperty("error", "missing_ingredient");
                        errData.addProperty("selector", p.selector);
                        errData.addProperty("grid_slot", p.gridSlot);
                        errData.addProperty("crafts_completed", craftsCompleted);
                        return CommandResult.error(
                                "Missing ingredient '" + p.selector + "' for grid slot " + p.gridSlot);
                    }

                    // Pick up source stack
                    client.interactionManager.clickSlot(
                            syncId, sourceSlot, 0, SlotActionType.PICKUP, client.player);
                    sleep(CLICK_DELAY_MS);

                    // Right-click place 1 item into grid slot
                    client.interactionManager.clickSlot(
                            syncId, p.gridSlot, 1, SlotActionType.PICKUP, client.player);
                    sleep(CLICK_DELAY_MS);

                    // Return remainder to source
                    client.interactionManager.clickSlot(
                            syncId, sourceSlot, 0, SlotActionType.PICKUP, client.player);
                    sleep(CLICK_DELAY_MS);
                }

                // ---- Verify output ----
                boolean outputReady = false;
                String actualOutputId = "minecraft:air";
                int actualOutputCount = 0;

                for (int poll = 0; poll < OUTPUT_POLL_MAX; poll++) {
                    ItemStack outputStack = handler.getSlot(0).getStack();
                    if (!outputStack.isEmpty()) {
                        actualOutputId = Registries.ITEM.getId(outputStack.getItem()).toString();
                        actualOutputCount = outputStack.getCount();

                        if (expectedOutput.isEmpty()) {
                            // No expected output specified — any non-empty output is OK
                            outputReady = true;
                            break;
                        }
                        if (actualOutputId.equals(expectedOutput)
                                && actualOutputCount >= expectedCount) {
                            outputReady = true;
                            break;
                        }
                    }
                    sleep(OUTPUT_POLL_DELAY_MS);
                }

                if (!outputReady) {
                    JsonObject errData = new JsonObject();
                    errData.addProperty("error", "no_output");
                    errData.addProperty("expected_output", expectedOutput);
                    errData.addProperty("expected_count", expectedCount);
                    errData.addProperty("actual_output", actualOutputId);
                    errData.addProperty("actual_count", actualOutputCount);
                    errData.addProperty("crafts_completed", craftsCompleted);
                    LOGGER.warn("place_recipe: output verification failed at craft {}/{}: expected {}x{}, got {}x{}",
                            craftIdx + 1, crafts, expectedOutput, expectedCount,
                            actualOutputId, actualOutputCount);
                    return CommandResult.success(errData);
                }

                // ---- Collect output ----
                client.interactionManager.clickSlot(
                        syncId, 0, 0, SlotActionType.QUICK_MOVE, client.player);
                sleep(CRAFT_ITER_DELAY_MS);
                craftsCompleted++;

                // Clear grid between craft iterations (residual phantom items)
                if (craftIdx < crafts - 1) {
                    for (int slot = gridStart; slot < gridEnd; slot++) {
                        ItemStack gridStack = handler.getSlot(slot).getStack();
                        if (!gridStack.isEmpty()) {
                            client.interactionManager.clickSlot(
                                    syncId, slot, 0, SlotActionType.QUICK_MOVE, client.player);
                            sleep(CLICK_DELAY_MS);
                        }
                    }
                }
            }

            // ---- Build success response ----
            JsonObject data = new JsonObject();
            data.addProperty("crafted", true);
            data.addProperty("crafts_completed", craftsCompleted);
            data.addProperty("item", expectedOutput.isEmpty() ? "unknown" : expectedOutput);
            data.addProperty("count", craftsCompleted * expectedCount);
            return CommandResult.success(data);
        });
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
    private static int findSource(ScreenHandler handler, String selector,
                                   int startSlot, int endSlot) {
        for (int i = startSlot; i < endSlot; i++) {
            ItemStack stack = handler.getSlot(i).getStack();
            if (stack.isEmpty()) continue;

            String itemId = Registries.ITEM.getId(stack.getItem()).toString();
            if (matchesSelector(selector, itemId)) {
                return i;
            }
        }
        return -1;
    }

    // ---------------------------------------------------------------
    // Utility
    // ---------------------------------------------------------------

    private static void sleep(int ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
