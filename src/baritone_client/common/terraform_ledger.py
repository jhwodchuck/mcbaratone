"""Per-chunk outcome tracking for terraform_ring/terraform_area.

Split out of terraform.py to keep that module under its size budget; these
are pure bookkeeping functions with no dependency on terraform_chunk itself,
so they live independently rather than importing back into terraform.py and
risking a cycle.

The ledger exists because terraform_ring/terraform_area used to `return
TaskResult.fail` on the first bad chunk, aborting the whole remaining lap --
and terraform_forever.py never checked `result.success` before advancing to
the next ring and wiping progress, so a failed lap's remainder was silently
dropped with no record it ever happened. The ledger makes "record and
continue" possible: every chunk gets an entry, the cursor always advances,
and a failed or unverified chunk is never conflated with a done one.

One invariant makes that conflation structurally hard to reintroduce:
`status="done"` is written in exactly one place (`record`), from the single
call site that just observed `verify_chunk_flat` pass.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

from .tasks import TaskResult

DEFAULT_MAX_LIFETIME_ATTEMPTS = 5

# Consecutive occurrences before a lap aborts rather than continuing to bury
# a systemic failure under identical entries. Reset to zero on every "done".
#
# "unobserved" (unverified: unloaded chunk, dropped read) is deliberately
# separate from "retryable_fail" (failed: genuinely observed and wrong).
# Three unloaded chunks in a row is often just normal chunk-loading lag
# around the edge of render distance, not evidence of bad terrain -- folding
# it into the same bucket as real terrain failures would trip the breaker on
# a benign, self-resolving condition using the same signal that is supposed
# to mean "something is actually broken".
BREAKER_THRESHOLDS = {
    "retryable_fail": 3,
    "unobserved": 3,
    "insufficient_materials": 2,
    "bridge_error": 2,
    "player_dead": 1,
}


def chunk_key(x: int, z: int) -> str:
    return f"{x},{z}"


def migrate_ledger(progress: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize `progress` to the v2 ledger shape in place, and return the ledger.

    A missing schema_version is v1. v1 carries no per-chunk history, so its
    `completed_chunks` cannot be trusted as `done` -- it is evidence the run
    was *attempted* there, nothing about whether it actually succeeded. Seed
    those origins as `unverified` (not `done`), so they get re-checked rather
    than silently trusted forever. Re-verifying already-swept ground costs
    reads and places nothing; it is the only way to find damage already
    recorded as success under the old code. Callers that want the old (fast,
    trusting) behavior can pass a pre-populated v2 ledger with those entries
    already marked `done` instead of relying on this migration.
    """
    if progress.get("schema_version") == 2 and isinstance(progress.get("chunks"), dict):
        return progress["chunks"]

    ledger: Dict[str, Any] = {}
    for value in progress.get("completed_chunks", []) or []:
        if isinstance(value, (list, tuple)) and len(value) == 2:
            ledger[chunk_key(int(value[0]), int(value[1]))] = {
                "status": "unverified",
                "attempts": 0,
                "reason_class": "legacy_unverified",
            }
    progress["schema_version"] = 2
    progress["chunks"] = ledger
    return ledger


def record(
    progress: Optional[Dict[str, Any]],
    ledger: Dict[str, Any],
    origin: Tuple[int, int],
    result: Optional[TaskResult],
    *,
    status: str,
) -> None:
    """Write exactly one ledger entry for `origin`."""
    key = chunk_key(*origin)
    existing = ledger.get(key, {})
    attempts = int(existing.get("attempts", 0))
    entry: Dict[str, Any] = {"status": status, "attempts": attempts}

    if result is not None:
        data = result.data or {}
        # Only a genuinely-observed outcome spends lifetime budget. "unverified"
        # means nothing was actually read (unloaded chunk, dropped bridge
        # call, a dead bot) -- it is not evidence against the site, so it
        # must not count toward the same 5-attempt blacklist a real
        # observed-and-wrong failure does. Without this, a chunk that was
        # never once successfully read could still get permanently
        # blacklisted after 5 unlucky reads.
        if status in ("done", "failed"):
            entry["attempts"] = attempts + 1
        entry["reason_class"] = data.get("reason_class")
        entry["retryable"] = bool(data.get("retryable", status != "done"))
        entry["reason"] = result.reason
        verification = data.get("verification")
        if verification:
            entry["samples_checked"] = verification.get("samples_checked")
            entry["samples_failed"] = verification.get("samples_failed")
            entry["first_bad"] = verification.get("first_bad")
            tolerated = verification.get("tolerated")
            if tolerated:
                entry["tolerated"] = tolerated
        entry["last_ts"] = time.time()

    if status == "done":
        entry["retryable"] = False
        entry["reason_class"] = None
    if entry["attempts"] >= DEFAULT_MAX_LIFETIME_ATTEMPTS and status != "done":
        entry["retryable"] = False

    ledger[key] = entry
    if progress is not None:
        sync_v1_mirror(progress, ledger)


def sync_v1_mirror(progress: Dict[str, Any], ledger: Dict[str, Any]) -> None:
    """Keep the v1 `completed_chunks` key meaning byte-identical: only chunks
    with status == "done" ever appear in it."""
    done_origins = sorted(
        tuple(int(part) for part in key.split(","))
        for key, entry in ledger.items()
        if entry.get("status") == "done"
    )
    progress["completed_chunks"] = [list(origin) for origin in done_origins]
    progress["failed_chunks"] = sorted(
        [int(part) for part in key.split(",")]
        for key, entry in ledger.items()
        if entry.get("status") == "failed"
    )
    progress["unverified_chunks"] = sorted(
        [int(part) for part in key.split(",")]
        for key, entry in ledger.items()
        if entry.get("status") == "unverified"
    )
    progress["blacklist"] = sorted(
        [int(part) for part in key.split(",")]
        for key, entry in ledger.items()
        if entry.get("retryable") is False and entry.get("status") not in ("done", "skipped")
    )
    progress["counts"] = {
        "done": sum(1 for e in ledger.values() if e.get("status") == "done"),
        "failed": sum(1 for e in ledger.values() if e.get("status") == "failed"),
        "unverified": sum(1 for e in ledger.values() if e.get("status") == "unverified"),
        "skipped": sum(1 for e in ledger.values() if e.get("status") == "skipped"),
    }


class CircuitBreaker:
    """Aborts a lap on a systemic failure without needing 197 identical
    entries to notice one. Branches on the SCOPE of the failure: a chunk-local
    verdict (terrain, one bad selection) continues; a global precondition
    (no materials at all, bot died, bridge degraded) aborts after a handful
    of chunks instead of burying the real cause under a full ring of retries.
    Every counter resets on a `done` chunk.
    """

    def __init__(self) -> None:
        self.consecutive: Dict[str, int] = {key: 0 for key in BREAKER_THRESHOLDS}
        self.tripped_on: Optional[str] = None

    def observe(self, status: str, reason_class: Optional[str]) -> bool:
        if status == "done":
            for key in self.consecutive:
                self.consecutive[key] = 0
            return False
        if status == "skipped":
            return False

        bucket = None
        if reason_class == "insufficient_materials":
            bucket = "insufficient_materials"
        elif reason_class == "bridge_error":
            bucket = "bridge_error"
        elif reason_class == "player_dead":
            bucket = "player_dead"
        elif status == "unverified":
            bucket = "unobserved"
        elif status == "failed":
            bucket = "retryable_fail"

        if bucket is None:
            return False

        self.consecutive[bucket] += 1
        if self.consecutive[bucket] >= BREAKER_THRESHOLDS[bucket]:
            self.tripped_on = bucket
            return True
        return False
