package com.minecraftbot.baritone;

import com.google.gson.JsonObject;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class ObservedMutationTest {
    private CommandResult accepted() {
        JsonObject data = new JsonObject(); data.addProperty("accepted", true);
        return CommandResult.success(data);
    }
    @Test void delayedEffectRequiresTwoTicksAndDispatchesOnce() throws Exception {
        AtomicInteger dispatched = new AtomicInteger(), samples = new AtomicInteger();
        AtomicLong tick = new AtomicLong();
        var result = ObservedMutation.run(Runnable::run, tick::incrementAndGet,
            () -> { dispatched.incrementAndGet(); return accepted(); },
            data -> samples.incrementAndGet() >= 3, "placed", 1000).get(2, TimeUnit.SECONDS);
        assertTrue(result.isSuccess());
        assertTrue(result.getData().get("postcondition_verified").getAsBoolean());
        assertEquals(1, dispatched.get()); assertTrue(samples.get() >= 4);
    }
    @Test void acceptedWithoutEffectIsUnknownAndNeverReplayed() throws Exception {
        AtomicInteger calls = new AtomicInteger();
        var result = ObservedMutation.run(Runnable::run, () -> 1L,
            () -> { calls.incrementAndGet(); return accepted(); }, d -> false, "moved", 120).get();
        assertFalse(result.isSuccess());
        assertEquals("unknown", result.getData().get("action_status").getAsString());
        assertFalse(result.getData().get("moved").getAsBoolean()); assertEquals(1, calls.get());
    }
    @Test void repeatedSameTickIsNotCompletion() throws Exception {
        var result = ObservedMutation.run(Runnable::run, () -> 7L, this::accepted, d -> true, "placed", 120).get();
        assertFalse(result.isSuccess());
    }
    @Test void cancelledQueuedTaskDoesNotMutate() {
        AtomicReference<Runnable> queued = new AtomicReference<>();
        AtomicInteger calls = new AtomicInteger();
        var pending = ObservedMutation.run(queued::set, () -> 1L,
            () -> { calls.incrementAndGet(); return accepted(); }, d -> true, "placed");
        pending.cancel(false); queued.get().run(); assertEquals(0, calls.get());
    }
    @Test void expiredQueuedTaskDoesNotMutate() throws Exception {
        AtomicReference<Runnable> queued = new AtomicReference<>();
        AtomicInteger calls = new AtomicInteger();
        var pending = ObservedMutation.run(queued::set, () -> 1L,
            () -> { calls.incrementAndGet(); return accepted(); }, d -> true, "placed", 50);
        var result = pending.get(); queued.get().run(); assertEquals(0, calls.get());
        assertEquals("not_dispatched", result.getData().get("action_status").getAsString());
    }
    @Test void rejectionDoesNotProbe() throws Exception {
        var result = ObservedMutation.run(Runnable::run, () -> 1L,
            () -> CommandResult.error("rejected"), d -> { fail("must not probe rejection"); return true; }, "placed").get();
        assertFalse(result.isSuccess());
    }
}
