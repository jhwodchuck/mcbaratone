package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class RequestProcessorTest {
    @Test
    void preservesTypedErrorsAndCorrelationEnvelope() {
        CommandDispatcher dispatcher = mock(CommandDispatcher.class);
        CommandResult failure = CommandResult.error(
            ErrorCode.INVALID_PARAMETER_VALUE,
            ErrorSeverity.ERROR,
            "bad radius",
            "radius",
            "-1",
            "positive integer",
            "use at least 1");
        when(dispatcher.dispatchCommand(any(), any(), any(), any()))
            .thenReturn(failure);

        IBaritone baritone = mock(IBaritone.class);
        Minecraft client = mock(Minecraft.class);
        BaritoneAPIBridge.IPlayerContext playerContext =
            mock(BaritoneAPIBridge.IPlayerContext.class);
        when(playerContext.isPlayerNull()).thenReturn(false);
        RequestProcessor processor = new RequestProcessor(
            dispatcher,
            () -> baritone,
            () -> client,
            playerContext,
            new EventManager(),
            null,
            command -> false);

        JsonObject request = new JsonObject();
        request.addProperty("id", "request-42");
        request.addProperty("seq", 99L);
        request.addProperty("command", "goto");
        request.add("params", new JsonObject());
        JsonObject response = processor.processCommand(request, null);

        assertEquals("error", response.get("status").getAsString());
        assertEquals("bad radius", response.get("error").getAsString());
        assertEquals("INVALID_PARAMETER_VALUE",
            response.getAsJsonArray("errors").get(0).getAsJsonObject()
                .get("code").getAsString());
        assertFalse(response.getAsJsonArray("errors").get(0).getAsJsonObject()
            .get("retryable").getAsBoolean());
        assertEquals("request-42", response.get("id").getAsString());
        assertEquals(99L, response.get("request_seq").getAsLong());
        assertEquals(response.get("seq"), response.get("server_seq"));
        assertEquals(
            GetVersionCommandHandler.getBridgeInstanceId(),
            response.get("bridge_session_id").getAsString());
        assertTrue(response.get("timestamp").getAsLong() > 0L);
    }
}
