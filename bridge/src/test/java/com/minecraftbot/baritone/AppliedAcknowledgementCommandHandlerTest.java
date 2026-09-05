package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import baritone.api.process.IBuilderProcess;
import baritone.api.process.ICustomGoalProcess;
import com.google.gson.JsonObject;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class AppliedAcknowledgementCommandHandlerTest {

    // goal_id is issued by the process-wide NavigationLifecycleTracker
    // singleton; isolate it from other test classes sharing this JVM worker.
    @BeforeEach
    void resetTrackerBefore() {
        NavigationLifecycleTracker.getInstance().resetForTests();
    }

    @AfterEach
    void resetTrackerAfter() {
        NavigationLifecycleTracker.getInstance().resetForTests();
    }

    @Test
    void gotoAcknowledgesOnlyAfterGoalIsAppliedOnMainThread() throws Exception {
        Minecraft client = mock(Minecraft.class);
        IBaritone baritone = mock(IBaritone.class);
        ICustomGoalProcess goalProcess = mock(ICustomGoalProcess.class);
        when(baritone.getCustomGoalProcess()).thenReturn(goalProcess);
        JsonObject params = coordinates(10, 70, -20);

        CompletableFuture<CommandResult> result =
            new GotoCommandHandler().execute(params, client, baritone, null);

        verify(goalProcess, never()).setGoalAndPath(any());
        assertFalse(result.isDone());
        runScheduledTask(client);

        assertApplied(result.get());
        verify(goalProcess).setGoalAndPath(any());
    }

    @Test
    void mineAcknowledgesOnlyAfterClearAreaIsAppliedOnMainThread() throws Exception {
        Minecraft client = mock(Minecraft.class);
        IBaritone baritone = mock(IBaritone.class);
        IBuilderProcess builderProcess = mock(IBuilderProcess.class);
        when(baritone.getBuilderProcess()).thenReturn(builderProcess);
        JsonObject params = coordinates(1, 64, 2);

        CompletableFuture<CommandResult> result =
            new MineCommandHandler().execute(params, client, baritone, null);

        verify(builderProcess, never()).clearArea(any(), any());
        assertFalse(result.isDone());
        runScheduledTask(client);

        assertApplied(result.get());
        verify(builderProcess).clearArea(any(), any());
    }

    @Test
    void breakBlockAcknowledgesOnlyAfterClearAreaIsAppliedOnMainThread() throws Exception {
        Minecraft client = mock(Minecraft.class);
        IBaritone baritone = mock(IBaritone.class);
        IBuilderProcess builderProcess = mock(IBuilderProcess.class);
        when(baritone.getBuilderProcess()).thenReturn(builderProcess);
        JsonObject params = coordinates(-3, 45, 8);

        CompletableFuture<CommandResult> result =
            new BreakBlockCommandHandler().execute(params, client, baritone, null);

        verify(builderProcess, never()).clearArea(any(), any());
        assertFalse(result.isDone());
        runScheduledTask(client);

        assertApplied(result.get());
        verify(builderProcess).clearArea(any(), any());
    }

    @Test
    void cancelledFutureDoesNotApplyQueuedMutation() {
        Minecraft client = mock(Minecraft.class);
        IBaritone baritone = mock(IBaritone.class);
        ICustomGoalProcess goalProcess = mock(ICustomGoalProcess.class);
        when(baritone.getCustomGoalProcess()).thenReturn(goalProcess);

        CompletableFuture<CommandResult> result =
            new GotoCommandHandler().execute(
                coordinates(10, 70, -20), client, baritone, null);

        assertTrue(result.cancel(true));
        runScheduledTask(client);
        verify(goalProcess, never()).setGoalAndPath(any());
    }

    private static JsonObject coordinates(int x, int y, int z) {
        JsonObject params = new JsonObject();
        params.addProperty("x", x);
        params.addProperty("y", y);
        params.addProperty("z", z);
        return params;
    }

    private static void runScheduledTask(Minecraft client) {
        ArgumentCaptor<Runnable> task = ArgumentCaptor.forClass(Runnable.class);
        verify(client).execute(task.capture());
        task.getValue().run();
    }

    private static void assertApplied(CommandResult result) {
        assertTrue(result.isSuccess());
        assertTrue(result.getData().get("started").getAsBoolean());
        assertTrue(result.getData().get("accepted").getAsBoolean());
        assertTrue(result.getData().get("applied").getAsBoolean());
        // Every started=true used to be indistinguishable from a wedged bot
        // over the wire; goal_id is what lets a later get_state poll ask
        // "what happened to THIS dispatch" instead of "what happened lately".
        assertTrue(result.getData().get("goal_id").getAsLong() > 0);
    }

    @Test
    void eachDispatchGetsItsOwnIncrementingGoalId() throws Exception {
        // Two separate mock pairs: each stands in for a distinct wire
        // request, the way two real dispatches never share one client.
        Minecraft clientA = mock(Minecraft.class);
        IBaritone baritoneA = mock(IBaritone.class);
        ICustomGoalProcess goalProcessA = mock(ICustomGoalProcess.class);
        when(baritoneA.getCustomGoalProcess()).thenReturn(goalProcessA);
        CompletableFuture<CommandResult> first =
            new GotoCommandHandler().execute(coordinates(1, 64, 1), clientA, baritoneA, null);
        runScheduledTask(clientA);
        long firstId = first.get().getData().get("goal_id").getAsLong();

        Minecraft clientB = mock(Minecraft.class);
        IBaritone baritoneB = mock(IBaritone.class);
        ICustomGoalProcess goalProcessB = mock(ICustomGoalProcess.class);
        when(baritoneB.getCustomGoalProcess()).thenReturn(goalProcessB);
        CompletableFuture<CommandResult> second =
            new GotoCommandHandler().execute(coordinates(2, 64, 2), clientB, baritoneB, null);
        runScheduledTask(clientB);
        long secondId = second.get().getData().get("goal_id").getAsLong();

        assertNotEquals(firstId, secondId);
        assertEquals("superseded",
            NavigationLifecycleTracker.getInstance().snapshot()
                .getAsJsonObject("last_resolution").get("status").getAsString());
    }
}
