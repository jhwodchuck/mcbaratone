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
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.when;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;

class RequestProcessorTest {
    private static CommandDispatcher buildFailingDispatcher() {
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
        return dispatcher;
    }

    private RequestProcessor createProcessor(CommandDispatcher dispatcher) {
        IBaritone baritone = mock(IBaritone.class);
        Minecraft client = mock(Minecraft.class);
        BaritoneAPIBridge.IPlayerContext playerContext =
            mock(BaritoneAPIBridge.IPlayerContext.class);
        when(playerContext.isPlayerNull()).thenReturn(false);
        return new RequestProcessor(
            dispatcher,
            () -> baritone,
            () -> client,
            playerContext,
            new EventManager(),
            null,
            command -> false);
    }

    @Test
    void preservesTypedErrorsAndCanonicalRequestSeqCorrelation() {
        CommandDispatcher dispatcher = buildFailingDispatcher();
        RequestProcessor processor = createProcessor(dispatcher);

        JsonObject request = new JsonObject();
        request.addProperty("id", "request-42");
        request.addProperty("request_seq", 99L);
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
        verify(dispatcher, times(1)).dispatchCommand(any(), any(), any(), any());
    }

    @Test
    void preservesTypedErrorsAndLegacySeqCorrelation() {
        CommandDispatcher dispatcher = buildFailingDispatcher();
        RequestProcessor processor = createProcessor(dispatcher);

        JsonObject request = new JsonObject();
        request.addProperty("id", "request-legacy");
        request.addProperty("seq", 44L);
        request.addProperty("command", "goto");
        request.add("params", new JsonObject());
        JsonObject response = processor.processCommand(request, null);

        assertEquals(44L, response.get("request_seq").getAsLong());
        assertEquals("request-legacy", response.get("id").getAsString());
    }

    @Test
    void acceptsMatchingCanonicalAndLegacySequenceValues() {
        CommandDispatcher dispatcher = buildFailingDispatcher();
        RequestProcessor processor = createProcessor(dispatcher);

        JsonObject request = new JsonObject();
        request.addProperty("id", "request-both");
        request.addProperty("request_seq", 12L);
        request.addProperty("seq", 12L);
        request.addProperty("command", "goto");
        request.add("params", new JsonObject());
        JsonObject response = processor.processCommand(request, null);

        assertEquals(12L, response.get("request_seq").getAsLong());
        assertEquals("error", response.get("status").getAsString());
        verify(dispatcher, times(1)).dispatchCommand(any(), any(), any(), any());
    }

    @Test
    void rejectsConflictingSequenceValues() {
        CommandDispatcher dispatcher = buildFailingDispatcher();
        RequestProcessor processor = createProcessor(dispatcher);

        JsonObject request = new JsonObject();
        request.addProperty("id", "request-conflict");
        request.addProperty("request_seq", 99L);
        request.addProperty("seq", 100L);
        request.addProperty("command", "goto");
        request.add("params", new JsonObject());
        JsonObject response = processor.processCommand(request, null);

        assertEquals("error", response.get("status").getAsString());
        assertTrue(response.get("error").getAsString().contains("Conflicting request sequence values"));
        assertEquals("request-conflict", response.get("id").getAsString());
        assertEquals(99L, response.get("request_seq").getAsLong());
        verify(dispatcher, never()).dispatchCommand(any(), any(), any(), any());
    }

    @Test
    void preservesErrorEnvelopeWhenCommandMissing() {
        CommandDispatcher dispatcher = mock(CommandDispatcher.class);
        RequestProcessor processor = createProcessor(dispatcher);

        JsonObject request = new JsonObject();
        request.addProperty("id", "request-missing");
        request.addProperty("request_seq", 77L);

        JsonObject response = processor.processCommand(request, null);

        assertEquals("error", response.get("status").getAsString());
        assertEquals("Missing command", response.get("error").getAsString());
        assertEquals(77L, response.get("request_seq").getAsLong());
        assertEquals("request-missing", response.get("id").getAsString());
        assertEquals(
            GetVersionCommandHandler.getBridgeInstanceId(),
            response.get("bridge_session_id").getAsString());
        verify(dispatcher, never()).dispatchCommand(any(), any(), any(), any());
    }
}
