package com.minecraftbot.baritone;

import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class CommandResultTest {

    @Test
    void structuredErrorRetainsLegacyMessageAndTypedFields() {
        CommandResult result = CommandResult.error(
            ErrorCode.RESOURCE_UNAVAILABLE,
            ErrorSeverity.ERROR,
            "Chest is not loaded",
            "target",
            "10,64,10",
            "a loaded block position",
            "Load the target chunk and retry"
        );

        JsonObject json = result.toJson();
        JsonObject error = json.getAsJsonArray("errors").get(0).getAsJsonObject();

        assertFalse(result.isSuccess());
        assertEquals("Chest is not loaded", result.getErrorMessage());
        assertEquals("error", json.get("status").getAsString());
        assertEquals("Chest is not loaded", json.get("error").getAsString());
        assertEquals("RESOURCE_UNAVAILABLE", error.get("code").getAsString());
        assertEquals("error", error.get("severity").getAsString());
        assertEquals("execution", error.get("category").getAsString());
        assertTrue(error.get("retryable").getAsBoolean());
        assertEquals("target", error.get("parameter").getAsString());
        assertEquals("10,64,10", error.get("provided_value").getAsString());
        assertEquals(
            "a loaded block position",
            error.get("expected_format").getAsString()
        );
        assertEquals(
            "Load the target chunk and retry",
            error.get("suggestion").getAsString()
        );
    }

    @Test
    void validationErrorsAreExplicitlyNotRetryable() {
        JsonObject error = CommandResult.error(
                ErrorCode.INVALID_COORDINATES,
                ErrorSeverity.ERROR,
                "Coordinates are invalid")
            .toJson()
            .getAsJsonArray("errors")
            .get(0)
            .getAsJsonObject();

        assertEquals("validation", error.get("category").getAsString());
        assertFalse(error.get("retryable").getAsBoolean());
    }
}
