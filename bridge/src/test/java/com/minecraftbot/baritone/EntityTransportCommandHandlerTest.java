package com.minecraftbot.baritone;

import com.google.gson.JsonObject;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import net.minecraft.world.phys.Vec3;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class EntityTransportCommandHandlerTest {
    @Test
    void exposesRegisteredCommandName() {
        EntityTransportCommandHandler handler = new EntityTransportCommandHandler();

        assertEquals("entity_transport", handler.getCommandName());
    }

    @Test
    void parsesStatusDefaults() {
        JsonObject params = new JsonObject();
        params.addProperty("entity_id", 42);

        EntityTransportCommandHandler.Request request =
            EntityTransportCommandHandler.Request.parse(params);

        assertEquals("status", request.action);
        assertEquals(42, request.entityId);
        assertEquals(6.0, request.maxDistance);
        assertEquals(3_000, request.timeoutMs);
    }

    @Test
    void parsesEveryRegisteredActionRoute() {
        assertEquals("status", EntityTransportCommandHandler.Request.parse(
            request("status")).action);
        assertEquals("capture", EntityTransportCommandHandler.Request.parse(
            request("capture")).action);
        assertEquals("release", EntityTransportCommandHandler.Request.parse(
            request("release")).action);

        JsonObject transport = request("transport");
        JsonObject destination = new JsonObject();
        destination.addProperty("x", 1);
        destination.addProperty("y", 64);
        destination.addProperty("z", 2);
        transport.add("destination", destination);
        assertEquals("transport", EntityTransportCommandHandler.Request.parse(
            transport).action);
    }

    @Test
    void clampsCaptureBounds() {
        JsonObject params = request("capture");
        params.addProperty("max_distance", 100);
        params.addProperty("timeout_ms", 100_000);

        EntityTransportCommandHandler.Request request =
            EntityTransportCommandHandler.Request.parse(params);

        assertEquals(8.0, request.maxDistance);
        assertEquals(5_000, request.timeoutMs);
    }

    @Test
    void parsesBoundedTransportDestination() {
        JsonObject params = request("transport");
        JsonObject destination = new JsonObject();
        destination.addProperty("x", 1.5);
        destination.addProperty("y", 64);
        destination.addProperty("z", -8.25);
        params.add("destination", destination);
        params.addProperty("timeout_ms", 500_000);
        params.addProperty("tolerance", 0.1);
        params.addProperty("stall_timeout_ms", 50_000);

        EntityTransportCommandHandler.Request request =
            EntityTransportCommandHandler.Request.parse(params);

        assertEquals(120_000, request.timeoutMs);
        assertEquals(1.0, request.tolerance);
        assertEquals(10_000, request.stallTimeoutMs);
        assertEquals(-8.25, request.destination.z);
    }

    @Test
    void rejectsTransportWithoutDestination() {
        JsonObject params = request("transport");

        assertThrows(IllegalArgumentException.class,
            () -> EntityTransportCommandHandler.Request.parse(params));
    }

    @Test
    void validationErrorsCarryPassengerVerificationContract() {
        CommandResult result = new EntityTransportCommandHandler()
            .execute(new JsonObject(), null, null, null).join();

        assertFalse(result.isSuccess());
        assertFalse(result.getData().get("captured").getAsBoolean());
        assertFalse(result.getData().get("passenger_verified").getAsBoolean());
    }

    @Test
    void rejectsInvalidActionAndVehicleType() {
        JsonObject action = request("teleport");
        JsonObject vehicle = request("capture");
        vehicle.addProperty("vehicle_type", "horse");

        assertThrows(IllegalArgumentException.class,
            () -> EntityTransportCommandHandler.Request.parse(action));
        assertThrows(IllegalArgumentException.class,
            () -> EntityTransportCommandHandler.Request.parse(vehicle));
    }

    @Test
    void rejectsNonFiniteBoundsAndDestination() {
        JsonObject bounds = request("capture");
        bounds.addProperty("max_distance", Double.NaN);
        JsonObject transport = request("transport");
        JsonObject destination = new JsonObject();
        destination.addProperty("x", Double.POSITIVE_INFINITY);
        destination.addProperty("y", 64);
        destination.addProperty("z", 0);
        transport.add("destination", destination);

        assertThrows(IllegalArgumentException.class,
            () -> EntityTransportCommandHandler.Request.parse(bounds));
        assertThrows(IllegalArgumentException.class,
            () -> EntityTransportCommandHandler.Request.parse(transport));
    }

    @Test
    void acceptsOnlyOrdinarySupportedVehicleItems() {
        assertEquals("boat", EntityTransportCommandHandler.vehicleTypeForItem(
            "minecraft:oak_boat"));
        assertEquals("boat", EntityTransportCommandHandler.vehicleTypeForItem(
            "minecraft:bamboo_raft"));
        assertEquals("minecart", EntityTransportCommandHandler.vehicleTypeForItem(
            "minecraft:minecart"));
        assertNull(EntityTransportCommandHandler.vehicleTypeForItem(
            "minecraft:oak_chest_boat"));
        assertNull(EntityTransportCommandHandler.vehicleTypeForItem(
            "minecraft:chest_minecart"));
        assertNull(EntityTransportCommandHandler.vehicleTypeForItem(
            "minecraft:rail"));
    }

    @Test
    void passengerVerificationRequiresBothLiveSignals() {
        assertTrue(EntityTransportCommandHandler.passengerPostcondition(true, true));
        assertFalse(EntityTransportCommandHandler.passengerPostcondition(true, false));
        assertFalse(EntityTransportCommandHandler.passengerPostcondition(false, true));
        assertFalse(EntityTransportCommandHandler.passengerPostcondition(false, false));
    }

    @Test
    void pushApproachIsBehindTargetOppositeVehicle() {
        Vec3 target = new Vec3(10, 64, 10);
        Vec3 vehicle = new Vec3(12, 64, 10);

        Vec3 approach = EntityTransportCommandHandler.captureApproachPoint(
            target, vehicle);

        assertEquals(8.25, approach.x);
        assertEquals(64, approach.y);
        assertEquals(10, approach.z);
        assertNull(EntityTransportCommandHandler.captureApproachPoint(
            target, target));
    }

    @Test
    void boatPlacementAimIsAdjacentInsteadOfThroughTarget() {
        Vec3 target = new Vec3(10, 64, 10);
        Vec3 player = new Vec3(10, 64, 5);

        Vec3 aim = EntityTransportCommandHandler.boatPlacementAim(target, player);

        assertEquals(8.25, aim.x);
        assertEquals(64, aim.y);
        assertEquals(10, aim.z);
        assertEquals(1.75, aim.distanceTo(target));
    }

    @Test
    void implementationDoesNotInvokeForbiddenMutationApis() throws IOException {
        String source = Files.readString(Path.of(
            "src/main/java/com/minecraftbot/baritone/EntityTransportCommandHandler.java"));

        assertFalse(source.matches("(?s).*\\.startRiding\\s*\\(.*"));
        assertFalse(source.matches("(?s).*\\.setPos\\s*\\(.*"));
        assertFalse(source.matches("(?s).*\\.teleportTo\\s*\\(.*"));
        assertFalse(source.matches("(?s).*\\.addFreshEntity\\s*\\(.*"));
    }

    @Test
    void handlerClassCanBeRegistered() {
        CommandHandlerFactory.clearRegistry();
        CommandHandlerFactory.registerHandler(
            "entity_transport", EntityTransportCommandHandler.class);

        assertInstanceOf(EntityTransportCommandHandler.class,
            CommandHandlerFactory.getHandler("entity_transport"));
        CommandHandlerFactory.clearRegistry();
    }

    private static JsonObject request(String action) {
        JsonObject params = new JsonObject();
        params.addProperty("action", action);
        params.addProperty("entity_id", 42);
        return params;
    }
}
