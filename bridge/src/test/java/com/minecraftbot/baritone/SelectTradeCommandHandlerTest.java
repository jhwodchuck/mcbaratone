package com.minecraftbot.baritone;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;

class SelectTradeCommandHandlerTest {
    @Test
    void usesAsynchronousHandlerSoPollingNeverBlocksClientThread() {
        assertTrue(AsyncCommandHandler.class.isAssignableFrom(SelectTradeCommandHandler.class));
    }

    @Test
    void parsesStrictIntegerParameters() {
        JsonObject params = new JsonObject();
        params.addProperty("index", 3);
        params.addProperty("count", 2);

        assertEquals(3, SelectTradeCommandHandler.readInteger(params, "index", true));
        assertEquals(2, SelectTradeCommandHandler.readInteger(params, "count", false));
    }

    @Test
    void defaultsAbsentOptionalCountToZero() {
        assertEquals(0, SelectTradeCommandHandler.readInteger(new JsonObject(), "count", false));
    }

    @Test
    void rejectsMissingRequiredOrNonIntegerValues() {
        JsonObject decimal = new JsonObject();
        decimal.addProperty("count", 1.5);
        JsonObject string = new JsonObject();
        string.addProperty("count", "1");

        assertNull(SelectTradeCommandHandler.readInteger(new JsonObject(), "index", true));
        assertNull(SelectTradeCommandHandler.readInteger(decimal, "count", false));
        assertNull(SelectTradeCommandHandler.readInteger(string, "count", false));
    }

    @Test
    void confirmsPurchaseOnlyWithExactUsesAndInventoryEvidence() {
        assertFalse(SelectTradeCommandHandler.confirmsCompletedTrade(5, 6, 3, 4));
        assertFalse(SelectTradeCommandHandler.confirmsCompletedTrade(6, 6, 3, 4));
        assertFalse(SelectTradeCommandHandler.confirmsCompletedTrade(7, 6, 4, 4));
        assertTrue(SelectTradeCommandHandler.confirmsCompletedTrade(6, 6, 4, 4));
    }
}
