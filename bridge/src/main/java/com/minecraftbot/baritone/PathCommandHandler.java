package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import baritone.api.pathing.goals.GoalBlock;
import baritone.api.pathing.goals.GoalNear;
import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.io.File;
import java.io.FileReader;
import java.io.FileWriter;
import java.io.IOException;
import java.net.Socket;
import java.util.*;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;

/**
 * Advanced pathfinding handler with waypoint support, path optimization, and persistence.
 * Supports waypoint chains, multi-destination routing, and dynamic replanning.
 */
public class PathCommandHandler extends AbstractCommandHandler {

    private static final String PATHS_DIR = "paths";
    private static final Gson GSON = new Gson();

    // In-memory path state storage (pathName -> PathState)
    private static final Map<String, PathState> activePaths = new HashMap<>();

    // Test helper method
    static void clearActivePaths() {
        activePaths.clear();
    }

    @Override
    public String getCommandName() {
        return "path";
    }

    @Override
    protected CommandResult execute(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        String action = params.has("action") ? params.get("action").getAsString() : "navigate";

        switch (action) {
            case "navigate":
                return handleNavigate(params, baritone);
            case "save":
                return handleSave(params);
            case "load":
                return handleLoad(params, baritone);
            case "list":
                return handleList();
            case "status":
                return handleStatus(params, baritone);
            case "cancel":
                return handleCancel(params);
            default:
                return CommandResult.error("Unknown path action: " + action);
        }
    }

    private CommandResult handleNavigate(JsonObject params, IBaritone baritone) {
        List<Waypoint> waypoints = parseWaypoints(params);
        if (waypoints.isEmpty()) {
            return CommandResult.error("No waypoints provided");
        }

        String pathName = params.has("path_name") ? params.get("path_name").getAsString() : "temp_" + System.currentTimeMillis();
        boolean optimize = params.has("optimize") ? params.get("optimize").getAsBoolean() : false;
        boolean replanOnBlock = params.has("replan_on_block") ? params.get("replan_on_block").getAsBoolean() : true;

        // Optimize waypoints if requested
        if (optimize && waypoints.size() > 2) {
            waypoints = optimizeWaypoints(waypoints, baritone);
        }

        // Create path state
        PathState pathState = new PathState(pathName, waypoints, 0, replanOnBlock);
        activePaths.put(pathName, pathState);

        // Start navigation to first waypoint
        return navigateToNextWaypoint(pathState, baritone);
    }

    private CommandResult handleSave(JsonObject params) {
        if (!params.has("path_name") || !params.has("waypoints")) {
            return CommandResult.error("path_name and waypoints required for save");
        }

        String pathName = params.get("path_name").getAsString();
        List<Waypoint> waypoints = parseWaypoints(params);

        try {
            ensurePathsDir();
            File pathFile = new File(PATHS_DIR, pathName + ".json");

            JsonObject pathData = new JsonObject();
            pathData.addProperty("name", pathName);
            pathData.addProperty("created", System.currentTimeMillis());
            JsonArray waypointArray = new JsonArray();
            for (Waypoint wp : waypoints) {
                JsonObject wpObj = new JsonObject();
                wpObj.addProperty("x", wp.x);
                wpObj.addProperty("y", wp.y);
                wpObj.addProperty("z", wp.z);
                wpObj.addProperty("radius", wp.radius);
                waypointArray.add(wpObj);
            }
            pathData.add("waypoints", waypointArray);

            try (FileWriter writer = new FileWriter(pathFile)) {
                GSON.toJson(pathData, writer);
            }

            JsonObject data = new JsonObject();
            data.addProperty("saved", true);
            data.addProperty("path_name", pathName);
            data.addProperty("waypoint_count", waypoints.size());
            return CommandResult.success(data);

        } catch (IOException e) {
            logger.error("Failed to save path: " + pathName, e);
            return CommandResult.error("Failed to save path: " + e.getMessage());
        }
    }

    private CommandResult handleLoad(JsonObject params, IBaritone baritone) {
        if (!params.has("path_name")) {
            return CommandResult.error("path_name required for load");
        }

        String pathName = params.get("path_name").getAsString();

        try {
            File pathFile = new File(PATHS_DIR, pathName + ".json");
            if (!pathFile.exists()) {
                return CommandResult.error("Path not found: " + pathName);
            }

            try (FileReader reader = new FileReader(pathFile)) {
                JsonObject pathData = GSON.fromJson(reader, JsonObject.class);
                JsonArray waypointArray = pathData.getAsJsonArray("waypoints");

                List<Waypoint> waypoints = new ArrayList<>();
                for (JsonElement elem : waypointArray) {
                    JsonObject wpObj = elem.getAsJsonObject();
                    waypoints.add(new Waypoint(
                        wpObj.get("x").getAsInt(),
                        wpObj.get("y").getAsInt(),
                        wpObj.get("z").getAsInt(),
                        wpObj.has("radius") ? wpObj.get("radius").getAsInt() : 0
                    ));
                }

                boolean optimize = params.has("optimize") ? params.get("optimize").getAsBoolean() : false;
                if (optimize && waypoints.size() > 2) {
                    waypoints = optimizeWaypoints(waypoints, baritone);
                }

                boolean replanOnBlock = params.has("replan_on_block") ? params.get("replan_on_block").getAsBoolean() : true;
                PathState pathState = new PathState(pathName, waypoints, 0, replanOnBlock);
                activePaths.put(pathName, pathState);

                CommandResult result = navigateToNextWaypoint(pathState, baritone);
                result.getData().addProperty("loaded", true);
                return result;
            }

        } catch (Exception e) {
            logger.error("Failed to load path: " + pathName, e);
            return CommandResult.error("Failed to load path: " + e.getMessage());
        }
    }

    private CommandResult handleList() {
        ensurePathsDir();
        File dir = new File(PATHS_DIR);
        File[] files = dir.listFiles((d, name) -> name.endsWith(".json"));

        JsonArray paths = new JsonArray();
        if (files != null) {
            for (File file : files) {
                String name = file.getName().replace(".json", "");
                paths.add(name);
            }
        }

        JsonObject data = new JsonObject();
        data.add("paths", paths);
        data.addProperty("count", paths.size());
        return CommandResult.success(data);
    }

    private CommandResult handleStatus(JsonObject params, IBaritone baritone) {
        String pathName = params.has("path_name") ? params.get("path_name").getAsString() : null;

        // Check all active paths for completion and advance if needed
        List<String> completedPaths = new ArrayList<>();
        for (Map.Entry<String, PathState> entry : activePaths.entrySet()) {
            PathState state = entry.getValue();
            if (checkAndAdvanceWaypoint(state, baritone)) {
                if (state.currentIndex >= state.waypoints.size()) {
                    completedPaths.add(entry.getKey());
                }
            }
        }
        // Remove completed paths
        for (String completed : completedPaths) {
            activePaths.remove(completed);
        }

        JsonObject data = new JsonObject();
        if (pathName != null && activePaths.containsKey(pathName)) {
            PathState state = activePaths.get(pathName);
            data.addProperty("path_name", pathName);
            data.addProperty("current_waypoint", state.currentIndex);
            data.addProperty("total_waypoints", state.waypoints.size());
            data.addProperty("completed", state.currentIndex >= state.waypoints.size());
            data.addProperty("replan_on_block", state.replanOnBlock);

            if (state.currentIndex < state.waypoints.size()) {
                Waypoint currentWp = state.waypoints.get(state.currentIndex);
                data.addProperty("current_x", currentWp.x);
                data.addProperty("current_y", currentWp.y);
                data.addProperty("current_z", currentWp.z);
                data.addProperty("current_radius", currentWp.radius);
            }
        } else {
            JsonArray activePathNames = new JsonArray();
            for (String name : activePaths.keySet()) {
                activePathNames.add(name);
            }
            data.add("active_paths", activePathNames);
            data.addProperty("checked_completed", completedPaths.size());
        }

        return CommandResult.success(data);
    }

    private boolean checkAndAdvanceWaypoint(PathState pathState, IBaritone baritone) {
        // Check if we're not currently pathing, which might indicate we reached the goal
        if (!baritone.getPathingBehavior().isPathing() && baritone.getCustomGoalProcess().getGoal() == null) {
            // Goal was reached, advance to next waypoint
            pathState.currentIndex++;
            if (pathState.currentIndex < pathState.waypoints.size()) {
                // Start navigation to next waypoint
                navigateToNextWaypoint(pathState, baritone);
                return true;
            }
        }
        return false;
    }

    private CommandResult handleCancel(JsonObject params) {
        String pathName = params.has("path_name") ? params.get("path_name").getAsString() : null;

        if (pathName != null) {
            activePaths.remove(pathName);
            JsonObject data = new JsonObject();
            data.addProperty("cancelled", true);
            data.addProperty("path_name", pathName);
            return CommandResult.success(data);
        } else {
            int count = activePaths.size();
            activePaths.clear();
            JsonObject data = new JsonObject();
            data.addProperty("cancelled_all", true);
            data.addProperty("count", count);
            return CommandResult.success(data);
        }
    }

    private List<Waypoint> parseWaypoints(JsonObject params) {
        List<Waypoint> waypoints = new ArrayList<>();

        if (params.has("waypoints")) {
            JsonArray waypointArray = params.getAsJsonArray("waypoints");
            for (JsonElement elem : waypointArray) {
                JsonObject wp = elem.getAsJsonObject();
                if (wp.has("x") && wp.has("y") && wp.has("z")) {
                    int x = wp.get("x").getAsInt();
                    int y = wp.get("y").getAsInt();
                    int z = wp.get("z").getAsInt();
                    int radius = wp.has("radius") ? wp.get("radius").getAsInt() : 0;
                    waypoints.add(new Waypoint(x, y, z, radius));
                }
            }
        } else if (params.has("x") && params.has("y") && params.has("z")) {
            // Single waypoint
            int x = params.get("x").getAsInt();
            int y = params.get("y").getAsInt();
            int z = params.get("z").getAsInt();
            int radius = params.has("radius") ? params.get("radius").getAsInt() : 0;
            waypoints.add(new Waypoint(x, y, z, radius));
        }

        return waypoints;
    }

    private List<Waypoint> optimizeWaypoints(List<Waypoint> waypoints, IBaritone baritone) {
        // Simple nearest neighbor optimization for waypoint ordering
        if (waypoints.size() <= 2) return waypoints;

        List<Waypoint> optimized = new ArrayList<>();
        List<Waypoint> remaining = new ArrayList<>(waypoints);

        // Start from first waypoint
        Waypoint current = remaining.remove(0);
        optimized.add(current);

        while (!remaining.isEmpty()) {
            Waypoint nearest = null;
            double minDistance = Double.MAX_VALUE;

            for (Waypoint wp : remaining) {
                double distance = current.distanceTo(wp);
                if (distance < minDistance) {
                    minDistance = distance;
                    nearest = wp;
                }
            }

            if (nearest != null) {
                optimized.add(nearest);
                remaining.remove(nearest);
                current = nearest;
            }
        }

        return optimized;
    }

    private CommandResult navigateToNextWaypoint(PathState pathState, IBaritone baritone) {
        if (pathState.currentIndex >= pathState.waypoints.size()) {
            // Path completed
            activePaths.remove(pathState.pathName);
            JsonObject data = new JsonObject();
            data.addProperty("completed", true);
            data.addProperty("path_name", pathState.pathName);
            return CommandResult.success(data);
        }

        Waypoint waypoint = pathState.waypoints.get(pathState.currentIndex);

        executeOnMainThread(Minecraft.getInstance(), () -> {
            BlockPos targetPos = new BlockPos(waypoint.x, waypoint.y, waypoint.z);
            if (waypoint.radius > 0) {
                baritone.getCustomGoalProcess().setGoalAndPath(new GoalNear(targetPos, waypoint.radius));
            } else {
                baritone.getCustomGoalProcess().setGoalAndPath(new GoalBlock(waypoint.x, waypoint.y, waypoint.z));
            }
        });

        JsonObject data = new JsonObject();
        data.addProperty("navigating", true);
        data.addProperty("path_name", pathState.pathName);
        data.addProperty("current_waypoint", pathState.currentIndex);
        data.addProperty("total_waypoints", pathState.waypoints.size());
        data.addProperty("waypoint_x", waypoint.x);
        data.addProperty("waypoint_y", waypoint.y);
        data.addProperty("waypoint_z", waypoint.z);
        data.addProperty("waypoint_radius", waypoint.radius);

        return CommandResult.success(data);
    }

    private void ensurePathsDir() {
        File dir = new File(PATHS_DIR);
        if (!dir.exists()) {
            dir.mkdirs();
        }
    }

    // Inner classes for data structures
    private static class Waypoint {
        final int x, y, z, radius;

        Waypoint(int x, int y, int z, int radius) {
            this.x = x;
            this.y = y;
            this.z = z;
            this.radius = radius;
        }

        double distanceTo(Waypoint other) {
            int dx = x - other.x;
            int dy = y - other.y;
            int dz = z - other.z;
            return Math.sqrt(dx*dx + dy*dy + dz*dz);
        }
    }

    private static class PathState {
        final String pathName;
        final List<Waypoint> waypoints;
        int currentIndex;
        final boolean replanOnBlock;

        PathState(String pathName, List<Waypoint> waypoints, int currentIndex, boolean replanOnBlock) {
            this.pathName = pathName;
            this.waypoints = waypoints;
            this.currentIndex = currentIndex;
            this.replanOnBlock = replanOnBlock;
        }
    }
}
