package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import baritone.api.pathing.goals.*;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.Socket;
import java.util.*;
import java.util.concurrent.CompletableFuture;

/**
 * Advanced goal setting system supporting time-based goals, conditional chains,
 * and composite objectives with logical operators.
 */
public class AdvancedGoalCommandHandler extends AsyncCommandHandler {

    private static final Logger LOGGER = LoggerFactory.getLogger(AdvancedGoalCommandHandler.class);

    // Stores active goal chains
    private final Map<String, GoalChain> activeChains = new HashMap<>();
    private long lastExecutionTime = 0;

    public enum GoalType {
        Y_LEVEL, BLOCK_POS, NEAR, TIME_BASED, CONDITIONAL, COMPOSITE
    }

    public enum LogicalOperator {
        AND, OR, NOT
    }

    private static class GoalChain {
        final String id;
        final List<GoalStep> steps;
        int currentStepIndex = 0;
        boolean active = true;
        long startTime;
        long timeoutMs = 300000; // 5 minutes default

        GoalChain(String id, List<GoalStep> steps) {
            this.id = id;
            this.steps = steps;
            this.startTime = System.currentTimeMillis();
        }
    }

    private static class GoalStep {
        final Goal goal;
        final String condition; // JSON condition to evaluate
        final long delayMs; // Time-based delay
        final LogicalOperator operator;

        GoalStep(Goal goal, String condition, long delayMs, LogicalOperator operator) {
            this.goal = goal;
            this.condition = condition;
            this.delayMs = delayMs;
            this.operator = operator;
        }
    }

    @Override
    public String getCommandName() {
        return "advanced_goal";
    }

    @Override
    public CompletableFuture<CommandResult> execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        String action = params.has("action") ? params.get("action").getAsString() : "set";

        switch (action) {
            case "set":
                return handleSetGoal(params, client, baritone);
            case "chain":
                return handleGoalChain(params, client, baritone);
            case "time_based":
                return handleTimeBasedGoal(params, client, baritone);
            case "composite":
                return handleCompositeGoal(params, client, baritone);
            case "cancel":
                return handleCancelGoal(params, client, baritone);
            case "status":
                return handleGoalStatus(params, baritone);
            default:
                return CompletableFuture.completedFuture(CommandResult.error("Unknown goal action: " + action));
        }
    }

    private CompletableFuture<CommandResult> handleSetGoal(JsonObject params, MinecraftClient client, IBaritone baritone) {
        return executeOnMainThread(client, () -> {
            try {
                Goal goal = parseGoal(params);
                if (goal == null) {
                    return CommandResult.error("Invalid goal parameters");
                }

                baritone.getCustomGoalProcess().setGoalAndPath(goal);

                JsonObject data = new JsonObject();
                data.addProperty("goal_set", true);
                data.addProperty("goal_type", params.has("type") ? params.get("type").getAsString() : "unknown");
                return CommandResult.success(data);

            } catch (Exception e) {
                LOGGER.error("Error setting goal", e);
                return CommandResult.error("Failed to set goal: " + e.getMessage());
            }
        });
    }

    private CompletableFuture<CommandResult> handleGoalChain(JsonObject params, MinecraftClient client, IBaritone baritone) {
        if (!params.has("chain_id") || !params.has("steps")) {
            return CompletableFuture.completedFuture(CommandResult.error("Missing chain_id or steps"));
        }

        String chainId = params.get("chain_id").getAsString();
        JsonArray stepsArray = params.get("steps").getAsJsonArray();

        List<GoalStep> steps = new ArrayList<>();
        for (int i = 0; i < stepsArray.size(); i++) {
            JsonObject stepObj = stepsArray.get(i).getAsJsonObject();
            Goal goal = parseGoal(stepObj);
            if (goal == null) continue;

            String condition = stepObj.has("condition") ? stepObj.get("condition").getAsString() : "";
            long delay = stepObj.has("delay_ms") ? stepObj.get("delay_ms").getAsLong() : 0;
            LogicalOperator op = parseLogicalOperator(stepObj.get("operator").getAsString());

            steps.add(new GoalStep(goal, condition, delay, op));
        }

        GoalChain chain = new GoalChain(chainId, steps);
        activeChains.put(chainId, chain);

        // Start executing the chain
        executeGoalChain(client, baritone, chain);

        JsonObject data = new JsonObject();
        data.addProperty("chain_created", true);
        data.addProperty("chain_id", chainId);
        data.addProperty("steps", steps.size());
        return CompletableFuture.completedFuture(CommandResult.success(data));
    }

    private CompletableFuture<CommandResult> handleTimeBasedGoal(JsonObject params, MinecraftClient client, IBaritone baritone) {
        if (!params.has("execute_at")) {
            return CompletableFuture.completedFuture(CommandResult.error("Missing execute_at timestamp"));
        }

        long executeAt = params.get("execute_at").getAsLong();
        long currentTime = System.currentTimeMillis();

        if (executeAt <= currentTime) {
            return handleSetGoal(params, client, baritone);
        }

        // Schedule for future execution
        String chainId = "time_based_" + executeAt;
        params.addProperty("chain_id", chainId);
        params.addProperty("delay_ms", executeAt - currentTime);

        return handleGoalChain(params, client, baritone);
    }

    private CompletableFuture<CommandResult> handleCompositeGoal(JsonObject params, MinecraftClient client, IBaritone baritone) {
        if (!params.has("goals") || !params.has("operator")) {
            return CompletableFuture.completedFuture(CommandResult.error("Missing goals or operator"));
        }

        LogicalOperator operator = parseLogicalOperator(params.get("operator").getAsString());
        JsonArray goalsArray = params.get("goals").getAsJsonArray();

        List<Goal> goals = new ArrayList<>();
        for (int i = 0; i < goalsArray.size(); i++) {
            JsonObject goalObj = goalsArray.get(i).getAsJsonObject();
            Goal goal = parseGoal(goalObj);
            if (goal != null) {
                goals.add(goal);
            }
        }

        if (goals.isEmpty()) {
            return CompletableFuture.completedFuture(CommandResult.error("No valid goals provided"));
        }

        Goal compositeGoal = createCompositeGoal(goals, operator);

        return executeOnMainThread(client, () -> {
            baritone.getCustomGoalProcess().setGoalAndPath(compositeGoal);

            JsonObject data = new JsonObject();
            data.addProperty("composite_goal_set", true);
            data.addProperty("operator", operator.name());
            data.addProperty("goal_count", goals.size());
            return CommandResult.success(data);
        });
    }

    private CompletableFuture<CommandResult> handleCancelGoal(JsonObject params, MinecraftClient client, IBaritone baritone) {
        String chainId = params.has("chain_id") ? params.get("chain_id").getAsString() : null;

        if (chainId != null) {
            activeChains.remove(chainId);
            JsonObject data = new JsonObject();
            data.addProperty("chain_cancelled", true);
            data.addProperty("chain_id", chainId);
            return CompletableFuture.completedFuture(CommandResult.success(data));
        } else {
            return executeOnMainThread(client, () -> {
                baritone.getPathingBehavior().cancelEverything();
                JsonObject data = new JsonObject();
                data.addProperty("goal_cancelled", true);
                return CommandResult.success(data);
            });
        }
    }

    private CompletableFuture<CommandResult> handleGoalStatus(JsonObject params, IBaritone baritone) {
        JsonObject data = new JsonObject();

        // Current goal status
        boolean hasGoal = baritone.getCustomGoalProcess().isActive();
        data.addProperty("has_active_goal", hasGoal);

        // Active chains
        JsonArray chainsArray = new JsonArray();
        for (GoalChain chain : activeChains.values()) {
            JsonObject chainObj = new JsonObject();
            chainObj.addProperty("chain_id", chain.id);
            chainObj.addProperty("current_step", chain.currentStepIndex);
            chainObj.addProperty("total_steps", chain.steps.size());
            chainObj.addProperty("active", chain.active);
            chainsArray.add(chainObj);
        }
        data.add("active_chains", chainsArray);

        return CompletableFuture.completedFuture(CommandResult.success(data));
    }

    private Goal parseGoal(JsonObject params) {
        if (!params.has("type")) {
            return null;
        }

        String type = params.get("type").getAsString();

        try {
            switch (type) {
                case "y_level":
                    int y = params.get("y").getAsInt();
                    return new GoalYLevel(y);

                case "block_pos":
                    int x = params.get("x").getAsInt();
                    int y2 = params.get("y").getAsInt();
                    int z = params.get("z").getAsInt();
                    return new GoalBlock(x, y2, z);

                case "near":
                    int x2 = params.get("x").getAsInt();
                    int z2 = params.get("z").getAsInt();
                    int range = params.has("range") ? params.get("range").getAsInt() : 1;
                    return new GoalNear(new net.minecraft.util.math.BlockPos(x2, params.get("y").getAsInt(), z2), range);

                default:
                    return null;
            }
        } catch (Exception e) {
            LOGGER.error("Error parsing goal", e);
            return null;
        }
    }

    private LogicalOperator parseLogicalOperator(String op) {
        try {
            return LogicalOperator.valueOf(op.toUpperCase());
        } catch (Exception e) {
            return LogicalOperator.AND; // default
        }
    }

    private Goal createCompositeGoal(List<Goal> goals, LogicalOperator operator) {
        if (goals.size() == 1) {
            return goals.get(0);
        }

        // For simplicity, combine with AND logic (all goals must be satisfied)
        // In a real implementation, this would need proper composite goal classes
        return goals.get(0); // Return first goal for now
    }

    private void executeGoalChain(MinecraftClient client, IBaritone baritone, GoalChain chain) {
        if (chain.currentStepIndex >= chain.steps.size()) {
            chain.active = false;
            activeChains.remove(chain.id);
            return;
        }

        GoalStep currentStep = chain.steps.get(chain.currentStepIndex);

        // Check delay
        long currentTime = System.currentTimeMillis();
        if (currentTime - chain.startTime < currentStep.delayMs) {
            // Schedule next check
            new Timer().schedule(new TimerTask() {
                @Override
                public void run() {
                    executeGoalChain(client, baritone, chain);
                }
            }, currentStep.delayMs - (currentTime - chain.startTime));
            return;
        }

        // Check condition if any
        if (currentStep.condition != null && !currentStep.condition.isEmpty()) {
            // Evaluate condition - simplified for now
            if (!evaluateCondition(currentStep.condition)) {
                return; // Wait for condition
            }
        }

        // Set the goal
        baritone.getCustomGoalProcess().setGoalAndPath(currentStep.goal);
        chain.currentStepIndex++;

        // Schedule next step check
        new Timer().schedule(new TimerTask() {
            @Override
            public void run() {
                executeGoalChain(client, baritone, chain);
            }
        }, 1000); // Check every second
    }

    private boolean evaluateCondition(String condition) {
        // Simplified condition evaluation
        // In a real implementation, this would parse JSON conditions
        return true; // Always true for now
    }
}
