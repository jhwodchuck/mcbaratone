package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.InteractionHand;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

class SwingHandCommandHandlerTest {

    @BeforeAll
    static void bootstrap() {
        // Mockito's inline mock maker instruments the whole class being
        // mocked, touching every referenced type -- including
        // BuiltInRegistries -- during class initialization. Mocking
        // LocalPlayer without bootstrapping first throws "Not bootstrapped"
        // deep in a static initializer and poisons the JVM's registry state
        // for every later mock in the same run, not just this one.
        net.minecraft.SharedConstants.tryDetectVersion();
        net.minecraft.server.Bootstrap.bootStrap();
    }

    private static Minecraft clientWithPlayer(LocalPlayer player) {
        Minecraft client = mock(Minecraft.class);
        TestUtils.setField(client, "player", player);
        doAnswer(invocation -> {
            invocation.<Runnable>getArgument(0).run();
            return null;
        }).when(client).execute(any(Runnable.class));
        return client;
    }

    @Test
    void reportsItsCommandName() {
        assertEquals("swing_hand", new SwingHandCommandHandler().getCommandName());
    }

    @Test
    void rejectsMissingPlayerBeforeSchedulingMinecraftWork() throws Exception {
        CommandResult result = new SwingHandCommandHandler().handle(
            new JsonObject(), mock(Minecraft.class), mock(IBaritone.class), null
        ).get();

        assertFalse(result.isSuccess());
    }

    @Test
    void swingsTheMainHandByDefault() throws Exception {
        LocalPlayer player = mock(LocalPlayer.class);
        Minecraft client = clientWithPlayer(player);

        CommandResult result = new SwingHandCommandHandler().handle(
            new JsonObject(), client, mock(IBaritone.class), null
        ).get();

        assertTrue(result.isSuccess());
        verify(player).swing(InteractionHand.MAIN_HAND);
    }

    @Test
    void swingsTheRequestedOffHand() throws Exception {
        LocalPlayer player = mock(LocalPlayer.class);
        Minecraft client = clientWithPlayer(player);
        JsonObject params = new JsonObject();
        params.addProperty("hand", "OFF_HAND");

        new SwingHandCommandHandler().handle(
            params, client, mock(IBaritone.class), null
        ).get();

        verify(player).swing(InteractionHand.OFF_HAND);
    }
}
