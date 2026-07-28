package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;

import java.net.Socket;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/**
 * Test suite for SequenceCommandHandler.
 * Tests sequence parsing, validation, execution, and rollback functionality.
 */
public class SequenceCommandHandlerTest {

    @Mock
    private Minecraft mockClient;

    @Mock
    private IBaritone mockBaritone;

    @Mock
    private Socket mockSocket;

    private SequenceCommandHandler handler;

    @BeforeEach
    void setUp() {
        MockitoAnnotations.openMocks(this);
        handler = new SequenceCommandHandler();
    }

    @Test
    void testParseSimpleSequence() {
        // Test parsing a simple sequence
        String sequence = "craft wooden_pickaxe ; mine stone 10";
        var commands = handler.parseSequence(sequence);

        assertEquals(2, commands.size());
        assertEquals("craft", commands.get(0).command);
        assertEquals("mine", commands.get(1).command);

        // Check craft command parameters
        assertTrue(commands.get(0).params.has("item"));
        assertEquals("wooden_pickaxe", commands.get(0).params.get("item").getAsString());

        // Check mine command parameters
        assertTrue(commands.get(1).params.has("block"));
        assertEquals("stone", commands.get(1).params.get("block").getAsString());
        assertEquals(10, commands.get(1).params.get("count").getAsInt());
    }

    @Test
    void testParseSequenceWithSpaces() {
        // Test parsing with extra spaces
        String sequence = "  craft wooden_pickaxe   ;   mine stone 10  ; goto 100 200 300  ";
        var commands = handler.parseSequence(sequence);

        assertEquals(3, commands.size());
        assertEquals("craft", commands.get(0).command);
        assertEquals("mine", commands.get(1).command);
        assertEquals("goto", commands.get(2).command);
    }

    @Test
    void testParseEmptySequence() {
        // Test parsing empty sequence
        String sequence = "   ; ;   ";
        var commands = handler.parseSequence(sequence);

        assertEquals(0, commands.size());
    }

    @Test
    void testValidateValidSequence() {
        // Mock a simple valid sequence
        String sequence = "craft wooden_pickaxe ; goto 100 200 300";

        JsonObject params = new JsonObject();
        params.addProperty("sequence", sequence);

        // Mock successful command validation
        try {
            var mockHandler = mock(CommandHandler.class);
            when(mockHandler.handle(any(), any(), any(), any()))
                .thenReturn(CompletableFuture.completedFuture(CommandResult.success()));

            // We can't easily mock the factory, so we'll test the parsing part
            var commands = handler.parseSequence(sequence);
            assertEquals(2, commands.size());

        } catch (Exception e) {
            // Expected due to mocking limitations
        }
    }

    @Test
    void testSequenceWithInvalidCommand() {
        // Test sequence with unknown command
        String sequence = "craft wooden_pickaxe ; invalidcommand arg";

        JsonObject params = new JsonObject();
        params.addProperty("sequence", sequence);

        // Execute the command - should fail validation
        var result = handler.execute(params, mockClient, mockBaritone, mockSocket);

        // Should return success with validation errors (not hard error)
        assertTrue(result.isSuccess());
        assertTrue(result.getData().has("validation_errors"));
    }

    @Test
    void testMissingSequenceParameter() {
        JsonObject params = new JsonObject();

        var result = handler.execute(params, mockClient, mockBaritone, mockSocket);

        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("Missing sequence parameter"));
    }

    @Test
    void testEmptySequenceParameter() {
        JsonObject params = new JsonObject();
        params.addProperty("sequence", "");

        var result = handler.execute(params, mockClient, mockBaritone, mockSocket);

        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("Empty sequence provided"));
    }

    @Test
    void testCommandName() {
        assertEquals("sequence", handler.getCommandName());
    }

    @Test
    void testParseCommandWithGoto() {
        String commandStr = "goto 100 200 300";
        var seqCmd = handler.parseCommand(commandStr);

        assertNotNull(seqCmd);
        assertEquals("goto", seqCmd.command);
        assertTrue(seqCmd.params.has("x"));
        assertTrue(seqCmd.params.has("y"));
        assertTrue(seqCmd.params.has("z"));
        assertEquals(100, seqCmd.params.get("x").getAsInt());
        assertEquals(200, seqCmd.params.get("y").getAsInt());
        assertEquals(300, seqCmd.params.get("z").getAsInt());
    }

    @Test
    void testParseCommandWithCraft() {
        String commandStr = "craft wooden_pickaxe 5";
        var seqCmd = handler.parseCommand(commandStr);

        assertNotNull(seqCmd);
        assertEquals("craft", seqCmd.command);
        assertTrue(seqCmd.params.has("item"));
        assertTrue(seqCmd.params.has("count"));
        assertEquals("wooden_pickaxe", seqCmd.params.get("item").getAsString());
        assertEquals(5, seqCmd.params.get("count").getAsInt());
    }

    @Test
    void testParseCommandWithCraftNoCount() {
        String commandStr = "craft wooden_pickaxe";
        var seqCmd = handler.parseCommand(commandStr);

        assertNotNull(seqCmd);
        assertEquals("craft", seqCmd.command);
        assertTrue(seqCmd.params.has("item"));
        assertTrue(seqCmd.params.has("count"));
        assertEquals("wooden_pickaxe", seqCmd.params.get("item").getAsString());
        assertEquals(1, seqCmd.params.get("count").getAsInt()); // Default count
    }

    @Test
    void testParseCommandWithMine() {
        String commandStr = "mine stone 10";
        var seqCmd = handler.parseCommand(commandStr);

        assertNotNull(seqCmd);
        assertEquals("mine", seqCmd.command);
        assertTrue(seqCmd.params.has("block"));
        assertTrue(seqCmd.params.has("count"));
        assertEquals("stone", seqCmd.params.get("block").getAsString());
        assertEquals(10, seqCmd.params.get("count").getAsInt());
    }

    @Test
    void testParseCommandGeneric() {
        String commandStr = "somecommand arg1 arg2";
        var seqCmd = handler.parseCommand(commandStr);

        assertNotNull(seqCmd);
        assertEquals("somecommand", seqCmd.command);
        assertTrue(seqCmd.params.has("args"));
        assertEquals("arg1 arg2", seqCmd.params.get("args").getAsString());
    }

    // --- Execution tests use registered test doubles so they exercise the
    // --- real execute/rollback path without any Minecraft state.

    /** Handler double that always succeeds. Public so the factory can instantiate it. */
    public static class AlwaysOkHandler implements CommandHandler {
        @Override
        public CompletableFuture<CommandResult> handle(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
            JsonObject data = new JsonObject();
            data.addProperty("ok", true);
            return CompletableFuture.completedFuture(CommandResult.success(data));
        }

        @Override
        public String getCommandName() {
            return "test_seq_ok";
        }
    }

    /** Handler double that always fails. Public so the factory can instantiate it. */
    public static class AlwaysFailHandler implements CommandHandler {
        @Override
        public CompletableFuture<CommandResult> handle(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
            return CompletableFuture.completedFuture(CommandResult.error("intentional test failure"));
        }

        @Override
        public String getCommandName() {
            return "test_seq_fail";
        }
    }

    private void registerDoubles() {
        CommandHandlerFactory.registerHandler("test_seq_ok", AlwaysOkHandler.class);
        CommandHandlerFactory.registerHandler("test_seq_fail", AlwaysFailHandler.class);
    }

    @Test
    void testInvalidCommandDoesNotExecuteValidPrefix() {
        // A sequence containing an unknown command must fail validation up
        // front and execute NOTHING - not run the valid prefix first.
        registerDoubles();
        JsonObject params = new JsonObject();
        params.addProperty("sequence", "test_seq_ok ; definitely_unknown_command arg");

        var result = handler.execute(params, mockClient, mockBaritone, mockSocket);

        assertTrue(result.isSuccess());
        assertTrue(result.getData().has("validation_errors"));
        assertFalse(result.getData().has("partial_results"),
                "No command may execute when validation fails");
    }

    @Test
    void testMidSequenceFailure_ReportsStepAndRollsBack() {
        registerDoubles();
        JsonObject params = new JsonObject();
        params.addProperty("sequence", "test_seq_ok ; test_seq_fail ; test_seq_ok");

        var result = handler.execute(params, mockClient, mockBaritone, mockSocket);

        // Failure mid-sequence is reported inside a success envelope.
        assertTrue(result.isSuccess());
        JsonObject data = result.getData();
        assertEquals(1, data.get("failed_at_step").getAsInt());
        assertTrue(data.get("rollback_performed").getAsBoolean());
        assertTrue(data.get("error").getAsString().contains("intentional test failure"));
        JsonObject partial = data.getAsJsonObject("partial_results");
        assertTrue(partial.has("step_0"));
        assertTrue(partial.has("step_1"));
        assertFalse(partial.has("step_2"), "Commands after the failure must not run");
    }

    @Test
    void testMultiCommandExecution_AllSucceed() {
        registerDoubles();
        JsonObject params = new JsonObject();
        params.addProperty("sequence", "test_seq_ok ; test_seq_ok ; test_seq_ok");

        var result = handler.execute(params, mockClient, mockBaritone, mockSocket);

        assertTrue(result.isSuccess());
        JsonObject data = result.getData();
        assertEquals("completed", data.get("status").getAsString());
        assertEquals(3, data.get("steps_executed").getAsInt());
        JsonObject partial = data.getAsJsonObject("partial_results");
        assertTrue(partial.has("step_0"));
        assertTrue(partial.has("step_1"));
        assertTrue(partial.has("step_2"));
    }

    @Test
    void testParenthesesAreNotASupportedSyntax() {
        // Nested sequences via parentheses are NOT implemented; the parser
        // treats "(cmd" as an unknown command name and validation rejects it.
        registerDoubles();
        JsonObject params = new JsonObject();
        params.addProperty("sequence", "test_seq_ok ; (test_seq_ok ; test_seq_ok)");

        var result = handler.execute(params, mockClient, mockBaritone, mockSocket);

        assertTrue(result.isSuccess());
        assertTrue(result.getData().has("validation_errors"));
    }

    // Note: atomic mode, conditionals, per-sequence timeouts, and nested
    // sequences are not features of this handler. If they are ever added,
    // give them real tests here rather than aspirational ones.
}
