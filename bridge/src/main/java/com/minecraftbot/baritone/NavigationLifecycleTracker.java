package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import baritone.api.event.events.PathEvent;
import baritone.api.event.listener.AbstractGameEventListener;
import com.google.gson.JsonObject;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Translates Baritone's own path lifecycle into a status goto/mine/break_block
 * callers can poll, instead of inferring arrival from position deltas.
 *
 * <p>Baritone's CustomGoalProcess (goto), MineProcess (mine), and
 * BuilderProcess (break_block, mine-at-position) all execute through the same
 * shared IPathingBehavior, so {@link PathEvent} covers all three uniformly.
 * Command handlers call {@link #beginGeneration(IBaritone)} right before dispatching a
 * new goal to Baritone; this tracker only ever reports the status of the
 * single currently-active generation, which matches Baritone's own
 * one-goal-at-a-time model.
 *
 * <p>Every goto previously returned {@code started=true} regardless of
 * outcome, and the ending Python recorded for every navigation -- arrival,
 * abandonment, or replacement -- was the single label "cancelled". This
 * distinguishes them from Baritone's own authoritative signal: AT_GOAL is
 * arrival, CALC_FAILED/NEXT_CALC_FAILED is failure, and CANCELED is only
 * reported as an operator cancellation when the cancel handler flagged one
 * was actually requested (see {@link #markCancelRequested()}) -- otherwise
 * Baritone gave up on its own, which is a failure, not a cancellation.
 */
final class NavigationLifecycleTracker implements AbstractGameEventListener {

    enum Status {
        UNKNOWN,
        CALCULATING,
        ACTIVE,
        ARRIVED,
        FAILED,
        CANCELED,
        SUPERSEDED,
    }

    private static final NavigationLifecycleTracker INSTANCE = new NavigationLifecycleTracker();

    private final AtomicLong generationCounter = new AtomicLong(0);
    private final Object lock = new Object();

    private long currentGenerationId = 0;
    private Status currentStatus = Status.UNKNOWN;
    private boolean everActive = false;
    private boolean cancelRequested = false;
    private volatile boolean registered = false;

    private long lastResolvedGenerationId = 0;
    private Status lastResolvedStatus = Status.UNKNOWN;

    private NavigationLifecycleTracker() {
    }

    static NavigationLifecycleTracker getInstance() {
        return INSTANCE;
    }

    private static boolean isTerminal(Status status) {
        return status == Status.ARRIVED
            || status == Status.FAILED
            || status == Status.CANCELED
            || status == Status.SUPERSEDED;
    }

    /**
     * Assigns a new goal generation. Call this right before handing a new
     * goal to Baritone (setGoalAndPath, mine, clearArea). Returns the id the
     * caller should echo in its own response so a poller can correlate a
     * later status to this exact dispatch, not just "whatever is active now".
     */
    long beginGeneration(IBaritone baritone) {
        ensureRegistered(baritone);
        synchronized (lock) {
            if (currentGenerationId != 0 && !isTerminal(currentStatus)) {
                resolve(Status.SUPERSEDED);
            }
            currentGenerationId = generationCounter.incrementAndGet();
            currentStatus = Status.CALCULATING;
            everActive = false;
            cancelRequested = false;
            return currentGenerationId;
        }
    }

    /**
     * Registers this tracker against Baritone's own path event stream on
     * first real use, using the IBaritone instance the caller already has --
     * not an eager BaritoneAPI.getProvider().getPrimaryBaritone() call at mod
     * init, which has no guarantee a real client/world exists yet and, when
     * tried, broke an unrelated existing test: spy(new BaritoneAPIBridge())
     * makes Mockito's inline mock maker instrument the whole class via
     * ByteBuddy, touching every method's referenced types during
     * redefinition even though the method is never called, which was enough
     * to poison BuiltInRegistries for the rest of that JVM. Idempotent; a
     * failure (an IBaritone mock with no stubbed getGameEventHandler(), or a
     * genuinely unavailable instance) must not break the dispatch that
     * triggered it -- the tracker just stays best-effort/UNKNOWN and retries
     * registration on the next beginGeneration call.
     */
    private void ensureRegistered(IBaritone baritone) {
        if (registered || baritone == null) {
            return;
        }
        synchronized (lock) {
            if (registered) {
                return;
            }
            try {
                baritone.getGameEventHandler().registerEventListener(this);
                registered = true;
            } catch (Exception ignored) {
                // Retried on the next beginGeneration call.
            }
        }
    }

    /**
     * Flags that the next CANCELED path event was operator-requested, not
     * Baritone abandoning the goal on its own. Call immediately before
     * cancelEverything().
     */
    void markCancelRequested() {
        synchronized (lock) {
            cancelRequested = true;
        }
    }

    @Override
    public void onPathEvent(PathEvent event) {
        synchronized (lock) {
            // Baritone can still emit cleanup events (e.g. a CANCELED as part
            // of tearing down a process that already reported AT_GOAL) after
            // this generation has already resolved. A resolved generation
            // never changes again; the next live signal belongs to whatever
            // generation beginGeneration() starts next.
            if (currentGenerationId == 0 || isTerminal(currentStatus)) {
                return;
            }
            switch (event) {
                case CALC_STARTED:
                case NEXT_SEGMENT_CALC_STARTED:
                    currentStatus = everActive ? Status.ACTIVE : Status.CALCULATING;
                    break;
                case CALC_FINISHED_NOW_EXECUTING:
                case NEXT_SEGMENT_CALC_FINISHED:
                case CONTINUING_ONTO_PLANNED_NEXT:
                case SPLICING_ONTO_NEXT_EARLY:
                case PATH_FINISHED_NEXT_STILL_CALCULATING:
                    currentStatus = Status.ACTIVE;
                    everActive = true;
                    break;
                case AT_GOAL:
                    resolve(Status.ARRIVED);
                    break;
                case CALC_FAILED:
                case NEXT_CALC_FAILED:
                    resolve(Status.FAILED);
                    break;
                case CANCELED:
                    resolve(cancelRequested ? Status.CANCELED : Status.FAILED);
                    break;
                case DISCARD_NEXT:
                default:
                    break;
            }
        }
    }

    /** Caller must hold {@link #lock}. */
    private void resolve(Status status) {
        currentStatus = status;
        lastResolvedGenerationId = currentGenerationId;
        lastResolvedStatus = status;
    }

    /**
     * A snapshot suitable for embedding in get_state: the currently active
     * (or most recently dispatched) generation's live status, plus what
     * happened to whichever generation resolved most recently -- which may be
     * this one, or the one it superseded.
     */
    JsonObject snapshot() {
        JsonObject data = new JsonObject();
        synchronized (lock) {
            data.addProperty("goal_id", currentGenerationId);
            data.addProperty("status", name(currentStatus));
            data.addProperty("ever_active", everActive);
            JsonObject lastResolution = new JsonObject();
            lastResolution.addProperty("goal_id", lastResolvedGenerationId);
            lastResolution.addProperty("status", name(lastResolvedStatus));
            data.add("last_resolution", lastResolution);
        }
        return data;
    }

    private static String name(Status status) {
        return status.name().toLowerCase(Locale.ROOT);
    }

    /** Test-only: clears all state so tests do not leak into each other. */
    void resetForTests() {
        synchronized (lock) {
            currentGenerationId = 0;
            currentStatus = Status.UNKNOWN;
            everActive = false;
            cancelRequested = false;
            lastResolvedGenerationId = 0;
            lastResolvedStatus = Status.UNKNOWN;
            registered = false;
        }
        generationCounter.set(0);
    }
}
