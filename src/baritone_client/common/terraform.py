"""
Terraforming utilities - Reshape the world's surface using Baritone's selection tools.

These helpers are intentionally decoupled from the EndGameAutomator / StateManager /
Phase machinery in `automator/`. They only need a `client` and (optionally) a plain
`progress` dict for resumability, so they can be driven either from a phase handler
or from a small standalone script without touching the core automation files.

Core idea:
    Baritone exposes a chat-driven selection tool (`#sel`). You mark two corners
    with `#sel pos1 x y z` / `#sel pos2 x y z`, then `#sel set <block>` tells the
    builder process to fill every block in that box with `<block>` (air = clear).

    Terraforming a chunk = two fills:
      1. Clear everything from just above the target height up to a margin
         above it (knocks down hills/trees/mountains to the target height).
      2. Fill everything from a margin below the target height up to it with a
         solid block (levels valleys, oceans, and near-surface caves/ravines).

    Note: `#sel pos1/pos2` with explicit coordinates is standard Baritone
    behavior for the version pinned in bridge/libs (1.15.0). If a future
    Baritone build only accepts "the block you're looking at" for pos1/pos2,
    swap `select_region` to walk to each corner (see `goto` in navigation.py)
    and call `#sel pos1` / `#sel pos2` with no arguments instead.

Verification (see terraform_verify.py):
    `#sel set <block>` used to be trusted on the strength of Baritone going
    idle afterward -- true equally whether the fill actually happened, never
    started, or ran out of materials partway through. A chunk was then
    recorded done permanently, since nothing ever revisits a chunk believed
    finished. terraform_chunk now reads the world back (verify_chunk_flat)
    before recording success, and terraform_ring/terraform_area record a
    failed or unverified chunk durably instead of either aborting the whole
    lap or silently advancing past it. See plans/WORLD_TERRAFORM_PLAN.md.
"""

import time
from typing import Any, Callable, Dict, Optional, Set, Tuple

from .tasks import TaskResult
from .terraform_ledger import (
    DEFAULT_MAX_LIFETIME_ATTEMPTS,
    CircuitBreaker,
    chunk_key as _chunk_key,
    migrate_ledger as _migrate_ledger,
    record as _record,
    sync_v1_mirror as _sync_v1_mirror,
)
from .terraform_verify import (
    WORLD_MAX_Y,
    WORLD_MIN_Y,
    VerifyReport,
    _await_build_complete,
    verify_chunk_flat,
)

CHUNK_SIZE = 16

# Band-level retries inside one terraform_chunk call. Deliberately small: a
# retryable failure here is almost always deterministic (out of materials,
# terrain), so extra attempts buy little once the progress-gate below stops
# them early anyway.
DEFAULT_VERIFY_ATTEMPTS = 3


def _chat(client, message: str) -> None:
    """Send a raw Baritone chat command."""
    client.transport.dispatch("chat", {"message": message})


def select_region(
    client,
    x1: int, y1: int, z1: int,
    x2: int, y2: int, z2: int,
    settle: float = 0.3,
) -> None:
    """Define a Baritone selection covering the given bounding box (inclusive corners)."""
    _chat(client, f"#sel pos1 {x1} {y1} {z1}")
    time.sleep(settle)
    _chat(client, f"#sel pos2 {x2} {y2} {z2}")
    time.sleep(settle)


def clear_selection(client, settle: float = 0.2) -> None:
    """Drop the current Baritone selection (does not undo any fill already applied)."""
    _chat(client, "#sel clear")
    time.sleep(settle)


def await_builder_idle(client, timeout: float = 600.0, poll: float = 2.0, settle_checks: int = 3) -> bool:
    """
    Block until Baritone appears to have finished its current build/path task.

    Mirrors the idle-detection approach used in `navigation.explore_until`:
    polls `get_state` for `is_pathing` and considers the task done once it's
    been false for a few consecutive checks in a row (avoids false positives
    from brief pauses between placed blocks).

    Kept unchanged: common/city.py depends on this exact signature and
    behavior. terraform_chunk below uses the stronger build{action:"status"}
    signal via terraform_verify._await_build_complete instead, but that is a
    completion bound only -- verify_chunk_flat remains the sole source of
    truth for whether a fill actually happened.
    """
    start = time.time()
    idle_streak = 0
    while time.time() - start < timeout:
        try:
            state = client.transport.dispatch("get_state", {})
            is_pathing = state.get("is_pathing", False)
        except Exception:
            # A failed observation is not evidence that the builder is idle.
            idle_streak = 0
            time.sleep(poll)
            continue

        if is_pathing:
            idle_streak = 0
        else:
            idle_streak += 1
            if idle_streak >= settle_checks:
                return True

        time.sleep(poll)

    return False


def fill_selection(client, block: str, timeout: float = 600.0) -> bool:
    """Trigger `#sel set <block>` on the active selection and wait for it to finish.

    Kept unchanged: common/city.py's build_box/_flatten_district depend on
    this exact -> bool contract.
    """
    _chat(client, f"#sel set {block}")
    # Give Baritone a moment to start the build process before polling for idle.
    time.sleep(1.5)
    return await_builder_idle(client, timeout=timeout)


def _issue_fill_and_wait(client, block: str, timeout: float) -> Dict[str, Any]:
    """terraform_chunk's own fill-and-wait: sends "#sel set <block>" and waits
    on the uncached build{action:"status"} signal rather than is_pathing.

    Deliberately separate from fill_selection() above, which must keep its
    older is_pathing-based behavior for city.py. Either way, completion here
    only bounds the wait -- verify_chunk_flat decides whether anything
    actually happened.
    """
    _chat(client, f"#sel set {block}")
    time.sleep(1.5)
    return _await_build_complete(client, timeout=timeout)


def _verify_tiered(
    client,
    cx: int, cz: int,
    target_y: int,
    *,
    fill_block: str,
    clear_margin: int,
    fill_depth: int,
    attempt: int,
    bands: Tuple[str, ...],
    mode: str = "strict",
) -> VerifyReport:
    """Cheap gate first, deep check only if the gate passes.

    Failure is the common path today, so it should be the cheap one: a
    "lean" (anchors-only, ~25 reads) pass is nearly free next to a fill that
    can place up to 3328 blocks over many minutes. Only once that gate is
    clean is the fuller "standard" sample spent confirming it.
    """
    gate = verify_chunk_flat(
        client, cx, cz, target_y,
        fill_block=fill_block, clear_margin=clear_margin, fill_depth=fill_depth,
        attempt=attempt, profile="lean", mode=mode, bands=bands,
    )
    if gate.verdict != "pass":
        return gate
    deep = verify_chunk_flat(
        client, cx, cz, target_y,
        fill_block=fill_block, clear_margin=clear_margin, fill_depth=fill_depth,
        attempt=attempt, profile="standard", mode=mode, bands=bands,
    )
    deep.samples_checked += gate.samples_checked
    return deep


def _run_band(
    client,
    x1: int, y1: int, z1: int, x2: int, y2: int, z2: int,
    fill_value: str,
    cx: int, cz: int, target_y: int,
    fill_block: str, clear_margin: int, fill_depth: int, timeout: float,
    bands: Tuple[str, ...], verify: bool, verify_attempts: int, lifetime_attempts_used: int,
) -> Tuple[Optional[VerifyReport], Optional[Dict[str, Any]]]:
    """One select+fill+verify retry loop, shared by terraform_chunk's clear
    and fill steps (identical shape, different bounds/block/bands).

    Returns (report, early_failure). `early_failure`, when set, is a dict of
    kwargs the caller passes straight to its `_failed()` closure -- the loop
    already knows it must stop (a permanent verdict, an unobservable chunk,
    or a verify=False timeout). `report` is the last VerifyReport produced
    (or None when verify=False), for the caller's own post-loop check.
    """
    report: Optional[VerifyReport] = None
    previous_failed: Optional[int] = None
    for attempt in range(verify_attempts):
        select_region(client, x1, y1, z1, x2, y2, z2)
        wait = _issue_fill_and_wait(client, fill_value, timeout)
        if not verify:
            if not wait["completed"]:
                return None, {
                    "reason": f"{fill_value} step timed out for chunk ({cx}, {cz})",
                    "retryable": True,
                    "reason_class": "builder_never_started" if not wait["started"] else "timeout",
                }
            return None, None

        report = _verify_tiered(
            client, cx, cz, target_y,
            fill_block=fill_block, clear_margin=clear_margin, fill_depth=fill_depth,
            attempt=lifetime_attempts_used + attempt, bands=bands,
        )
        if report.verdict == "pass":
            return report, None
        if report.verdict == "unverified":
            # A failed observation is not evidence against the site -- do
            # not spend an attempt on it.
            return report, {
                "reason": f"Chunk ({cx}, {cz}) could not be observed ({report.reason_class})",
                "retryable": True, "reason_class": report.reason_class or "unloaded",
                "verification": report.__dict__,
            }
        if not report.retryable:
            return report, {
                "reason": f"{fill_value} failed permanently for chunk ({cx}, {cz}): {report.reason_class}",
                "retryable": False, "reason_class": report.reason_class,
                "verification": report.__dict__,
            }
        if previous_failed is not None and report.samples_failed >= previous_failed:
            # No progress between attempts; further retries of the same
            # band are pure cost.
            break
        previous_failed = report.samples_failed
    return report, None


def terraform_chunk(
    client,
    x: int, z: int,
    target_y: int,
    fill_block: str = "minecraft:stone",
    clear_margin: int = 48,
    fill_depth: int = 12,
    timeout: float = 600.0,
    verify: bool = True,
    verify_attempts: int = DEFAULT_VERIFY_ATTEMPTS,
    verify_profile: str = "standard",
    max_lifetime_attempts: int = DEFAULT_MAX_LIFETIME_ATTEMPTS,
    lifetime_attempts_used: int = 0,
) -> TaskResult:
    """
    Flatten a single 16x16 chunk to `target_y`.

    Args:
        client: Baritone client
        x, z: Any block coordinate inside the chunk (rounded down to the chunk origin)
        target_y: The height the surface should end up at
        fill_block: Block used to fill valleys/holes below target_y
        clear_margin: How far above target_y to clear (knocks down hills/trees)
        fill_depth: How far below target_y to fill (levels valleys/caves near the surface)
        timeout: Max seconds to wait for each fill operation
        verify: Read the world back before declaring success. Defaults True;
            disable only for a caller that has its own verification (e.g. a
            dry run that never intends to check).
        verify_attempts: Band-level retries before giving up this call.
        verify_profile: Passed through to verify_chunk_flat's deep pass
            ("standard" or "audit"; "lean" is always tried first as the gate).
        max_lifetime_attempts / lifetime_attempts_used: Caller-tracked budget
            across resumes and Pass-2 sweeps (see terraform_ring/_area). Once
            exhausted the chunk comes back non-retryable regardless of reason.
    """
    cx = (x // CHUNK_SIZE) * CHUNK_SIZE
    cz = (z // CHUNK_SIZE) * CHUNK_SIZE
    x1, z1 = cx, cz
    x2, z2 = cx + CHUNK_SIZE - 1, cz + CHUNK_SIZE - 1
    clear_top = min(target_y + clear_margin, WORLD_MAX_Y)
    fill_bottom = max(target_y - fill_depth, WORLD_MIN_Y)

    def _clear_selection_best_effort() -> None:
        # Best-effort cleanup: a bridge that just failed the call we are
        # unwinding from is not obligated to answer this one either, and
        # letting that second failure propagate would replace a clear
        # diagnosis (the original error) with a confusing one from cleanup.
        try:
            clear_selection(client)
        except Exception:
            pass

    def _done(reason: str, **data) -> TaskResult:
        _clear_selection_best_effort()
        return TaskResult.ok(reason, chunk=(cx, cz), **data)

    def _failed(reason: str, *, retryable: bool, reason_class: Optional[str], **data) -> TaskResult:
        _clear_selection_best_effort()
        return TaskResult.fail(
            reason, chunk=(cx, cz), retryable=retryable, reason_class=reason_class, **data
        )

    try:
        try:
            state = client.transport.dispatch("get_state", {})
            if isinstance(state, dict) and state.get("is_dead"):
                return _failed(
                    f"Bot is dead; cannot terraform chunk ({cx}, {cz})",
                    retryable=True, reason_class="player_dead",
                )
        except Exception as exc:
            return _failed(
                f"Bridge unreachable before chunk ({cx}, {cz}): {exc}",
                retryable=True, reason_class="bridge_error",
            )

        if lifetime_attempts_used >= max_lifetime_attempts:
            return _failed(
                f"Chunk ({cx}, {cz}) exhausted its lifetime attempt budget "
                f"({lifetime_attempts_used}/{max_lifetime_attempts})",
                retryable=False, reason_class="lifetime_exhausted",
            )

        # --- 1. CLEAR: knock down anything above target_y ---
        clear_report, early = _run_band(
            client, x1, target_y + 1, z1, x2, clear_top, z2, "air",
            cx, cz, target_y, fill_block, clear_margin, fill_depth, timeout,
            ("clear",), verify, verify_attempts, lifetime_attempts_used,
        )
        if early is not None:
            reason = early.pop("reason")
            return _failed(reason, **early)

        if verify and clear_report is not None and clear_report.verdict != "pass":
            return _failed(
                f"Clear could not be verified for chunk ({cx}, {cz}) after "
                f"{verify_attempts} attempt(s): {clear_report.reason_class}",
                retryable=True, reason_class=clear_report.reason_class or "clear_incomplete",
                verification=clear_report.__dict__,
            )

        # --- 2. Material preflight: do not spend a fill on a chunk with none carried ---
        try:
            from .inventory import count_item
            held = count_item(client, fill_block)
        except Exception:
            held = None
        if held is not None and held <= 0:
            return _failed(
                f"No {fill_block} carried; cannot fill chunk ({cx}, {cz})",
                retryable=True, reason_class="insufficient_materials",
            )

        # --- 3. FILL: level anything below target_y ---
        # Bands include "clear" too: filling can undo clearing (a gravel
        # column dropping into it, water crossing back in).
        fill_report, early = _run_band(
            client, x1, fill_bottom, z1, x2, target_y, z2, fill_block,
            cx, cz, target_y, fill_block, clear_margin, fill_depth, timeout,
            ("clear", "fill"), verify, verify_attempts, lifetime_attempts_used,
        )
        if early is not None:
            reason = early.pop("reason")
            return _failed(reason, **early)

        if verify and fill_report is not None and fill_report.verdict != "pass":
            return _failed(
                f"Fill could not be verified for chunk ({cx}, {cz}) after "
                f"{verify_attempts} attempt(s): {fill_report.reason_class}",
                retryable=True, reason_class=fill_report.reason_class or "fill_incomplete",
                verification=fill_report.__dict__,
            )

        return _done(
            f"Terraformed chunk ({cx}, {cz})",
            verification=(fill_report.__dict__ if fill_report is not None else None),
            verified=verify,
        )
    except Exception as exc:
        return _failed(
            f"Terraform error at chunk ({cx}, {cz}): {exc}",
            retryable=True, reason_class="bridge_error",
        )


def disk_chunk_offsets(radius_chunks: int):
    """Yield (dcx, dcz) chunk offsets filling a square disk out to `radius_chunks`, center first."""
    yield (0, 0)
    for r in range(1, radius_chunks + 1):
        for dx in range(-r, r + 1):
            yield (dx, -r)
            yield (dx, r)
        for dz in range(-r + 1, r):
            yield (-r, dz)
            yield (r, dz)


def chunk_ring_offsets(ring: int):
    """
    Yield (dcx, dcz) chunk offsets for the square *ring* at Chebyshev distance
    `ring` from the center (ring=0 is just the center chunk itself).

    Unlike `disk_chunk_offsets`, this only yields the outer shell, so calling
    it for ring=0,1,2,3,... covers the whole plane exactly once with no
    re-processing of already-finished chunks - the shape an unattended,
    ever-expanding terraform job wants.
    """
    if ring == 0:
        yield (0, 0)
        return
    for dx in range(-ring, ring + 1):
        yield (dx, -ring)
        yield (dx, ring)
    for dz in range(-ring + 1, ring):
        yield (-ring, dz)
        yield (ring, dz)


def sweep_pass(
    client,
    origins: list,
    target_y: int,
    *,
    fill_block: str,
    clear_margin: int,
    fill_depth: int,
    progress: Optional[Dict[str, Any]],
    ledger: Dict[str, Any],
    on_chunk_done: Optional[Callable[[int, int, Tuple[int, int]], None]],
    breaker: "CircuitBreaker",
    total_for_progress: Optional[int] = None,
    start_index: int = 0,
    index_key: str = "next_index",
) -> Optional[TaskResult]:
    """Run terraform_chunk over `origins` in order, recording every outcome
    and always advancing, even past a failure. Returns a TaskResult only if
    the circuit breaker trips (abort); otherwise returns None so the caller
    can decide what "done" means for its own shape (ring vs area vs sweep).
    """
    for i in range(start_index, len(origins)):
        chunk_x, chunk_z = origins[i]
        origin = (
            (chunk_x // CHUNK_SIZE) * CHUNK_SIZE,
            (chunk_z // CHUNK_SIZE) * CHUNK_SIZE,
        )
        key = _chunk_key(*origin)
        existing = ledger.get(key, {})

        if existing.get("status") in ("done", "skipped"):
            if progress is not None:
                progress[index_key] = i + 1
            continue
        if existing.get("retryable") is False:
            if progress is not None:
                progress[index_key] = i + 1
            continue

        result = terraform_chunk(
            client, chunk_x, chunk_z, target_y,
            fill_block=fill_block, clear_margin=clear_margin, fill_depth=fill_depth,
            lifetime_attempts_used=int(existing.get("attempts", 0)),
        )
        # "unverified" means "could not observe" (unloaded chunk, a dropped
        # bridge read); "failed" means "observed and wrong". Only the former
        # gets excused from the failed-chunk bucket.
        if result.success:
            status = "done"
        else:
            reason_class = (result.data or {}).get("reason_class")
            could_not_observe = reason_class in ("unloaded", "bridge_error") and (
                result.data or {}
            ).get("retryable")
            status = "unverified" if could_not_observe else "failed"

        _record(progress, ledger, origin, result, status=status)
        if progress is not None:
            progress[index_key] = i + 1
            if total_for_progress is not None:
                progress["total"] = total_for_progress
        if on_chunk_done:
            on_chunk_done(i + 1, len(origins), origin)

        if breaker.observe(status, (result.data or {}).get("reason_class")):
            if progress is not None:
                progress["last_error"] = result.reason
            return TaskResult.fail(
                f"Circuit breaker tripped on '{breaker.tripped_on}' at chunk {origin}",
                abort_scope="lap",
                reason_class=breaker.tripped_on,
                failed_chunk=origin,
            )

    if progress is not None:
        progress[index_key] = len(origins)
    return None


def terraform_ring(
    client,
    center_x: int, center_z: int,
    target_y: int,
    ring: int,
    fill_block: str = "minecraft:stone",
    clear_margin: int = 48,
    fill_depth: int = 12,
    progress: Optional[Dict[str, Any]] = None,
    on_chunk_done: Optional[Callable[[int, int, Tuple[int, int]], None]] = None,
    ledger: Optional[Dict[str, Any]] = None,
) -> TaskResult:
    """
    Terraform exactly the chunks forming the square ring at distance `ring`
    (in chunks) from (center_x, center_z). Resumable: pass a `progress` dict
    (e.g. loaded from a checkpoint file) and it will pick up from
    `progress["next_index"]` instead of restarting the ring from scratch.

    Record-and-continue: a chunk that fails verification is recorded and the
    cursor still advances past it, rather than aborting the entire remaining
    ring. `next_index == total` means the cursor swept every chunk once, NOT
    that every chunk succeeded -- check `result.success` /
    `result.data["chunks_failed"]` for that. A scoped circuit breaker (see
    CircuitBreaker) still aborts the lap early on a systemic failure.

    `ledger` is normally derived from `progress` (single-dict usage, e.g. an
    offline test or the dormant Phase.TERRAFORM handler). An unbounded runner
    that expects to keep going forever should instead hoist one persistent
    ledger across every ring it processes and pass it here explicitly --
    chunk_ring_offsets guarantees each ring's chunks are disjoint from every
    other ring's, so a shared ledger never collides, and a periodic sweep of
    old failures needs the accumulated history a per-ring reset would throw
    away (see terraform_forever.py).
    """
    progress = progress if progress is not None else {}
    if ledger is None:
        ledger = _migrate_ledger(progress)
    else:
        progress.setdefault("schema_version", 2)
    offsets = list(chunk_ring_offsets(ring))
    origins = [(center_x + dcx * CHUNK_SIZE, center_z + dcz * CHUNK_SIZE) for dcx, dcz in offsets]
    total = len(origins)
    start_index = progress.get("next_index", 0)

    breaker = CircuitBreaker()
    abort = sweep_pass(
        client, origins, target_y,
        fill_block=fill_block, clear_margin=clear_margin, fill_depth=fill_depth,
        progress=progress, ledger=ledger, on_chunk_done=on_chunk_done,
        breaker=breaker, total_for_progress=total, start_index=start_index,
    )
    if abort is not None:
        return TaskResult.fail(
            abort.reason, ring=ring, **{k: v for k, v in (abort.data or {}).items()}
        )

    # Pass 2: one deferred sweep over this ring's own retryable origins.
    # Inventory and position change over a lap; terrain does not, so a
    # second attempt right after the first can succeed where the first did
    # not (materials picked up along the way, a mob that moved off).
    retryable = [
        tuple(int(p) for p in key.split(","))
        for key, entry in ledger.items()
        if entry.get("status") in ("failed", "unverified") and entry.get("retryable") is not False
        and tuple(int(p) for p in key.split(",")) in {
            ((cx // CHUNK_SIZE) * CHUNK_SIZE, (cz // CHUNK_SIZE) * CHUNK_SIZE) for cx, cz in origins
        }
    ]
    if retryable:
        breaker2 = CircuitBreaker()
        abort2 = sweep_pass(
            client, retryable, target_y,
            fill_block=fill_block, clear_margin=clear_margin, fill_depth=fill_depth,
            progress=None, ledger=ledger, on_chunk_done=on_chunk_done,
            breaker=breaker2, index_key="_sweep2_index",
        )
        if progress is not None:
            _sync_v1_mirror(progress, ledger)
        if abort2 is not None:
            return TaskResult.fail(
                abort2.reason, ring=ring, **{k: v for k, v in (abort2.data or {}).items()}
            )

    # Scoped to THIS ring's own chunks, never the whole ledger: with a hoisted
    # (multi-ring) ledger, progress["counts"] covers every ring ever swept, so
    # one old failure in ring 3 would make ring 47 report incomplete forever.
    # Floor-divide first -- the ledger keys are chunk-aligned origins, but
    # `origins` is not necessarily aligned when center_x/center_z are not
    # multiples of CHUNK_SIZE.
    ring_keys = {
        _chunk_key((cx // CHUNK_SIZE) * CHUNK_SIZE, (cz // CHUNK_SIZE) * CHUNK_SIZE)
        for cx, cz in origins
    }
    ring_entries = [ledger[key] for key in ring_keys if key in ledger]
    done = sum(1 for e in ring_entries if e.get("status") == "done")
    failed = sum(1 for e in ring_entries if e.get("status") == "failed")
    unverified = sum(1 for e in ring_entries if e.get("status") == "unverified")
    ring_complete = failed == 0 and unverified == 0
    ring_failed_chunks = sorted(
        [int(p) for p in key.split(",")] for key in ring_keys
        if ledger.get(key, {}).get("status") == "failed"
    )
    ring_unverified_chunks = sorted(
        [int(p) for p in key.split(",")] for key in ring_keys
        if ledger.get(key, {}).get("status") == "unverified"
    )

    result = TaskResult.ok(
        f"Ring {ring}: {done} done, {failed} failed, {unverified} unverified "
        f"of {total} chunks",
        ring=ring,
        chunks=done,
        chunks_completed=done,
        chunks_total=total,
        chunks_failed=failed,
        chunks_unverified=unverified,
        failed_chunks=ring_failed_chunks,
        unverified_chunks=ring_unverified_chunks,
        sweep_complete=True,
        progress_complete=ring_complete,
    )
    if not ring_complete:
        result.success = False
        result.reason = (
            f"Ring {ring} swept but left {failed} failed and {unverified} "
            f"unverified chunk(s)"
        )
    return result


def terraform_area(
    client,
    center_x: int, center_z: int,
    target_y: int,
    radius_chunks: int = 8,
    fill_block: str = "minecraft:stone",
    clear_margin: int = 48,
    fill_depth: int = 12,
    progress: Optional[Dict[str, Any]] = None,
    on_chunk_done: Optional[Callable[[int, int, Tuple[int, int]], None]] = None,
    skip_chunks: Optional[Set[Tuple[int, int]]] = None,
    ledger: Optional[Dict[str, Any]] = None,
) -> TaskResult:
    """
    Terraform a bounded disk of chunks (radius `radius_chunks`) around a center
    point. Useful for a one-shot "flatten the area around the base/megabase"
    job. For an unbounded, ever-expanding job, call `terraform_ring` in a loop
    with `ring = 0, 1, 2, ...` instead (see terraform_forever.py).

    Same record-and-continue behavior as terraform_ring: a failed or
    unverified chunk is recorded and the cursor still advances, rather than
    aborting the whole disk on the first bad chunk. See terraform_ring's
    docstring for why `ledger` may be supplied externally.
    """
    progress = progress if progress is not None else {}
    if ledger is None:
        ledger = _migrate_ledger(progress)
    else:
        progress.setdefault("schema_version", 2)
    offsets = list(disk_chunk_offsets(radius_chunks))
    total = len(offsets)
    skipped_origins = set(skip_chunks or set())
    area_origins = {
        (
            ((center_x + dcx * CHUNK_SIZE) // CHUNK_SIZE) * CHUNK_SIZE,
            ((center_z + dcz * CHUNK_SIZE) // CHUNK_SIZE) * CHUNK_SIZE,
        )
        for dcx, dcz in offsets
    }
    skipped_origins.intersection_update(area_origins)
    for origin in skipped_origins:
        key = _chunk_key(*origin)
        if ledger.get(key, {}).get("status") != "done":
            ledger[key] = {"status": "skipped", "reason_class": "preserved", "attempts": 0}
    _sync_v1_mirror(progress, ledger)

    origins = [(center_x + dcx * CHUNK_SIZE, center_z + dcz * CHUNK_SIZE) for dcx, dcz in offsets]
    start_index = progress.get("next_index", 0)

    breaker = CircuitBreaker()
    abort = sweep_pass(
        client, origins, target_y,
        fill_block=fill_block, clear_margin=clear_margin, fill_depth=fill_depth,
        progress=progress, ledger=ledger, on_chunk_done=on_chunk_done,
        breaker=breaker, total_for_progress=total, start_index=start_index,
    )
    if abort is not None:
        return TaskResult.fail(
            abort.reason, radius_chunks=radius_chunks,
            **{k: v for k, v in (abort.data or {}).items()},
        )

    retryable = [
        (cx, cz) for cx, cz in origins
        if ledger.get(_chunk_key((cx // CHUNK_SIZE) * CHUNK_SIZE, (cz // CHUNK_SIZE) * CHUNK_SIZE), {}).get(
            "status"
        ) in ("failed", "unverified")
        and ledger.get(_chunk_key((cx // CHUNK_SIZE) * CHUNK_SIZE, (cz // CHUNK_SIZE) * CHUNK_SIZE), {}).get(
            "retryable"
        ) is not False
    ]
    if retryable:
        breaker2 = CircuitBreaker()
        abort2 = sweep_pass(
            client, retryable, target_y,
            fill_block=fill_block, clear_margin=clear_margin, fill_depth=fill_depth,
            progress=None, ledger=ledger, on_chunk_done=on_chunk_done,
            breaker=breaker2, index_key="_sweep2_index",
        )
        if progress is not None:
            _sync_v1_mirror(progress, ledger)
        if abort2 is not None:
            return TaskResult.fail(
                abort2.reason, radius_chunks=radius_chunks,
                **{k: v for k, v in (abort2.data or {}).items()},
            )

    # Scoped to this area's own chunks -- see terraform_ring's identical fix.
    area_keys = {
        _chunk_key((cx // CHUNK_SIZE) * CHUNK_SIZE, (cz // CHUNK_SIZE) * CHUNK_SIZE)
        for cx, cz in origins
    }
    area_entries = [ledger[key] for key in area_keys if key in ledger]
    done = sum(1 for e in area_entries if e.get("status") == "done")
    failed = sum(1 for e in area_entries if e.get("status") == "failed")
    unverified = sum(1 for e in area_entries if e.get("status") == "unverified")
    operation_total = total - len(skipped_origins)
    area_complete = failed == 0 and unverified == 0
    area_failed_chunks = sorted(
        [int(p) for p in key.split(",")] for key in area_keys
        if ledger.get(key, {}).get("status") == "failed"
    )
    area_unverified_chunks = sorted(
        [int(p) for p in key.split(",")] for key in area_keys
        if ledger.get(key, {}).get("status") == "unverified"
    )

    result = TaskResult.ok(
        f"Terraformed {done}/{operation_total} unprotected chunks "
        f"({failed} failed, {unverified} unverified)",
        chunks=done,
        chunks_completed=done,
        chunks_total=operation_total,
        chunks_failed=failed,
        chunks_unverified=unverified,
        progress_entries_total=total,
        failed_chunks=area_failed_chunks,
        unverified_chunks=area_unverified_chunks,
        skipped_chunks=[list(value) for value in sorted(skipped_origins)],
        radius_chunks=radius_chunks,
        sweep_complete=True,
        progress_complete=area_complete,
    )
    if not area_complete:
        result.success = False
        result.reason = (
            f"Area swept but left {failed} failed and {unverified} unverified chunk(s)"
        )
    return result
