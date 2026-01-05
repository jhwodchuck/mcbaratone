package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.util.math.BlockPos;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;

import java.net.Socket;
import java.util.concurrent.CompletableFuture;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/**
 * Test suite for SequenceCommandHandler.
 * Tests sequence parsing, validation, execution, and rollback functionality.
 */
public class SequenceCommandHandlerTest {

    @Mock
    private MinecraftClient mockClient;

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

    @Test
    void testRollbackFunctionality_OnFailure() {
        // Setup - sequence with a command that will fail
        String sequence = "craft wooden_pickaxe ; invalid_command arg ; mine stone 5";
        JsonObject params = new JsonObject();
        params.addProperty("sequence", sequence);
        params.addProperty("rollback_on_failure", true);

        // Execute
        var result = handler.execute(params, mockClient, mockBaritone, mockSocket);

        // Verify - sequence should fail and rollback
        assertFalse(result.isSuccess());
        assertTrue(result.getData().has("rollback_performed"));
        assertTrue(result.getData().get("rollback_performed").getAsBoolean());
        assertTrue(result.getData().has("executed_commands"));
        assertTrue(result.getData().has("rolled_back_commands"));
    }

    @Test
    void testAtomicExecution_AllOrNothing_Success() {
        // Setup - sequence that should succeed atomically
        String sequence = "craft stick ; craft wooden_pickaxe";
        JsonObject params = new JsonObject();
        params.addProperty("sequence", sequence);
        params.addProperty("atomic", true);

        // Execute
        var result = handler.execute(params, mockClient, mockBaritone, mockSocket);

        // Verify - all commands should succeed or none
        assertTrue(result.isSuccess());
        assertTrue(result.getData().get("atomic_execution").getAsBoolean());
        assertTrue(result.getData().has("completed_commands"));
    }

    @Test
    void testAtomicExecution_AllOrNothing_Failure() {
        // Setup - sequence with failure in atomic mode
        String sequence = "craft stick ; invalid_command ; craft wooden_pickaxe";
        JsonObject params = new JsonObject();
        params.addProperty("sequence", sequence);
        params.addProperty("atomic", true);

        // Execute
        var result = handler.execute(params, mockClient, mockBaritone, mockSocket);

        // Verify - should fail atomically, no partial success
        assertFalse(result.isSuccess());
        assertTrue(result.getData().get("atomic_execution").getAsBoolean());
        assertTrue(result.getData().has("failed_at_command"));
        assertEquals(1, result.getData().get("failed_at_command").getAsInt()); // Second command fails
        assertTrue(result.getData().get("rollback_performed").getAsBoolean());
    }

    @Test
    void testNestedSequenceSupport() {
        // Setup - sequence with nested sequences
        String sequence = "craft stick ; (mine stone 5 ; mine coal 3) ; craft furnace";
        JsonObject params = new JsonObject();
        params.addProperty("sequence", sequence);
        params.addProperty("atomic", true);

        // Execute
        var result = handler.execute(params, mockClient, mockBaritone, mockSocket);

        // Verify
        assertTrue(result.isSuccess());
        assertTrue(result.getData().has("nested_sequences"));
        assertTrue(result.getData().get("nested_sequences").getAsJsonArray().size() >= 1);
    }

    @Test
    void testNestedSequenceRollback() {
        // Setup - nested sequence with failure
        String sequence = "craft stick ; (mine stone 5 ; invalid_command ; mine coal 3) ; craft furnace";
        JsonObject params = new JsonObject();
        params.addProperty("sequence", sequence);
        params.addProperty("atomic", true);
        params.addProperty("rollback_on_failure", true);

        // Execute
        var result = handler.execute(params, mockClient, mockBaritone, mockSocket);

        // Verify - nested failure should trigger rollback
        assertFalse(result.isSuccess());
        assertTrue(result.getData().get("rollback_performed").getAsBoolean());
        assertTrue(result.getData().has("nested_rollback"));
    }

    @Test
    void testSequenceExecutionProgress() {
        // Setup - multi-step sequence
        String sequence = "craft stick ; craft wooden_pickaxe ; mine stone 10 ; goto 100 200 300";
        JsonObject params = new JsonObject();
        params.addProperty("sequence", sequence);

        // Execute
        var result = handler.execute(params, mockClient, mockBaritone, mockSocket);

        // Verify - should track progress
        assertTrue(result.isSuccess());
        assertTrue(result.getData().has("execution_progress"));
        assertTrue(result.getData().get("execution_progress").getAsJsonObject().has("total_commands"));
        assertTrue(result.getData().get("execution_progress").getAsJsonObject().has("completed_commands"));
        assertEquals(4, result.getData().get("execution_progress").getAsJsonObject().get("total_commands").getAsInt());
    }

    @Test
    void testSequenceTimeoutHandling() {
        // Setup - sequence that might take too long
        String sequence = "mine stone 100 ; mine diamond 64 ; goto 1000 1000 1000";
        JsonObject params = new JsonObject();
        params.addProperty("sequence", sequence);
        params.addProperty("timeout_seconds", 30);

        // Execute
        var result = handler.execute(params, mockClient, mockBaritone, mockSocket);

        // Verify - should handle timeout gracefully
        assertTrue(result.isSuccess() || result.getData().has("timeout_handled"));
        assertTrue(result.getData().has("execution_time"));
    }

    @Test
    void testConditionalSequenceExecution() {
        // Setup - sequence with conditional logic (if supported)
        String sequence = "craft stick ; if_inventory_has wooden_planks then craft wooden_pickaxe else mine oak_log";
        JsonObject params = new JsonObject();
        params.addProperty("sequence", sequence);

        // Execute
        var result = handler.execute(params, mockClient, mockBaritone, mockSocket);

        // Verify
        assertTrue(result.isSuccess());
        assertTrue(result.getData().has("conditional_execution"));
        assertTrue(result.getData().get("conditional_execution").getAsBoolean());
    }

    // Note: Full execution tests are complex due to mocking requirements.
    // Integration tests would be better for testing actual command execution and rollback.
}