package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import baritone.api.pathing.goals.GoalYLevel;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;

import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import net.minecraft.client.Minecraft;
import org.junit.jupiter.api.Disabled;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@Disabled("Requires Minecraft Bootstrap (Goal classes initialization) which fails in unit test environment")
class AdvancedGoalCommandHandlerTest {

    @Mock
    private Minecraft mockClient;

    @Mock
    private IBaritone mockBaritone;

    private AdvancedGoalCommandHandler handler;

    @BeforeEach
    void setUp() {
        MockitoAnnotations.openMocks(this);
        handler = new AdvancedGoalCommandHandler();

        // Mock Baritone process
        var mockCustomGoalProcess = mock(baritone.api.process.ICustomGoalProcess.class);
        when(mockBaritone.getCustomGoalProcess()).thenReturn(mockCustomGoalProcess);
        
        // Setup PathingBehavior for cancel
        var mockPathing = mock(baritone.api.behavior.IPathingBehavior.class);
        when(mockBaritone.getPathingBehavior()).thenReturn(mockPathing);

        // Stub client.execute to run immediately
        doAnswer(invocation -> {
            Runnable r = invocation.getArgument(0);
            r.run();
            return null;
        }).when(mockClient).execute(any(Runnable.class));
    }

    @Test
    void testCommandName() {
        assertEquals("advanced_goal", handler.getCommandName());
    }

    @Test
    void testSetGoal_Success() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("action", "set");
        params.addProperty("type", "y_level");
        params.addProperty("y", 100);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertTrue(result.getData().get("goal_set").getAsBoolean());
        assertEquals("y_level", result.getData().get("goal_type").getAsString());
    }

    @Test
    void testGoalChain_Success() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("action", "chain");
        params.addProperty("chain_id", "test_chain");

        JsonArray steps = new JsonArray();
        JsonObject step1 = new JsonObject();
        step1.addProperty("type", "y_level");
        step1.addProperty("y", 50);
        step1.addProperty("delay_ms", 1000);
        steps.add(step1);

        JsonObject step2 = new JsonObject();
        step2.addProperty("type", "block_pos");
        step2.addProperty("x", 100);
        step2.addProperty("y", 64);
        step2.addProperty("z", 100);
        steps.add(step2);

        params.add("steps", steps);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertTrue(result.getData().get("chain_created").getAsBoolean());
        assertEquals("test_chain", result.getData().get("chain_id").getAsString());
        assertEquals(2, result.getData().get("steps").getAsInt());
    }

    @Test
    void testTimeBasedGoal_Future() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("action", "time_based");
        params.addProperty("type", "y_level");
        params.addProperty("y", 75);
        params.addProperty("execute_at", System.currentTimeMillis() + 60000); // 1 minute from now

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertTrue(result.getData().get("chain_created").getAsBoolean());
    }

    @Test
    void testTimeBasedGoal_Past() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("action", "time_based");
        params.addProperty("type", "y_level");
        params.addProperty("y", 75);
        params.addProperty("execute_at", System.currentTimeMillis() - 60000); // 1 minute ago

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify - should execute immediately
        assertTrue(result.isSuccess());
        assertTrue(result.getData().get("goal_set").getAsBoolean());
    }

    @Test
    void testCompositeGoal_Success() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("action", "composite");
        params.addProperty("operator", "and");

        JsonArray goals = new JsonArray();
        JsonObject goal1 = new JsonObject();
        goal1.addProperty("type", "y_level");
        goal1.addProperty("y", 50);
        goals.add(goal1);

        JsonObject goal2 = new JsonObject();
        goal2.addProperty("type", "block_pos");
        goal2.addProperty("x", 0);
        goal2.addProperty("y", 64);
        goal2.addProperty("z", 0);
        goals.add(goal2);

        params.add("goals", goals);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertTrue(result.getData().get("composite_goal_set").getAsBoolean());
        assertEquals(2, result.getData().get("goal_count").getAsInt());
    }

    @Test
    void testCancelGoal_ActiveGoal() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("action", "cancel");

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertTrue(result.getData().get("goal_cancelled").getAsBoolean());
    }

    @Test
    void testCancelGoal_Chain() throws ExecutionException, InterruptedException {
        // Setup - First create a chain
        JsonObject createParams = new JsonObject();
        createParams.addProperty("action", "chain");
        createParams.addProperty("chain_id", "cancel_test");

        JsonArray steps = new JsonArray();
        JsonObject step = new JsonObject();
        step.addProperty("type", "y_level");
        step.addProperty("y", 30);
        steps.add(step);
        createParams.add("steps", steps);

        handler.handle(createParams, mockClient, mockBaritone, null).get();

        // Now cancel it
        JsonObject cancelParams = new JsonObject();
        cancelParams.addProperty("action", "cancel");
        cancelParams.addProperty("chain_id", "cancel_test");

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(cancelParams, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertTrue(result.getData().get("chain_cancelled").getAsBoolean());
    }

    @Test
    void testGoalStatus_NoActiveGoals() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("action", "status");

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertFalse(result.getData().get("has_active_goal").getAsBoolean());
        assertTrue(result.getData().get("active_chains").getAsJsonArray().size() == 0);
    }

    @Test
    void testInvalidGoalType() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("action", "set");
        params.addProperty("type", "invalid_type");

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertFalse(result.isSuccess());
        assertEquals("Invalid goal parameters", result.getErrorMessage());
    }

    @Test
    void testGoalChainWithoutSteps() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("action", "chain");
        params.addProperty("chain_id", "empty_chain");

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertFalse(result.isSuccess());
        assertEquals("Missing chain_id or steps", result.getErrorMessage());
    }

    @Test
    void testCompositeGoalWithoutGoals() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("action", "composite");
        params.addProperty("operator", "and");

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertFalse(result.isSuccess());
        assertEquals("Missing goals or operator", result.getErrorMessage());
    }
}
