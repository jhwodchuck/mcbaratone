package com.minecraftbot.baritone;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.google.gson.JsonObject;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

public class SetFastBreakCommandHandlerTest {

    private final SetFastBreakCommandHandler handler = new SetFastBreakCommandHandler();

    @AfterEach
    void resetMode() {
        ManualMiningController.getInstance().configureMode(FastBreakMode.OFF);
    }

    @Test
    void aggressiveModeReportsBothCapabilities() throws Exception {
        JsonObject params = new JsonObject();
        params.addProperty("mode", "aggressive");

        CommandResult result = handler.handle(params, null, null, null).get();

        assertTrue(result.isSuccess());
        assertEquals(FastBreakMode.AGGRESSIVE, ManualMiningController.getInstance().getMode());
        assertEquals("aggressive", result.getData().get("mode").getAsString());
        assertTrue(result.getData().get("no_break_delay").getAsBoolean());
        assertTrue(result.getData().get("early_stop").getAsBoolean());
    }

    @Test
    void legitModeDoesNotClaimEarlyStop() throws Exception {
        JsonObject params = new JsonObject();
        params.addProperty("mode", "legit");

        CommandResult result = handler.handle(params, null, null, null).get();

        assertTrue(result.isSuccess());
        assertEquals(FastBreakMode.LEGIT, ManualMiningController.getInstance().getMode());
        assertTrue(result.getData().get("no_break_delay").getAsBoolean());
        assertFalse(result.getData().get("early_stop").getAsBoolean());
    }

    @Test
    void invalidModeFailsClosed() throws Exception {
        JsonObject params = new JsonObject();
        params.addProperty("mode", "instant-ish");

        CommandResult result = handler.handle(params, null, null, null).get();

        assertFalse(result.isSuccess());
        assertEquals(FastBreakMode.OFF, ManualMiningController.getInstance().getMode());
    }
}
