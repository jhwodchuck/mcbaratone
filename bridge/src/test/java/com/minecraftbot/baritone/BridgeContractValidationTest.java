package com.minecraftbot.baritone;

import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class BridgeContractValidationTest {

    @Test
    void coordinateValidatorAcceptsNestedPosition() {
        JsonObject position = new JsonObject();
        position.addProperty("x", 10);
        position.addProperty("y", 64);
        position.addProperty("z", -3);
        JsonObject params = new JsonObject();
        params.add("position", position);

        assertTrue(ParameterValidator.coordinates()
            .validate(params, "coordinates").isValid());
    }

    @Test
    void farmRejectsUnsupportedCropFilterBeforeExecution() throws Exception {
        JsonObject params = new JsonObject();
        params.addProperty("crop", "minecraft:wheat");

        CommandResult result = new FarmCommandHandler()
            .execute(params, null, null, null).get();

        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("crop filtering"));
    }

    @Test
    void biomeScanRejectsUnboundedRadiusBeforeExecution() throws Exception {
        JsonObject params = new JsonObject();
        params.addProperty("radius", 513);

        CommandResult result = new BiomeScanCommandHandler()
            .execute(params, null, null, null).get();

        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("between 0 and 512"));
    }

    @Test
    void useItemRejectsNegativeDurationBeforeExecution() throws Exception {
        JsonObject params = new JsonObject();
        params.addProperty("duration_ms", -1);

        CommandResult result = new UseItemCommandHandler()
            .execute(params, null, null, null).get();

        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("non-negative"));
    }
}
