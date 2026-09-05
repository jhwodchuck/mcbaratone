package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.util.concurrent.atomic.AtomicReference;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.damagesource.DamageSource;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntityType;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class StateCommandHandlerTest {

    @BeforeAll
    static void bootstrap() {
        // Must run before any test touches Entity/EntityType/DamageSource.
        // Skipping this let a class-load order where this class ran before
        // MutationHandlerBehaviorTest leave Minecraft's registries in a
        // partially-touched state, breaking THAT class's own bootstrap call
        // with ExceptionInInitializerError even though every test here still
        // passed -- a single test file passing in isolation is not evidence
        // it did not corrupt shared JVM state for the rest of the suite.
        net.minecraft.SharedConstants.tryDetectVersion();
        net.minecraft.server.Bootstrap.bootStrap();
    }

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

    // Three of four A1 deaths in the 2026-09-04 telemetry capture had no
    // attributable cause because get_state never carried one. These pin the
    // schema that closes that gap, independent of the full snapshot pipeline
    // this file's other test deliberately does not exercise.

    @Test
    void noPriorDamageReportsUnavailableRatherThanOmittingTheKey() {
        LocalPlayer player = mock(LocalPlayer.class);
        when(player.getLastDamageSource()).thenReturn(null);

        JsonObject snapshot = new StateCommandHandler().lastDamageSourceSnapshot(player);

        assertFalse(snapshot.get("available").getAsBoolean());
        assertFalse(snapshot.has("type"), "no source means no other fields either");
    }

    @Test
    void meleeDamageReportsOneAttackerNotAsAProjectile() {
        LocalPlayer player = mock(LocalPlayer.class);
        DamageSource source = mock(DamageSource.class);
        Entity attacker = mock(Entity.class);
        when(attacker.getId()).thenReturn(42);
        when(attacker.getStringUUID()).thenReturn("attacker-uuid");
        when(attacker.getType()).thenReturn(mock(EntityType.class));
        when(source.getMsgId()).thenReturn("mob");
        when(source.getEntity()).thenReturn(attacker);
        when(source.getDirectEntity()).thenReturn(attacker);
        when(player.getLastDamageSource()).thenReturn(source);

        JsonObject snapshot = new StateCommandHandler().lastDamageSourceSnapshot(player);

        assertTrue(snapshot.get("available").getAsBoolean());
        assertEquals("mob", snapshot.get("type").getAsString());
        assertEquals(42, snapshot.get("attacker_id").getAsInt());
        assertFalse(snapshot.get("is_projectile").getAsBoolean());
        assertFalse(snapshot.has("direct_source_id"), "melee has no separate direct source");
    }

    @Test
    void projectileDamageDistinguishesShooterFromTheProjectileItself() {
        LocalPlayer player = mock(LocalPlayer.class);
        DamageSource source = mock(DamageSource.class);
        Entity shooter = mock(Entity.class);
        Entity arrow = mock(Entity.class);
        when(shooter.getId()).thenReturn(7);
        when(shooter.getStringUUID()).thenReturn("shooter-uuid");
        when(shooter.getType()).thenReturn(mock(EntityType.class));
        when(arrow.getId()).thenReturn(8);
        when(arrow.getStringUUID()).thenReturn("arrow-uuid");
        when(arrow.getType()).thenReturn(mock(EntityType.class));
        when(source.getMsgId()).thenReturn("arrow");
        when(source.getEntity()).thenReturn(shooter);
        when(source.getDirectEntity()).thenReturn(arrow);
        when(player.getLastDamageSource()).thenReturn(source);

        JsonObject snapshot = new StateCommandHandler().lastDamageSourceSnapshot(player);

        assertTrue(snapshot.get("is_projectile").getAsBoolean());
        assertEquals(7, snapshot.get("attacker_id").getAsInt());
        assertEquals(8, snapshot.get("direct_source_id").getAsInt());
    }

    @Test
    void entitylessDamageReportsTypeWithoutFabricatingAnAttacker() {
        // Fall, lava, drowning, and starvation carry no entity at all. The
        // review's fourth A1 death was a fall inference precisely because
        // nothing distinguished this case from an unknown one.
        LocalPlayer player = mock(LocalPlayer.class);
        DamageSource source = mock(DamageSource.class);
        when(source.getMsgId()).thenReturn("fall");
        when(source.getEntity()).thenReturn(null);
        when(source.getDirectEntity()).thenReturn(null);
        when(player.getLastDamageSource()).thenReturn(source);

        JsonObject snapshot = new StateCommandHandler().lastDamageSourceSnapshot(player);

        assertTrue(snapshot.get("available").getAsBoolean());
        assertEquals("fall", snapshot.get("type").getAsString());
        assertFalse(snapshot.has("attacker_id"));
        assertFalse(snapshot.has("direct_source_id"));
        assertFalse(snapshot.get("is_projectile").getAsBoolean());
    }
}
