package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.atomic.AtomicReference;
import net.minecraft.client.Minecraft;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.mock;

class PlaceRecipeCommandHandlerTest {
    @Test
    void cancelledFutureDoesNotBeginQueuedCraft() {
        Minecraft client = mock(Minecraft.class);
        AtomicReference<Runnable> scheduled = new AtomicReference<>();
        doAnswer(invocation -> {
            scheduled.set(invocation.getArgument(0));
            return null;
        }).when(client).execute(any(Runnable.class));

        JsonObject placement = new JsonObject();
        placement.addProperty("selector", "minecraft:wheat");
        placement.addProperty("grid_slot", 1);
        JsonArray placements = new JsonArray();
        placements.add(placement);
        JsonObject params = new JsonObject();
        params.add("placements", placements);
        params.addProperty("expected_output", "minecraft:bread");

        CompletableFuture<CommandResult> result =
            new PlaceRecipeCommandHandler().execute(
                params, client, mock(IBaritone.class), null);

        assertFalse(result.isDone());
        assertTrue(result.cancel(true));
        scheduled.get().run();
        assertTrue(result.isCancelled());
    }
}
