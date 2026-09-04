package com.minecraftbot.baritone;

import com.google.gson.JsonObject;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.*;

/** Dispatch once; report completion only after two distinct observed game ticks.
 * Client observations are evidence, not a claim of server acknowledgement.
 */
final class ObservedMutation {
    private static final ScheduledExecutorService TIMER = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "bridge-effect-observer"); t.setDaemon(true); return t;
    });

    static CompletableFuture<CommandResult> run(Executor main, LongSupplier tick,
            Supplier<CommandResult> dispatch, Predicate<JsonObject> observed, String effect) {
        return run(main, tick, dispatch, observed, effect, 3000);
    }

    static CompletableFuture<CommandResult> run(Executor main, LongSupplier tick,
            Supplier<CommandResult> dispatch, Predicate<JsonObject> observed, String effect, long timeoutMs) {
        CompletableFuture<CommandResult> future = new CompletableFuture<>();
        AtomicBoolean dispatched = new AtomicBoolean();
        AtomicReference<JsonObject> latest = new AtomicReference<>(new JsonObject());
        ScheduledFuture<?> deadline = TIMER.schedule(() -> {
            JsonObject data = latest.get().deepCopy();
            data.addProperty(effect, false);
            data.addProperty("action_status", dispatched.get() ? "unknown" : "not_dispatched");
            data.addProperty("postcondition_verified", false);
            data.addProperty("mutation_dispatched", dispatched.get());
            data.addProperty("retry_safe", !dispatched.get());
            future.complete(new CommandResult(false, data, "Observed effect deadline exceeded; reconcile before retry"));
        }, timeoutMs, TimeUnit.MILLISECONDS);
        future.whenComplete((r, e) -> deadline.cancel(false));
        main.execute(() -> {
            if (future.isDone()) return;
            try {
                dispatched.set(true);
                CommandResult accepted = dispatch.get();
                if (!accepted.isSuccess()) { future.complete(accepted); return; }
                JsonObject data = accepted.getData().deepCopy();
                data.addProperty(effect, false);
                data.addProperty("mutation_dispatched", true);
                data.addProperty("postcondition_verified", false);
                data.addProperty("applied_tick", tick.getAsLong());
                latest.set(data.deepCopy());
                Runnable probe = new Runnable() {
                    long lastMatch = Long.MIN_VALUE;
                    public void run() {
                        if (future.isDone()) return;
                        try {
                            long now = tick.getAsLong();
                            boolean matches = observed.test(data);
                            latest.set(data.deepCopy());
                            if (matches) {
                                if (lastMatch != Long.MIN_VALUE && now != lastMatch) {
                                    data.addProperty(effect, true);
                                    data.addProperty("action_status", "observed_complete");
                                    data.addProperty("postcondition_verified", true);
                                    data.addProperty("observed_tick", now);
                                    data.addProperty("evidence_scope", "client_world");
                                    future.complete(CommandResult.success(data)); return;
                                }
                                lastMatch = now;
                            } else { lastMatch = Long.MIN_VALUE; }
                            TIMER.schedule(() -> { if (!future.isDone()) main.execute(this); }, 50, TimeUnit.MILLISECONDS);
                        } catch (Exception e) {
                            data.addProperty("action_status", "unknown");
                            future.complete(new CommandResult(false, data, "Observation failed: " + e.getMessage()));
                        }
                    }
                };
                probe.run();
            } catch (Exception e) {
                JsonObject data = new JsonObject();
                data.addProperty("action_status", "unknown");
                data.addProperty("postcondition_verified", false);
                future.complete(new CommandResult(false, data, "Mutation failed; reconcile: " + e.getMessage()));
            }
        });
        return future;
    }
}
