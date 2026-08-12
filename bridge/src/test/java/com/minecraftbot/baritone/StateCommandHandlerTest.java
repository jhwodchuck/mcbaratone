package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.util.concurrent.atomic.AtomicReference;
import net.minecraft.client.Minecraft;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.mock;

class StateCommandHandlerTest {
    @Test
    void stateSnapshotCompletesOnlyFromClientThreadRunnable() {
        Minecraft client = mock(Minecraft.class);
        IBaritone baritone = mock(IBaritone.class);
        AtomicReference<Runnable> scheduled = new AtomicReference<>();
        doAnswer(invocation -> {
            scheduled.set(invocation.getArgument(0));
            return null;
        }).when(client).execute(any(Runnable.class));

        var future = new StateCommandHandler().execute(
            new JsonObject(), client, baritone, null);

        assertFalse(future.isDone());
        scheduled.get().run();
        assertTrue(future.isDone());
        assertFalse(future.join().isSuccess());
    }
}
