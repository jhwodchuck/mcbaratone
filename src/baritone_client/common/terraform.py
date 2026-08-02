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
"""

import time
from typing import Any, Callable, Dict, Optional, Tuple

from .tasks import TaskResult

# Minecraft world height limits (1.18+). Used to bound clear/fill volumes.
WORLD_MIN_Y = -64
WORLD_MAX_Y = 320

CHUNK_SIZE = 16


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
    """Trigger `#sel set <block>` on the active selection and wait for it to finish."""
    _chat(client, f"#sel set {block}")
    # Give Baritone a moment to start the build process before polling for idle.
    time.sleep(1.5)
    return await_builder_idle(client, timeout=timeout)


def terraform_chunk(
    client,
    x: int, z: int,
    target_y: int,
    fill_block: str = "minecraft:stone",
    clear_margin: int = 48,
    fill_depth: int = 12,
    timeout: float = 600.0,
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
    """
    cx = (x // CHUNK_SIZE) * CHUNK_SIZE
    cz = (z // CHUNK_SIZE) * CHUNK_SIZE
    x1, z1 = cx, cz
    x2, z2 = cx + CHUNK_SIZE - 1, cz + CHUNK_SIZE - 1

    try:
        # 1. Knock down anything above target_y (hills, trees, mountains).
        clear_top = min(target_y + clear_margin, WORLD_MAX_Y)
        select_region(client, x1, target_y + 1, z1, x2, clear_top, z2)
        if not fill_selection(client, "air", timeout=timeout):
            return TaskResult.fail(f"Clear timed out for chunk ({cx}, {cz})", chunk=(cx, cz))

        # 2. Fill anything below target_y within fill_depth (valleys, oceans, caves).
        fill_bottom = max(target_y - fill_depth, WORLD_MIN_Y)
        select_region(client, x1, fill_bottom, z1, x2, target_y, z2)
        if not fill_selection(client, fill_block, timeout=timeout):
            return TaskResult.fail(f"Fill timed out for chunk ({cx}, {cz})", chunk=(cx, cz))

        clear_selection(client)
        return TaskResult.ok(f"Terraformed chunk ({cx}, {cz})", chunk=(cx, cz))
    except Exception as exc:
        return TaskResult.fail(f"Terraform error at chunk ({cx}, {cz}): {exc}", chunk=(cx, cz))


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
) -> TaskResult:
    """
    Terraform exactly the chunks forming the square ring at distance `ring`
    (in chunks) from (center_x, center_z). Resumable: pass a `progress` dict
    (e.g. loaded from a checkpoint file) and it will pick up from
    `progress["next_index"]` instead of restarting the ring from scratch.
    """
    offsets = list(chunk_ring_offsets(ring))
    total = len(offsets)
    start_index = (progress or {}).get("next_index", 0)

    completed = 0
    for i in range(start_index, total):
        dcx, dcz = offsets[i]
        chunk_x = center_x + dcx * CHUNK_SIZE
        chunk_z = center_z + dcz * CHUNK_SIZE

        result = terraform_chunk(
            client, chunk_x, chunk_z, target_y,
            fill_block=fill_block, clear_margin=clear_margin, fill_depth=fill_depth,
        )
        if not result.success:
            return TaskResult.fail(
                result.reason,
                ring=ring,
                failed_chunk=(chunk_x, chunk_z),
                chunks_completed=completed,
                chunks_total=total,
            )

        completed += 1
        if progress is not None:
            progress["next_index"] = i + 1
            progress["total"] = total
        if on_chunk_done:
            on_chunk_done(i + 1, total, (chunk_x, chunk_z))

    if progress is not None:
        progress["next_index"] = total

    return TaskResult.ok(
        f"Terraformed ring {ring} ({completed} chunks processed)",
        ring=ring,
        chunks=completed,
        chunks_completed=completed,
        chunks_total=total,
        progress_complete=True,
    )


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
    skip_chunks: Optional[set[Tuple[int, int]]] = None,
) -> TaskResult:
    """
    Terraform a bounded disk of chunks (radius `radius_chunks`) around a center
    point. Useful for a one-shot "flatten the area around the base/megabase"
    job. For an unbounded, ever-expanding job, call `terraform_ring` in a loop
    with `ring = 0, 1, 2, ...` instead (see terraform_forever.py).
    """
    offsets = list(disk_chunk_offsets(radius_chunks))
    total = len(offsets)
    start_index = (progress or {}).get("next_index", 0)
    skipped_origins = set(skip_chunks or set())
    area_origins = {
        (
            ((center_x + dcx * CHUNK_SIZE) // CHUNK_SIZE) * CHUNK_SIZE,
            ((center_z + dcz * CHUNK_SIZE) // CHUNK_SIZE) * CHUNK_SIZE,
        )
        for dcx, dcz in offsets
    }
    skipped_origins.intersection_update(area_origins)
    completed_origins = set()
    if progress is not None:
        completed_origins = {
            tuple(value)
            for value in progress.get("completed_chunks", [])
            if isinstance(value, (list, tuple)) and len(value) == 2
        }
    completed_origins.difference_update(skipped_origins)

    for i in range(start_index, total):
        dcx, dcz = offsets[i]
        chunk_x = center_x + dcx * CHUNK_SIZE
        chunk_z = center_z + dcz * CHUNK_SIZE
        chunk_origin = (
            (chunk_x // CHUNK_SIZE) * CHUNK_SIZE,
            (chunk_z // CHUNK_SIZE) * CHUNK_SIZE,
        )

        if chunk_origin in skipped_origins:
            if progress is not None:
                progress["next_index"] = i + 1
                progress["total"] = total
            continue

        result = terraform_chunk(
            client, chunk_x, chunk_z, target_y,
            fill_block=fill_block, clear_margin=clear_margin, fill_depth=fill_depth,
        )
        if not result.success:
            return TaskResult.fail(
                result.reason,
                failed_chunk=(chunk_x, chunk_z),
                chunks_completed=len(completed_origins),
                chunks_total=total - len(skipped_origins),
                radius_chunks=radius_chunks,
            )

        completed_origins.add(chunk_origin)
        if progress is not None:
            progress["next_index"] = i + 1
            progress["total"] = total
            progress["completed_chunks"] = [
                list(value) for value in sorted(completed_origins)
            ]
        if on_chunk_done:
            on_chunk_done(i + 1, total, (chunk_x, chunk_z))

    completed = len(completed_origins)
    operation_total = total - len(skipped_origins)
    return TaskResult.ok(
        f"Terraformed {completed}/{operation_total} unprotected chunks",
        chunks=completed,
        chunks_completed=completed,
        chunks_total=operation_total,
        progress_entries_total=total,
        skipped_chunks=[list(value) for value in sorted(skipped_origins)],
        radius_chunks=radius_chunks,
        progress_complete=True,
    )
