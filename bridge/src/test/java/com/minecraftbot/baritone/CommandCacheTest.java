package com.minecraftbot.baritone;

import com.google.gson.JsonObject;
import java.util.List;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class CommandCacheTest {

    @Test
    void cachesOnlyImmutableMetadataAndNeverLiveObservations() {
        CommandCache cache = new CommandCache(10, 30_000);
        try {
            assertTrue(cache.isIdempotentCommand("get_version"));
            assertTrue(cache.isIdempotentCommand("get_recipes"));

            List<String> liveCommands = List.of(
                "get_state",
                "get_inventory",
                "get_screen",
                "get_events",
                "get_dimension",
                "get_death_location",
                "get_view",
                "get_entities",
                "get_player_pos",
                "get_block"
            );
            liveCommands.forEach(command ->
                assertFalse(cache.isIdempotentCommand(command), command));

            JsonObject params = new JsonObject();
            CommandResult observation = CommandResult.success(new JsonObject());
            cache.put("get_state", params, observation);

            assertNull(cache.get("get_state", params));
            assertEquals(0, cache.size());
        } finally {
            cache.shutdown();
        }
    }
}
