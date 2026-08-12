package com.minecraftbot.baritone;

import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;

/** Thread-safe source of truth for the most recently observed player death. */
public final class DeathTracker {
    public record DeathSnapshot(
            long deathId,
            double x,
            double y,
            double z,
            String dimension,
            long gameTick,
            long timestampMs) {
    }

    private final AtomicLong nextDeathId = new AtomicLong(0);
    private final AtomicReference<DeathSnapshot> lastDeath = new AtomicReference<>();

    public DeathSnapshot record(
            double x,
            double y,
            double z,
            String dimension,
            long gameTick,
            long timestampMs) {
        DeathSnapshot snapshot = new DeathSnapshot(
                nextDeathId.incrementAndGet(),
                x,
                y,
                z,
                dimension,
                gameTick,
                timestampMs);
        lastDeath.set(snapshot);
        return snapshot;
    }

    public DeathSnapshot getLastDeath() {
        return lastDeath.get();
    }
}
