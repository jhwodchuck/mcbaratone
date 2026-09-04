package com.minecraftbot.baritone;

import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class MutationLedgerTest {
    @Test void sameIdAndPayloadReplaysResultWithoutMutation() {
        var ledger = new MutationLedger(); var calls = new AtomicInteger();
        var first = ledger.execute("id", "place:A", () -> { calls.incrementAndGet(); return CommandResult.success(); });
        var again = ledger.execute("id", "place:A", () -> { fail("mutation replayed"); return null; });
        assertFalse(first.replayed()); assertTrue(again.replayed()); assertEquals(1, calls.get());
    }
    @Test void conflictingIdFailsBeforeDispatch() {
        var ledger = new MutationLedger();
        ledger.execute("id", "place:A", CommandResult::success);
        assertFalse(ledger.execute("id", "place:B", () -> { fail("conflict dispatched"); return null; }).result().isSuccess());
    }
    @Test void uncertainFailureIsAlsoRemembered() {
        var ledger = new MutationLedger();
        assertFalse(ledger.execute("id", "click", () -> { throw new RuntimeException("timeout"); }).result().isSuccess());
        assertFalse(ledger.execute("id", "click", () -> { fail("uncertain mutation replayed"); return null; }).result().isSuccess());
    }
}
