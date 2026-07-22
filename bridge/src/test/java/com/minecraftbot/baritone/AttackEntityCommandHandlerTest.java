package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;

class AttackEntityCommandHandlerTest {

    @Test
    void clampsCooldownThresholdToProtocolRange() {
        assertEquals(0.0f, AttackEntityCommandHandler.clampCooldown(-1.0f));
        assertEquals(0.75f, AttackEntityCommandHandler.clampCooldown(0.75f));
        assertEquals(1.0f, AttackEntityCommandHandler.clampCooldown(2.0f));
    }

    @Test
    void attacksOnlyAtOrAboveRequestedCooldown() {
        assertFalse(AttackEntityCommandHandler.cooldownReady(0.89f, 0.9f));
        assertTrue(AttackEntityCommandHandler.cooldownReady(0.9f, 0.9f));
        assertTrue(AttackEntityCommandHandler.cooldownReady(1.0f, 0.9f));
    }

    @Test
    void rejectsMissingEntityBeforeSchedulingMinecraftWork() throws Exception {
        CommandResult result = new AttackEntityCommandHandler().handle(
            new JsonObject(),
            mock(MinecraftClient.class),
            mock(IBaritone.class),
            null
        ).get();

        assertFalse(result.isSuccess());
        assertEquals("Missing required parameter: entity_id", result.getErrorMessage());
    }
}
