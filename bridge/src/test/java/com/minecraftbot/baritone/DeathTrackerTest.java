package com.minecraftbot.baritone;

import com.google.gson.JsonObject;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class DeathTrackerTest {

    @Test
    void handlerReflectsDeathsRecordedByItsSharedTracker() throws Exception {
        DeathTracker tracker = new DeathTracker();
        GetDeathLocationCommandHandler handler =
            new GetDeathLocationCommandHandler(tracker);

        CommandResult empty = handler
            .handle(new JsonObject(), null, null, null)
            .get(1, TimeUnit.SECONDS);
        assertTrue(empty.isSuccess());
        assertFalse(empty.getData().get("has_death_location").getAsBoolean());

        DeathTracker.DeathSnapshot recorded = tracker.record(
            12.25,
            63.0,
            -44.75,
            "minecraft:the_nether",
            9876L,
            1_725_000_000_123L
        );
        CommandResult observed = handler
            .handle(new JsonObject(), null, null, null)
            .get(1, TimeUnit.SECONDS);
        JsonObject data = observed.getData();

        assertTrue(data.get("has_death_location").getAsBoolean());
        assertEquals(recorded.deathId(), data.get("death_id").getAsLong());
        assertEquals(12.25, data.get("x").getAsDouble());
        assertEquals(63.0, data.get("y").getAsDouble());
        assertEquals(-44.75, data.get("z").getAsDouble());
        assertEquals("minecraft:the_nether", data.get("dimension").getAsString());
        assertEquals(9876L, data.get("game_tick").getAsLong());
        assertEquals(1_725_000_000_123L, data.get("timestamp").getAsLong());
    }

    @Test
    void deathIdsIncreaseMonotonically() {
        DeathTracker tracker = new DeathTracker();

        DeathTracker.DeathSnapshot first = tracker.record(
            0, 0, 0, "minecraft:overworld", 1, 10);
        DeathTracker.DeathSnapshot second = tracker.record(
            1, 2, 3, "minecraft:overworld", 2, 20);

        assertEquals(first.deathId() + 1, second.deathId());
        assertEquals(second, tracker.getLastDeath());
    }
}
