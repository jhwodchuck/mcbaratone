package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import baritone.api.pathing.goals.GoalNear;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.function.Supplier;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.tags.BlockTags;
import net.minecraft.util.Mth;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.monster.zombie.Zombie;
import net.minecraft.world.entity.npc.villager.Villager;
import net.minecraft.world.entity.vehicle.boat.AbstractBoat;
import net.minecraft.world.entity.vehicle.minecart.AbstractMinecart;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.EntityHitResult;
import net.minecraft.world.phys.Vec3;

/**
 * Survival-only vehicle transport for villagers and zombies.
 *
 * <p>This handler never summons, teleports, changes entity coordinates, or
 * calls {@code startRiding}. Capture is accepted only after vanilla collision
 * mechanics make the target a real passenger. Boat transport uses ordinary
 * player interaction plus forward/steering key input.</p>
 */
public class EntityTransportCommandHandler extends AsyncCommandHandler {
    private static final ExecutorService WORKER = Executors.newCachedThreadPool(runnable -> {
        Thread thread = new Thread(runnable, "bridge-entity-transport");
        thread.setDaemon(true);
        return thread;
    });
    private static final int POLL_MS = 100;

    static final class Request {
        final String action;
        final int entityId;
        final String vehicleType;
        final Integer vehicleId;
        final double maxDistance;
        final int timeoutMs;
        final Vec3 destination;
        final double tolerance;
        final int stallTimeoutMs;

        private Request(String action, int entityId, String vehicleType,
                Integer vehicleId, double maxDistance, int timeoutMs,
                Vec3 destination, double tolerance, int stallTimeoutMs) {
            this.action = action;
            this.entityId = entityId;
            this.vehicleType = vehicleType;
            this.vehicleId = vehicleId;
            this.maxDistance = maxDistance;
            this.timeoutMs = timeoutMs;
            this.destination = destination;
            this.tolerance = tolerance;
            this.stallTimeoutMs = stallTimeoutMs;
        }

        static Request parse(JsonObject params) {
            if (params == null || !params.has("entity_id")) {
                throw new IllegalArgumentException("entity_id is required");
            }
            String action = params.has("action")
                ? params.get("action").getAsString().toLowerCase(Locale.ROOT)
                : "status";
            if (!Set.of("status", "capture", "release", "transport").contains(action)) {
                throw new IllegalArgumentException(
                    "action must be status, capture, release, or transport");
            }
            String vehicleType = params.has("vehicle_type")
                ? params.get("vehicle_type").getAsString().toLowerCase(Locale.ROOT)
                : null;
            if (vehicleType != null && !Set.of("boat", "minecart").contains(vehicleType)) {
                throw new IllegalArgumentException("vehicle_type must be boat or minecart");
            }
            Integer vehicleId = params.has("vehicle_id")
                ? params.get("vehicle_id").getAsInt() : null;
            double maxDistance = boundedDouble(params, "max_distance", 6.0, 1.0, 8.0);
            int timeoutLimit = "transport".equals(action) ? 120_000 : 5_000;
            int timeoutMs = clamp(
                params.has("timeout_ms") ? params.get("timeout_ms").getAsInt() : 3_000,
                0, timeoutLimit);
            double tolerance = boundedDouble(params, "tolerance", 2.0, 1.0, 8.0);
            int stallTimeoutMs = clamp(
                params.has("stall_timeout_ms")
                    ? params.get("stall_timeout_ms").getAsInt() : 3_000,
                1_000, 10_000);

            Vec3 destination = null;
            if ("transport".equals(action)) {
                if (!params.has("destination") || !params.get("destination").isJsonObject()) {
                    throw new IllegalArgumentException(
                        "transport requires destination: {x, y, z}");
                }
                JsonObject value = params.getAsJsonObject("destination");
                if (!value.has("x") || !value.has("y") || !value.has("z")) {
                    throw new IllegalArgumentException(
                        "transport destination requires x, y, and z");
                }
                destination = new Vec3(
                    value.get("x").getAsDouble(),
                    value.get("y").getAsDouble(),
                    value.get("z").getAsDouble());
                if (!Double.isFinite(destination.x)
                        || !Double.isFinite(destination.y)
                        || !Double.isFinite(destination.z)) {
                    throw new IllegalArgumentException(
                        "transport destination coordinates must be finite");
                }
            }
            return new Request(action, params.get("entity_id").getAsInt(),
                vehicleType, vehicleId, maxDistance, timeoutMs, destination,
                tolerance, stallTimeoutMs);
        }

        private static double boundedDouble(JsonObject params, String name,
                double defaultValue, double minimum, double maximum) {
            double value = params.has(name)
                ? params.get(name).getAsDouble() : defaultValue;
            if (!Double.isFinite(value)) {
                throw new IllegalArgumentException(name + " must be finite");
            }
            return clamp(value, minimum, maximum);
        }
    }

    private static final class Observation {
        final Entity target;
        final Entity vehicle;
        final JsonObject data;

        Observation(Entity target, Entity vehicle, JsonObject data) {
            this.target = target;
            this.vehicle = vehicle;
            this.data = data;
        }

        boolean captured() {
            return vehicle != null
                && passengerPostcondition(
                    target.getVehicle() == vehicle, vehicle.hasPassenger(target));
        }
    }

    @Override
    public String getCommandName() {
        return "entity_transport";
    }

    @Override
    public CompletableFuture<CommandResult> execute(JsonObject params,
            Minecraft client, IBaritone baritone, Socket clientSocket) {
        final Request request;
        try {
            request = Request.parse(params);
        } catch (RuntimeException exception) {
            return CompletableFuture.completedFuture(
                errorWithVerification(exception.getMessage()));
        }
        return CompletableFuture.supplyAsync(
            () -> run(request, client, baritone), WORKER);
    }

    private CommandResult run(Request request, Minecraft client,
            IBaritone baritone) {
        try {
            return switch (request.action) {
                case "status" -> status(request, client);
                case "capture" -> capture(request, client, baritone);
                case "release" -> release(request, client);
                case "transport" -> transport(request, client);
                default -> CommandResult.error("Unsupported action");
            };
        } catch (Exception exception) {
            releaseControls(client);
            if ("capture".equals(request.action)) {
                cancelPathing(baritone, client);
            }
            String message = "entity_transport failed: " + exception.getMessage();
            try {
                Observation latest = onClient(client, () -> observe(request, client));
                return failure(latest, message);
            } catch (Exception ignored) {
                return errorWithVerification(message);
            }
        }
    }

    private CommandResult status(Request request, Minecraft client) throws Exception {
        Observation observed = onClient(client, () -> observe(request, client));
        if (observed.target == null) {
            return failure(observed, "Target entity was not found");
        }
        return CommandResult.success(observed.data);
    }

    private CommandResult capture(Request request, Minecraft client,
            IBaritone baritone) throws Exception {
        long deadline = System.nanoTime()
            + TimeUnit.MILLISECONDS.toNanos(request.timeoutMs);
        Observation initial = onClient(client, () -> observe(request, client));
        String validation = validateTargetAndRange(initial, request, client);
        if (validation != null) {
            return failure(initial, validation);
        }
        if (initial.captured() && vehicleMatches(initial.vehicle, request.vehicleType)) {
            initial.data.addProperty("capture_observed", true);
            return CommandResult.success(initial.data);
        }

        Integer vehicleId = request.vehicleId;
        if (vehicleId == null) {
            Set<Integer> before = onClient(client,
                () -> nearbyVehicleIds(initial.target, client, 4.0));
            CommandResult placement = onClient(client,
                () -> placeSelectedVehicle(request, initial.target, client));
            if (!placement.isSuccess()) {
                Observation after = onClient(client, () -> observe(request, client));
                return failure(after, placement.getErrorMessage());
            }
            long placementDeadline = Math.min(deadline,
                System.nanoTime() + TimeUnit.SECONDS.toNanos(1));
            vehicleId = waitForPlacedVehicle(
                request, initial.target.getId(), before, client,
                placementDeadline);
            if (vehicleId == null) {
                Observation after = onClient(client, () -> observe(request, client));
                return failure(after,
                    "Vanilla placement did not produce an observed nearby vehicle");
            }
        }

        Request observedRequest = withVehicleId(request, vehicleId);
        CommandResult pushResult = pushTargetTowardVehicle(
            observedRequest, client, baritone, deadline);
        if (pushResult != null) {
            return pushResult;
        }

        Observation latest;
        do {
            latest = onClient(client, () -> observe(observedRequest, client));
            if (latest.captured()) {
                latest.data.addProperty("capture_observed", true);
                return CommandResult.success(latest.data);
            }
            sleep(POLL_MS);
        } while (System.nanoTime() <= deadline);

        latest = onClient(client, () -> observe(observedRequest, client));
        return failure(latest,
            "Target did not mount naturally before timeout; no riding state was forced");
    }

    /**
     * Uses pathing and ordinary player collision to move the target into the
     * vehicle. A non-null result means capture either completed or failed;
     * null means the push ended while time remained for the final observation.
     */
    private CommandResult pushTargetTowardVehicle(Request request,
            Minecraft client, IBaritone baritone, long deadline) throws Exception {
        Observation initial = onClient(client, () -> observe(request, client));
        if (initial.captured()) {
            initial.data.addProperty("capture_observed", true);
            return CommandResult.success(initial.data);
        }
        if (initial.vehicle == null) {
            return failure(initial, "Observed capture vehicle is no longer loaded");
        }
        if (baritone == null) {
            return failure(initial,
                "Baritone is unavailable for the physical capture push stage");
        }

        Vec3 approach = captureApproachPoint(
            initial.target.position(), initial.vehicle.position());
        if (approach == null) {
            return failure(initial,
                "Cannot establish a behind-target approach point separate from the vehicle");
        }
        String safetyFailure = onClient(client,
            () -> approachSafetyFailure(approach, client));
        if (safetyFailure != null) {
            return failure(initial, safetyFailure);
        }

        BlockPos goal = BlockPos.containing(approach);
        onClient(client, () -> {
            baritone.getCustomGoalProcess().setGoalAndPath(new GoalNear(goal, 1));
            return null;
        });
        boolean activeObserved = false;
        long pathStarted = System.nanoTime();
        while (System.nanoTime() <= deadline) {
            Observation latest = onClient(client, () -> observe(request, client));
            if (latest.captured()) {
                cancelPathing(baritone, client);
                latest.data.addProperty("capture_observed", true);
                latest.data.addProperty("push_stage", "path");
                return CommandResult.success(latest.data);
            }
            String validation = validateTargetAndRange(latest, request, client);
            if (validation != null) {
                cancelPathing(baritone, client);
                return failure(latest, validation);
            }
            double distance = onClient(client,
                () -> client.player.position().distanceTo(approach));
            if (distance <= 1.75) {
                break;
            }
            boolean active = onClient(client,
                () -> baritone.getCustomGoalProcess().isActive()
                    || baritone.getPathingBehavior().isPathing());
            activeObserved |= active;
            if (!active && (activeObserved
                    || System.nanoTime() - pathStarted > TimeUnit.SECONDS.toNanos(1))) {
                cancelPathing(baritone, client);
                return failure(latest,
                    "Baritone could not establish a path to the safe behind-target point");
            }
            sleep(POLL_MS);
        }

        cancelPathing(baritone, client);
        if (System.nanoTime() > deadline) {
            Observation latest = onClient(client, () -> observe(request, client));
            return failure(latest,
                "Capture timed out before reaching the behind-target push point");
        }

        long lastProgress = System.nanoTime();
        double bestGap = initial.target.distanceTo(initial.vehicle);
        try {
            while (System.nanoTime() <= deadline) {
                Observation latest = onClient(client, () -> observe(request, client));
                if (latest.captured()) {
                    latest.data.addProperty("capture_observed", true);
                    latest.data.addProperty("push_stage", "forward_collision");
                    return CommandResult.success(latest.data);
                }
                String validation = validateTargetAndRange(latest, request, client);
                if (validation != null) {
                    return failure(latest, validation);
                }
                if (latest.vehicle == null || !latest.vehicle.isAlive()) {
                    return failure(latest,
                        "Capture vehicle was removed during the physical push stage");
                }
                double gap = latest.target.distanceTo(latest.vehicle);
                if (gap < bestGap - 0.05) {
                    bestGap = gap;
                    lastProgress = System.nanoTime();
                } else if (System.nanoTime() - lastProgress
                        >= TimeUnit.MILLISECONDS.toNanos(request.stallTimeoutMs)) {
                    return failure(latest,
                        "Physical capture push stalled or encountered an obstruction");
                }
                onClient(client, () -> {
                    lookAt(client.player, latest.vehicle.getX(),
                        latest.vehicle.getY(), latest.vehicle.getZ());
                    setControls(client, true, false, false);
                    return null;
                });
                sleep(POLL_MS);
            }
        } finally {
            releaseControls(client);
            cancelPathing(baritone, client);
        }
        Observation latest = onClient(client, () -> observe(request, client));
        return failure(latest,
            "Physical capture push timed out before passenger verification");
    }

    private CommandResult release(Request request, Minecraft client) throws Exception {
        Observation latest = onClient(client, () -> observe(request, client));
        String validation = validateTargetAndRange(latest, request, client);
        if (validation != null) {
            return failure(latest, validation);
        }
        if (!latest.captured()) {
            latest.data.addProperty("release_observed", true);
            return CommandResult.success(latest.data);
        }

        int vehicleId = latest.vehicle.getId();
        Request observedRequest = withVehicleId(request, vehicleId);
        long deadline = System.nanoTime()
            + TimeUnit.MILLISECONDS.toNanos(request.timeoutMs);
        for (int attempt = 1; attempt <= 5; attempt++) {
            onClient(client, () -> {
                Entity vehicle = client.level.getEntity(vehicleId);
                if (vehicle != null && client.player != null && client.gameMode != null) {
                    client.gameMode.attack(client.player, vehicle);
                    client.player.swing(InteractionHand.MAIN_HAND);
                }
                return null;
            });
            sleep(250);
            latest = onClient(client, () -> observe(observedRequest, client));
            latest.data.addProperty("release_attacks", attempt);
            if (!latest.captured()) {
                latest.data.addProperty("release_observed", true);
                return CommandResult.success(latest.data);
            }
            if (System.nanoTime() > deadline) {
                break;
            }
        }
        return failure(latest,
            "Vehicle survived bounded vanilla attacks and target remains a passenger");
    }

    private CommandResult transport(Request request, Minecraft client) throws Exception {
        Observation initial = onClient(client, () -> observe(request, client));
        String validation = validateTargetAndRange(initial, request, client);
        if (validation != null) {
            return failure(initial, validation);
        }
        if (!initial.captured()) {
            return failure(initial, "Target is not an observed vehicle passenger");
        }
        if (!(initial.vehicle instanceof AbstractBoat)) {
            initial.data.addProperty("capability_supported", false);
            return failure(initial,
                "Minecart transport requires a powered-rail driver; only boat driving is supported");
        }

        int vehicleId = initial.vehicle.getId();
        Request observedRequest = withVehicleId(request, vehicleId);
        long deadline = System.nanoTime()
            + TimeUnit.MILLISECONDS.toNanos(request.timeoutMs);
        boolean mounted = mountBoat(
            vehicleId, client, request.maxDistance, deadline);
        if (!mounted) {
            Observation latest = onClient(client, () -> observe(observedRequest, client));
            return failure(latest,
                "Player could not mount the occupied boat's available seat by vanilla interaction");
        }

        long lastProgress = System.nanoTime();
        double bestDistance = Double.MAX_VALUE;
        try {
            while (System.nanoTime() <= deadline) {
                Observation latest = onClient(client,
                    () -> observe(observedRequest, client));
                if (!latest.captured()) {
                    return failure(latest,
                        "Target stopped being a passenger during boat transport");
                }
                if (!latest.target.isAlive()) {
                    return failure(latest,
                        "Target died during boat transport");
                }
                DriveState drive = onClient(client,
                    () -> driveStep(vehicleId, request.destination, client));
                if (drive.error != null) {
                    return failure(latest, drive.error);
                }
                latest.data.addProperty("destination_distance", drive.distance);
                if (drive.distance <= request.tolerance) {
                    latest.data.addProperty("transport_observed", true);
                    latest.data.addProperty("capability_supported", true);
                    return CommandResult.success(latest.data);
                }
                if (drive.distance < bestDistance - 0.25) {
                    bestDistance = drive.distance;
                    lastProgress = System.nanoTime();
                } else if (System.nanoTime() - lastProgress
                        >= TimeUnit.MILLISECONDS.toNanos(request.stallTimeoutMs)) {
                    return failure(latest,
                        "Boat transport stalled or encountered an obstruction");
                }
                sleep(POLL_MS);
            }
            Observation latest = onClient(client,
                () -> observe(observedRequest, client));
            return failure(latest, "Boat transport timed out before destination tolerance");
        } finally {
            releaseControls(client);
        }
    }

    private static final class DriveState {
        final double distance;
        final String error;

        DriveState(double distance, String error) {
            this.distance = distance;
            this.error = error;
        }
    }

    private DriveState driveStep(int vehicleId, Vec3 destination,
            Minecraft client) {
        if (client.player == null || client.player.isDeadOrDying()
                || client.player.getHealth() <= 0) {
            setControls(client, false, false, false);
            return new DriveState(Double.MAX_VALUE,
                "Player died or has no health during boat transport");
        }
        Entity entity = client.level == null ? null : client.level.getEntity(vehicleId);
        if (!(entity instanceof AbstractBoat boat)) {
            setControls(client, false, false, false);
            return new DriveState(Double.MAX_VALUE, "Observed boat is no longer loaded");
        }
        if (client.player.getVehicle() != boat
                || boat.getControllingPassenger() != client.player) {
            setControls(client, false, false, false);
            return new DriveState(Double.MAX_VALUE,
                "Player is no longer controlling the observed boat");
        }

        double dx = destination.x - boat.getX();
        double dy = destination.y - boat.getY();
        double dz = destination.z - boat.getZ();
        double distance = Math.sqrt(dx * dx + dy * dy + dz * dz);
        float desiredYaw = (float) (Math.atan2(dz, dx) * (180.0 / Math.PI)) - 90.0f;
        float delta = Mth.wrapDegrees(desiredYaw - boat.getYRot());
        client.player.setYRot(desiredYaw);
        boolean left = delta < -5.0f;
        boolean right = delta > 5.0f;
        boat.setInput(left, right, true, false);
        setControls(client, true, left, right);
        return new DriveState(distance, null);
    }

    private boolean mountBoat(int vehicleId, Minecraft client,
            double maxDistance, long actionDeadline) throws Exception {
        onClient(client, () -> {
            Entity entity = client.level == null ? null : client.level.getEntity(vehicleId);
            if (!(entity instanceof AbstractBoat boat)
                    || client.player == null || client.gameMode == null
                    || client.player.distanceTo(boat) > maxDistance
                    || boat.getPassengers().size() >= 2) {
                return false;
            }
            InteractionResult result = client.gameMode.interact(
                client.player, boat,
                new EntityHitResult(boat, boat.getBoundingBox().getCenter()),
                InteractionHand.MAIN_HAND);
            if (result.consumesAction()) {
                client.player.swing(InteractionHand.MAIN_HAND);
            }
            return result.consumesAction();
        });

        long deadline = Math.min(actionDeadline,
            System.nanoTime() + TimeUnit.SECONDS.toNanos(2));
        while (System.nanoTime() <= deadline) {
            boolean mounted = onClient(client, () -> {
                Entity entity = client.level == null ? null : client.level.getEntity(vehicleId);
                return entity instanceof AbstractBoat boat
                    && client.player != null
                    && client.player.getVehicle() == boat
                    && boat.getControllingPassenger() == client.player;
            });
            if (mounted) {
                return true;
            }
            sleep(POLL_MS);
        }
        return false;
    }

    private CommandResult placeSelectedVehicle(Request request, Entity target,
            Minecraft client) {
        if (client.player == null || client.level == null || client.gameMode == null) {
            return CommandResult.error("Player, world, or interaction manager unavailable");
        }
        String heldItem = BuiltInRegistries.ITEM.getKey(
            client.player.getMainHandItem().getItem()).toString();
        String selectedType = vehicleTypeForItem(heldItem);
        if (selectedType == null) {
            return CommandResult.error(
                "Selected item must be an ordinary boat/raft or minecraft:minecart");
        }
        if (request.vehicleType != null && !request.vehicleType.equals(selectedType)) {
            return CommandResult.error(
                "Selected vehicle item does not match requested vehicle_type");
        }

        InteractionResult result;
        if ("minecart".equals(selectedType)) {
            BlockPos rail = findNearbyRail(target.blockPosition(), client);
            if (rail == null) {
                return CommandResult.error(
                    "Minecart capture requires an existing nearby rail");
            }
            BlockHitResult hit = new BlockHitResult(
                Vec3.atCenterOf(rail), Direction.UP, rail, false);
            result = client.gameMode.useItemOn(
                client.player, InteractionHand.MAIN_HAND, hit);
        } else {
            Vec3 aim = boatPlacementAim(target.position(), client.player.position());
            lookAt(client.player, aim.x, aim.y - 0.25, aim.z);
            result = client.gameMode.useItem(
                client.player, InteractionHand.MAIN_HAND);
        }
        if (result.consumesAction()) {
            client.player.swing(InteractionHand.MAIN_HAND);
            JsonObject data = new JsonObject();
            data.addProperty("placement_accepted", true);
            data.addProperty("held_item", heldItem);
            return CommandResult.success(data);
        }
        return CommandResult.error(
            "Vanilla vehicle placement was rejected: " + result);
    }

    private Integer waitForPlacedVehicle(Request request, int targetId,
            Set<Integer> before, Minecraft client, long deadline) throws Exception {
        do {
            Integer found = onClient(client, () -> {
                Entity target = client.level == null ? null : client.level.getEntity(targetId);
                if (target == null) {
                    return null;
                }
                List<Entity> nearby = client.level.getEntities(
                    target, target.getBoundingBox().inflate(4.0),
                    entity -> isVehicle(entity)
                        && !before.contains(entity.getId())
                        && vehicleMatches(entity, request.vehicleType));
                return nearby.isEmpty() ? null : nearby.getFirst().getId();
            });
            if (found != null) {
                return found;
            }
            sleep(POLL_MS);
        } while (System.nanoTime() <= deadline);
        return null;
    }

    private Observation observe(Request request, Minecraft client) {
        Entity target = client.level == null ? null : client.level.getEntity(request.entityId);
        Entity vehicle = null;
        if (target != null && target.getVehicle() != null
                && isVehicle(target.getVehicle())) {
            vehicle = target.getVehicle();
        }
        if (request.vehicleId != null && client.level != null) {
            Entity requested = client.level.getEntity(request.vehicleId);
            if (isVehicle(requested)) {
                vehicle = requested;
            }
        }

        JsonObject data = new JsonObject();
        data.addProperty("action", request.action);
        data.addProperty("target_entity_id", request.entityId);
        if (target != null) {
            data.addProperty("target_entity_type", entityType(target));
            data.addProperty("target_alive", target.isAlive());
            if (target instanceof LivingEntity livingTarget) {
                data.addProperty("target_health", livingTarget.getHealth());
            }
            if (client.player != null) {
                data.addProperty("target_distance", client.player.distanceTo(target));
            }
        }
        JsonArray passengers = new JsonArray();
        if (vehicle != null) {
            data.addProperty("vehicle_id", vehicle.getId());
            data.addProperty("vehicle_type", vehicleType(vehicle));
            for (Entity passenger : vehicle.getPassengers()) {
                passengers.add(passenger.getId());
            }
        }
        data.add("passenger_ids", passengers);
        boolean captured = target != null && vehicle != null
            && passengerPostcondition(
                target.getVehicle() == vehicle, vehicle.hasPassenger(target));
        data.addProperty("captured", captured);
        data.addProperty("passenger_verified", captured);
        return new Observation(target, vehicle, data);
    }

    static boolean passengerPostcondition(boolean targetReportsVehicle,
            boolean vehicleListsTarget) {
        return targetReportsVehicle && vehicleListsTarget;
    }

    static Vec3 captureApproachPoint(Vec3 target, Vec3 vehicle) {
        double dx = target.x - vehicle.x;
        double dz = target.z - vehicle.z;
        double horizontal = Math.sqrt(dx * dx + dz * dz);
        if (!Double.isFinite(horizontal) || horizontal < 0.25) {
            return null;
        }
        double offset = 1.75;
        return new Vec3(
            target.x + dx / horizontal * offset,
            target.y,
            target.z + dz / horizontal * offset);
    }

    static Vec3 boatPlacementAim(Vec3 target, Vec3 player) {
        double dx = target.x - player.x;
        double dz = target.z - player.z;
        double horizontal = Math.sqrt(dx * dx + dz * dz);
        if (!Double.isFinite(horizontal) || horizontal < 0.25) {
            return target.add(1.75, 0, 0);
        }
        // Aim beside the target so BoatItem's entity ray check does not pass
        // through the villager/zombie and reject an intersecting placement.
        return new Vec3(
            target.x - dz / horizontal * 1.75,
            target.y,
            target.z + dx / horizontal * 1.75);
    }

    private String approachSafetyFailure(Vec3 approach, Minecraft client) {
        if (client.level == null || client.player == null) {
            return "World or player is unavailable for capture path safety checks";
        }
        BlockPos feet = BlockPos.containing(approach);
        BlockPos head = feet.above();
        BlockPos below = feet.below();
        if (!client.level.getWorldBorder().isWithinBounds(feet)) {
            return "Behind-target approach point is outside the world border";
        }
        boolean feetClear = client.level.getBlockState(feet)
            .getCollisionShape(client.level, feet).isEmpty();
        boolean headClear = client.level.getBlockState(head)
            .getCollisionShape(client.level, head).isEmpty();
        boolean supported = !client.level.getBlockState(below)
            .getCollisionShape(client.level, below).isEmpty()
            || !client.level.getFluidState(feet).isEmpty();
        if (!feetClear || !headClear || !supported) {
            return "Behind-target approach point is obstructed or unsupported";
        }
        return null;
    }

    private static void cancelPathing(IBaritone baritone, Minecraft client) {
        if (baritone == null || client == null) {
            return;
        }
        try {
            onClient(client, () -> {
                baritone.getPathingBehavior().cancelEverything();
                return null;
            });
        } catch (Exception ignored) {
            // Best-effort cleanup only; the command's primary result is preserved.
        }
    }

    private String validateTargetAndRange(Observation observed, Request request,
            Minecraft client) {
        if (observed.target == null) {
            return "Target entity was not found";
        }
        if (!(observed.target instanceof Villager)
                && !(observed.target instanceof Zombie)) {
            return "Only villagers and zombies may be transported";
        }
        if (client.player == null || client.player.isDeadOrDying()
                || client.player.getHealth() <= 0) {
            return "Player is unavailable, dead, or has no health";
        }
        if (client.gameMode == null || !client.gameMode.getPlayerMode().isSurvival()) {
            return "entity_transport is available only in survival mode";
        }
        if (!observed.target.isAlive()) {
            return "Target is dead or no longer alive";
        }
        if (client.player.distanceTo(observed.target) > request.maxDistance) {
            return "Target is beyond max_distance";
        }
        if (observed.vehicle != null
                && !vehicleMatches(observed.vehicle, request.vehicleType)) {
            return "Observed vehicle does not match requested vehicle_type";
        }
        return null;
    }

    private CommandResult failure(Observation observed, String message) {
        JsonObject data = observed == null ? new JsonObject() : observed.data;
        data.addProperty("failure_reason", message);
        return new CommandResult(false, data, message);
    }

    private static CommandResult errorWithVerification(String message) {
        JsonObject data = new JsonObject();
        data.addProperty("captured", false);
        data.addProperty("passenger_verified", false);
        data.addProperty("failure_reason", message);
        return new CommandResult(false, data, message);
    }

    private Set<Integer> nearbyVehicleIds(Entity target, Minecraft client,
            double radius) {
        Set<Integer> ids = new HashSet<>();
        AABB bounds = target.getBoundingBox().inflate(radius);
        for (Entity entity : client.level.getEntities(
                target, bounds, EntityTransportCommandHandler::isVehicle)) {
            ids.add(entity.getId());
        }
        return ids;
    }

    private BlockPos findNearbyRail(BlockPos center, Minecraft client) {
        for (int dy = -1; dy <= 1; dy++) {
            for (int dx = -2; dx <= 2; dx++) {
                for (int dz = -2; dz <= 2; dz++) {
                    BlockPos candidate = center.offset(dx, dy, dz);
                    if (client.level.getBlockState(candidate).is(BlockTags.RAILS)) {
                        return candidate;
                    }
                }
            }
        }
        return null;
    }

    private static Request withVehicleId(Request request, int vehicleId) {
        return new Request(request.action, request.entityId, request.vehicleType,
            vehicleId, request.maxDistance, request.timeoutMs,
            request.destination, request.tolerance, request.stallTimeoutMs);
    }

    private static boolean isVehicle(Entity entity) {
        return entity instanceof AbstractBoat || entity instanceof AbstractMinecart;
    }

    private static boolean vehicleMatches(Entity entity, String requested) {
        return requested == null || requested.equals(vehicleType(entity));
    }

    private static String vehicleType(Entity entity) {
        if (entity instanceof AbstractBoat) {
            return "boat";
        }
        if (entity instanceof AbstractMinecart) {
            return "minecart";
        }
        return "unknown";
    }

    static String vehicleTypeForItem(String itemId) {
        if (itemId == null) {
            return null;
        }
        if ("minecraft:minecart".equals(itemId)) {
            return "minecart";
        }
        if (itemId.startsWith("minecraft:")
                && (itemId.endsWith("_boat") || itemId.endsWith("_raft"))
                && !itemId.contains("chest")) {
            return "boat";
        }
        return null;
    }

    private static String entityType(Entity entity) {
        return BuiltInRegistries.ENTITY_TYPE.getKey(entity.getType()).toString();
    }

    private static void lookAt(net.minecraft.world.entity.player.Player player,
            double x, double y, double z) {
        double dx = x - player.getX();
        double dy = y - player.getEyeY();
        double dz = z - player.getZ();
        double horizontal = Math.sqrt(dx * dx + dz * dz);
        player.setYRot((float) (Math.atan2(dz, dx) * (180.0 / Math.PI)) - 90.0f);
        player.setXRot((float) -(Math.atan2(dy, horizontal) * (180.0 / Math.PI)));
    }

    private static void setControls(Minecraft client, boolean forward,
            boolean left, boolean right) {
        if (client == null || client.options == null) {
            return;
        }
        client.options.keyUp.setDown(forward);
        client.options.keyLeft.setDown(left);
        client.options.keyRight.setDown(right);
        client.options.keyDown.setDown(false);
        if (!forward && client.player != null
                && client.player.getVehicle() instanceof AbstractBoat boat) {
            boat.setInput(false, false, false, false);
        }
    }

    private static void releaseControls(Minecraft client) {
        if (client == null) {
            return;
        }
        try {
            client.submit(() -> {
                setControls(client, false, false, false);
                return null;
            }).get(2, TimeUnit.SECONDS);
        } catch (Exception ignored) {
            client.execute(() -> setControls(client, false, false, false));
        }
    }

    private static <T> T onClient(Minecraft client, Supplier<T> supplier)
            throws Exception {
        return client.submit(supplier::get).get(5, TimeUnit.SECONDS);
    }

    private static void sleep(int milliseconds) throws InterruptedException {
        Thread.sleep(milliseconds);
    }

    private static int clamp(int value, int minimum, int maximum) {
        return Math.max(minimum, Math.min(maximum, value));
    }

    private static double clamp(double value, double minimum, double maximum) {
        return Math.max(minimum, Math.min(maximum, value));
    }
}
