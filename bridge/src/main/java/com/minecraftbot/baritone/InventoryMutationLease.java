package com.minecraftbot.baritone;

import baritone.api.BaritoneAPI;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;
import net.minecraft.client.Minecraft;

/** Prevent native hotbar rearrangement from racing bridge inventory transactions. */
final class InventoryMutationLease {
    private static final ScheduledExecutorService EXPIRY = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread thread = new Thread(r, "bridge-inventory-lease");
        thread.setDaemon(true);
        return thread;
    });
    private static final InventoryMutationLease SHARED = new InventoryMutationLease(
        () -> BaritoneAPI.getSettings().allowInventory.value,
        value -> BaritoneAPI.getSettings().allowInventory.value = value);

    private final BooleanSupplier enabled;
    private final Consumer<Boolean> setEnabled;
    private Boolean original;
    private long generation;

    InventoryMutationLease(BooleanSupplier enabled, Consumer<Boolean> setEnabled) {
        this.enabled = enabled;
        this.setEnabled = setEnabled;
    }

    // Acquire and release on the client thread, just like the inventory mutation.
    static void hold(Minecraft client) {
        SHARED.hold(release -> EXPIRY.schedule(
            () -> client.execute(release), 10, TimeUnit.SECONDS));
    }

    static void operatorOverride() {
        SHARED.override();
    }

    synchronized void hold(Consumer<Runnable> scheduleRelease) {
        if (original == null) original = enabled.getAsBoolean();
        long token = ++generation;
        try {
            setEnabled.accept(false);
            scheduleRelease.accept(() -> release(token));
        } catch (RuntimeException error) {
            release(token);
            throw error;
        }
    }

    private synchronized void release(long token) {
        if (token != generation || original == null) return;
        boolean restore = original;
        original = null;
        // Do not overwrite a direct external re-enable while the lease was held.
        if (!enabled.getAsBoolean()) setEnabled.accept(restore);
    }

    synchronized void override() {
        original = null;
        ++generation;
    }
}
