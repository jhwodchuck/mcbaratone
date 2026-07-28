package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import baritone.api.behavior.IPathingBehavior;
import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class CancelCommandHandlerTest {

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
}
