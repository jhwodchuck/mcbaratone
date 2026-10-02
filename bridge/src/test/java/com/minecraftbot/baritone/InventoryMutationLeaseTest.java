package com.minecraftbot.baritone;

import static org.junit.jupiter.api.Assertions.*;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;
import org.junit.jupiter.api.Test;

class InventoryMutationLeaseTest {
    @Test
    void pausesAndRestoresOriginallyEnabledOptimizer() {
        AtomicBoolean enabled = new AtomicBoolean(true);
        InventoryMutationLease lease = new InventoryMutationLease(enabled::get, enabled::set);
        List<Runnable> expiry = new ArrayList<>();
        lease.hold(expiry::add);
        assertFalse(enabled.get());
        expiry.getFirst().run();
        assertTrue(enabled.get());
    }

    @Test
    void renewedLeaseDoesNotRestoreEarlyOrForgetOriginalValue() {
        AtomicBoolean enabled = new AtomicBoolean(true);
        InventoryMutationLease lease = new InventoryMutationLease(enabled::get, enabled::set);
        List<Runnable> expiry = new ArrayList<>();
        lease.hold(expiry::add);
        lease.hold(expiry::add);
        expiry.getFirst().run();
        assertFalse(enabled.get());
        expiry.getLast().run();
        assertTrue(enabled.get());
        enabled.set(false);
        expiry.getLast().run();
        assertFalse(enabled.get(), "duplicate expiry must not restore twice");
    }

    @Test
    void preservesOriginallyDisabledOptimizer() {
        AtomicBoolean enabled = new AtomicBoolean(false);
        InventoryMutationLease lease = new InventoryMutationLease(enabled::get, enabled::set);
        List<Runnable> expiry = new ArrayList<>();
        lease.hold(expiry::add);
        expiry.getFirst().run();
        assertFalse(enabled.get());
    }

    @Test
    void operatorOverrideInvalidatesOutstandingExpiryAndNextLeaseCapturesNewValue() {
        AtomicBoolean enabled = new AtomicBoolean(true);
        InventoryMutationLease lease = new InventoryMutationLease(enabled::get, enabled::set);
        List<Runnable> expiry = new ArrayList<>();
        lease.hold(expiry::add);
        enabled.set(false);
        lease.override();
        expiry.getFirst().run();
        assertFalse(enabled.get());
        lease.hold(expiry::add);
        expiry.getLast().run();
        assertFalse(enabled.get());
    }

    @Test
    void preservesExternalReenable() {
        AtomicBoolean enabled = new AtomicBoolean(false);
        InventoryMutationLease lease = new InventoryMutationLease(enabled::get, enabled::set);
        List<Runnable> expiry = new ArrayList<>();
        lease.hold(expiry::add);
        enabled.set(true);
        expiry.getFirst().run();
        assertTrue(enabled.get());
    }

    @Test
    void schedulingFailureRestoresOriginalSettingAndThrowsBeforeMutation() {
        AtomicBoolean enabled = new AtomicBoolean(true);
        InventoryMutationLease lease = new InventoryMutationLease(enabled::get, enabled::set);
        assertThrows(IllegalStateException.class, () -> lease.hold(release -> {
            throw new IllegalStateException("scheduler unavailable");
        }));
        assertTrue(enabled.get());
    }
}
