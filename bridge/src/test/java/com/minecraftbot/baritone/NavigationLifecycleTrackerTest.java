package com.minecraftbot.baritone;

import baritone.api.event.events.PathEvent;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/**
 * Every goto used to return started=true regardless of outcome, and every
 * navigation ending Python recorded was the single label "cancelled". These
 * pin the arrival/failure/supersession/cancellation distinctions this tracker
 * exists to make from Baritone's own PathEvent stream, not from position
 * deltas.
 */
class NavigationLifecycleTrackerTest {

    private final NavigationLifecycleTracker tracker = NavigationLifecycleTracker.getInstance();

    // The tracker is a process-wide singleton, matching production (one
    // Baritone instance, one navigation lifecycle per JVM). Reset on both
    // sides of every test so ordering relative to other test classes in the
    // same JVM worker cannot leak state either direction.
    @BeforeEach
    void resetBefore() {
        tracker.resetForTests();
    }

    @AfterEach
    void resetAfter() {
        tracker.resetForTests();
    }

    @Test void freshGenerationStartsCalculating() {
        tracker.beginGeneration(null);
        assertEquals("calculating", tracker.snapshot().get("status").getAsString());
    }

    @Test void arrivalIsReportedDistinctlyFromCancellation() {
        long goalId = tracker.beginGeneration(null);
        tracker.onPathEvent(PathEvent.CALC_FINISHED_NOW_EXECUTING);
        assertEquals("active", tracker.snapshot().get("status").getAsString());
        tracker.onPathEvent(PathEvent.AT_GOAL);
        var snapshot = tracker.snapshot();
        assertEquals("arrived", snapshot.get("status").getAsString());
        assertEquals(goalId, snapshot.get("goal_id").getAsLong());
    }

    @Test void unrequestedCancellationIsReportedAsFailureNotCancellation() {
        // Baritone gave up on its own -- no cancel command was ever issued.
        tracker.beginGeneration(null);
        tracker.onPathEvent(PathEvent.CANCELED);
        assertEquals("failed", tracker.snapshot().get("status").getAsString());
    }

    @Test void operatorRequestedCancellationIsReportedAsCancellation() {
        tracker.beginGeneration(null);
        tracker.markCancelRequested();
        tracker.onPathEvent(PathEvent.CANCELED);
        assertEquals("canceled", tracker.snapshot().get("status").getAsString());
    }

    @Test void aFailedCalculationIsFailedNotSilentlyCancelled() {
        tracker.beginGeneration(null);
        tracker.onPathEvent(PathEvent.CALC_FAILED);
        assertEquals("failed", tracker.snapshot().get("status").getAsString());
    }

    @Test void aNewGoalSupersedesAnUnresolvedPreviousGoal() {
        long firstGoal = tracker.beginGeneration(null);
        tracker.onPathEvent(PathEvent.CALC_FINISHED_NOW_EXECUTING);
        long secondGoal = tracker.beginGeneration(null);

        var snapshot = tracker.snapshot();
        assertEquals(secondGoal, snapshot.get("goal_id").getAsLong());
        assertEquals("calculating", snapshot.get("status").getAsString());
        var lastResolution = snapshot.getAsJsonObject("last_resolution");
        assertEquals(firstGoal, lastResolution.get("goal_id").getAsLong());
        assertEquals("superseded", lastResolution.get("status").getAsString());
        assertNotEquals(firstGoal, secondGoal);
    }

    @Test void anAlreadyArrivedGoalIsNeverDowngradedByALateCleanupEvent() {
        // Baritone can still emit CANCELED while tearing down a process that
        // already reported AT_GOAL. A resolved generation must stay resolved.
        tracker.beginGeneration(null);
        tracker.onPathEvent(PathEvent.AT_GOAL);
        tracker.onPathEvent(PathEvent.CANCELED);
        assertEquals("arrived", tracker.snapshot().get("status").getAsString());
    }

    @Test void reachingActiveIsRememberedAcrossARecalculation() {
        // NEXT_SEGMENT_CALC_STARTED must not regress an already-moving bot
        // back to "calculating", which would look like it never left the start.
        tracker.beginGeneration(null);
        tracker.onPathEvent(PathEvent.CALC_FINISHED_NOW_EXECUTING);
        tracker.onPathEvent(PathEvent.NEXT_SEGMENT_CALC_STARTED);
        assertEquals("active", tracker.snapshot().get("status").getAsString());
    }

    @Test void pathEventsBeforeAnyGoalAreIgnored() {
        tracker.onPathEvent(PathEvent.AT_GOAL);
        assertEquals("unknown", tracker.snapshot().get("status").getAsString());
        assertEquals(0, tracker.snapshot().get("goal_id").getAsLong());
    }

    @Test void cancelRequestedFlagDoesNotLeakIntoTheNextGeneration() {
        tracker.beginGeneration(null);
        tracker.markCancelRequested();
        tracker.onPathEvent(PathEvent.CANCELED);

        // A fresh goal that fails on its own must not inherit the previous
        // generation's cancel-requested flag.
        tracker.beginGeneration(null);
        tracker.onPathEvent(PathEvent.CALC_FAILED);
        assertEquals("failed", tracker.snapshot().get("status").getAsString());
    }
}
