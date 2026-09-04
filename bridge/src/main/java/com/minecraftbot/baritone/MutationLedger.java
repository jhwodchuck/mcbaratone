package com.minecraftbot.baritone;

import com.google.gson.JsonObject;
import java.util.*;
import java.util.concurrent.*;
import java.util.function.Supplier;

/** In-memory request-id deduplication. Never durable across game-JVM restart. */
final class MutationLedger {
    static final long WINDOW_MS = 600_000;
    static final Set<String> ROUTES = Set.of("place_block", "place_fire", "use_bucket",
        "smelt_items", "inventory_click", "interact_block");
    private record Entry(String fingerprint, long created, CompletableFuture<CommandResult> result) {}
    record Response(CommandResult result, boolean replayed) {}
    private final Map<String, Entry> entries = new HashMap<>();

    Response execute(String id, String fingerprint, Supplier<CommandResult> mutation) {
        Entry entry;
        boolean owner;
        synchronized (entries) {
            long now = System.currentTimeMillis();
            entries.entrySet().removeIf(e -> e.getValue().result.isDone() && now - e.getValue().created > WINDOW_MS);
            entry = entries.get(id);
            owner = entry == null;
            if (!owner && !entry.fingerprint.equals(fingerprint))
                return new Response(CommandResult.error("Request id reused with different mutation parameters"), false);
            if (owner) {
                if (entries.size() >= 4096)
                    return new Response(CommandResult.error("Mutation deduplication ledger full; no mutation dispatched"), false);
                entry = new Entry(fingerprint, now, new CompletableFuture<>());
                entries.put(id, entry);
            }
        }
        if (owner) {
            try { entry.result.complete(mutation.get()); }
            catch (Exception error) {
                JsonObject data = new JsonObject();
                data.addProperty("action_status", "unknown");
                data.addProperty("postcondition_verified", false);
                entry.result.complete(new CommandResult(false, data, "Mutation failed; reconcile: " + error.getMessage()));
            }
        }
        try { return new Response(entry.result.get(31, TimeUnit.SECONDS), !owner); }
        catch (Exception error) {
            if (error instanceof InterruptedException) Thread.currentThread().interrupt();
            return new Response(CommandResult.error("Mutation result pending/unknown; do not replay with a new id"), !owner);
        }
    }
}
