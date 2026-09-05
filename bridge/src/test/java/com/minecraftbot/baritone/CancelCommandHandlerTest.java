package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import baritone.api.behavior.IPathingBehavior;
import baritone.api.event.events.PathEvent;
import com.google.gson.JsonObject;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class CancelCommandHandlerTest {

    @BeforeEach
    void resetTrackerBefore() {
        NavigationLifecycleTracker.getInstance().resetForTests();
    }

    @AfterEach
    void resetTrackerAfter() {
        NavigationLifecycleTracker.getInstance().resetForTests();
    }

    @Test
    void schedulesCancellationOnMinecraftMainThread() throws Exception {
        Minecraft client = mock(Minecraft.class);
        IBaritone baritone = mock(IBaritone.class);
        IPathingBehavior pathing = mock(IPathingBehavior.class);
        when(baritone.getPathingBehavior()).thenReturn(pathing);
        CancelCommandHandler handler = new CancelCommandHandler();

        CompletableFuture<CommandResult> result = handler.handle(
            new JsonObject(), client, baritone, null
        );

        verify(pathing, never()).cancelEverything();
        assertFalse(result.isDone());
        ArgumentCaptor<Runnable> task = ArgumentCaptor.forClass(Runnable.class);
        verify(client).execute(task.capture());
        task.getValue().run();

        assertTrue(result.get().isSuccess());
        verify(pathing).cancelEverything();
    }

    @Test
    void cancelFlagsTheTrackerBeforeBaritoneEverCancels() throws Exception {
        // Baritone cannot say whether a CANCELED event was operator-requested
        // or its own abandonment; the cancel handler has to record intent
        // before asking Baritone to stop, or the two are indistinguishable
        // once the real PathEvent arrives.
        Minecraft client = mock(Minecraft.class);
        IBaritone baritone = mock(IBaritone.class);
        IPathingBehavior pathing = mock(IPathingBehavior.class);
        when(baritone.getPathingBehavior()).thenReturn(pathing);
        NavigationLifecycleTracker.getInstance().beginGeneration(baritone);
        CancelCommandHandler handler = new CancelCommandHandler();

        CompletableFuture<CommandResult> result =
            handler.handle(new JsonObject(), client, baritone, null);
        ArgumentCaptor<Runnable> task = ArgumentCaptor.forClass(Runnable.class);
        verify(client).execute(task.capture());
        task.getValue().run();
        result.get();

        NavigationLifecycleTracker.getInstance().onPathEvent(PathEvent.CANCELED);
        assertEquals("canceled",
            NavigationLifecycleTracker.getInstance().snapshot().get("status").getAsString());
    }
}
