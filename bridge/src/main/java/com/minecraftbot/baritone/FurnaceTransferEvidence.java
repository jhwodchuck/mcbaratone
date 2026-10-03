package com.minecraftbot.baritone;

/** Completion evidence shared by ordinary and already-consumed input. */
final class FurnaceTransferEvidence {
    static boolean moved(int sourceBefore, int sourceAfter, boolean landed, boolean consumed) {
        return sourceBefore > 0 && sourceAfter < sourceBefore && (landed || consumed);
    }
}
