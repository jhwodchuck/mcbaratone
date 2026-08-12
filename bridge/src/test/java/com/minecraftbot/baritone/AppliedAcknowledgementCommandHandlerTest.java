package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import baritone.api.process.IBuilderProcess;
import baritone.api.process.ICustomGoalProcess;
import com.google.gson.JsonObject;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class AppliedAcknowledgementCommandHandlerTest {

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
    }
}
