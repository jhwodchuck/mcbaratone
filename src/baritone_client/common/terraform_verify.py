"""World-read verification for terraform.py's chunk fills.

terraform_chunk used to trust `await_builder_idle` -- a poll of
`get_state()["is_pathing"]` -- as proof that a fill succeeded. That signal
means "not currently walking", which is equally true whether the builder
finished, never started, ran out of materials, or could not reach the
selection. A chunk was then recorded done permanently, because the runner
never revisits a chunk it believes finished. At scale this becomes hundreds
of chunks of recorded-success no-op (see plans/WORLD_TERRAFORM_PLAN.md §2).

This module reads the world instead of watching the bot. It answers one
question -- "does this chunk look flat at target_y, from a bounded sample of
columns" -- and nothing more. It is not a proof: `VerifyReport.samples_checked`
records exactly how much ground was actually read, and a sparse sample can
miss a small defect (see the coverage note on `sample_columns`).

Two facts drive the whole design:
  - `void_air` must be classified as UNKNOWN, never as evidence. Overworld
    terrain never generates void_air within [-64, 319]; seeing it means the
    chunk is not loaded. A two-valued predicate that treats it as "air" (pass
    the clear band) or "not air" (pass the fill band) reproduces the original
    defect with a new sensor -- an unvisited chunk would pass both bands.
  - A sampled column only proves something when BOTH bands are read at it:
    solid(target_y) alone passes a chunk buried inside a mountain; air(T+1)
    alone passes a crater. Neither proves a platform alone.
"""

from __future__ import annotations

import time
import zlib
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

Position = Tuple[int, int, int]
Column = Tuple[int, int]

# 1.18+ overworld build range. 320 is the EXCLUSIVE Minecraft height limit; the
# inclusive ceiling is 319. Sampling y=320 reads an out-of-world row that
# always returns void_air, which under the classification rule above marks
# the chunk unverified forever -- verification would deadlock exactly where
# the unfixed code silently "succeeded". WORLD_MIN_Y=-64 is already inclusive.
WORLD_MIN_Y = -64
WORLD_MAX_Y = 319

# --- block classification ---------------------------------------------------
#
# Exact names or explicit suffix checks only -- never substring. "grass" in
# name matches both grass_block (ground) and short_grass (a plant); that
# exact bug is on record at actions/homestead.py.

AIR = frozenset({"air", "cave_air", "void_air"})
LIQUIDS = frozenset({"water", "lava", "bubble_column"})
UNBREAKABLE = frozenset({
    "bedrock", "barrier", "end_portal", "end_portal_frame", "end_gateway",
    "structure_void", "structure_block", "jigsaw", "light", "command_block",
    "chain_command_block", "repeating_command_block", "reinforced_deepslate",
})
# A tool gap, not terrain -- kept apart from UNBREAKABLE so it is never
# silently tolerated as "the ground was like that".
UNTOOLED = frozenset({"obsidian", "crying_obsidian", "respawn_anchor", "ancient_debris"})
REGROWTH = frozenset({
    "short_grass", "tall_grass", "fern", "large_fern", "dead_bush", "snow", "vine",
    "glow_lichen", "seagrass", "kelp", "kelp_plant", "moss_carpet", "azalea",
    "cave_vines", "cave_vines_plant",
})
_REGROWTH_SUFFIXES = ("_sapling", "_carpet")

FALLING = frozenset({
    "sand", "red_sand", "gravel", "anvil", "chipped_anvil", "damaged_anvil",
    "suspicious_sand", "suspicious_gravel",
})
_FALLING_SUFFIXES = ("_concrete_powder",)

NON_SOLID = frozenset({
    "short_grass", "tall_grass", "fern", "large_fern", "dead_bush", "snow", "torch",
    "wall_torch", "soul_torch", "ladder", "vine", "glow_lichen", "cobweb", "scaffolding",
    "lily_pad", "seagrass", "kelp", "kelp_plant", "sugar_cane", "bamboo", "rail",
    "powered_rail", "detector_rail", "activator_rail", "tripwire", "tripwire_hook",
    "chain", "iron_bars", "fire", "soul_fire", "cave_vines", "cave_vines_plant",
    "big_dripleaf", "small_dripleaf", "hanging_roots", "sculk_vein", "lever", "string",
    "end_rod", "lightning_rod", "flower_pot", "cake",
})
_NON_SOLID_SUFFIXES = (
    "_leaves", "_sapling", "_carpet", "_sign", "_hanging_sign", "_banner",
    "_button", "_pressure_plate", "_door", "_trapdoor", "_slab", "_stairs",
    "_fence", "_fence_gate", "_wall", "_pane", "_bed", "_candle", "_coral",
    "_coral_fan", "_sprouts", "_roots", "_bush", "_tulip", "_orchid",
)


def classify_block(block_id: Optional[str]) -> str:
    """Bare block name, or "unknown" for an unloaded/unreadable block.

    "unknown" is returned for a missing/empty id AND for void_air -- the
    single highest-severity trap in this module. It must be checked before
    any air/not-air test, in every caller.
    """
    if not block_id:
        return "unknown"
    name = str(block_id).split(":")[-1]
    if not name or name == "void_air":
        return "unknown"
    return name


def _ends_with_any(name: str, suffixes: Sequence[str]) -> bool:
    return any(name.endswith(suffix) for suffix in suffixes)


def _is_regrowth(name: str) -> bool:
    return name in REGROWTH or _ends_with_any(name, _REGROWTH_SUFFIXES)


def _is_non_solid(name: str) -> bool:
    return name in NON_SOLID or _ends_with_any(name, _NON_SOLID_SUFFIXES)


def _is_falling(name: str) -> bool:
    return name in FALLING or _ends_with_any(name, _FALLING_SUFFIXES)


def clear_verdict(name: str, mode: str = "strict") -> Tuple[str, Optional[str]]:
    """Classify one clear-band read (target_y+1 .. clear_top).

    Returns (verdict, reason_class); verdict is "pass", "fail_permanent", or
    "fail_retryable". `name` must already be resolved via classify_block --
    never pass a raw block id.
    """
    if name in ("air", "cave_air"):
        return "pass", None
    if mode == "audit" and _is_regrowth(name):
        # Only in audit mode: seconds after "#sel set air" nothing has had
        # time to regrow, so its presence there proves the clear never ran.
        return "pass", None
    if name in LIQUIDS:
        return "fail_permanent", "liquid"
    if name in UNBREAKABLE:
        return "fail_permanent", "unbreakable"
    if name in UNTOOLED:
        return "fail_permanent", "untooled"
    return "fail_retryable", "clear_incomplete"


def fill_verdict(
    name: str,
    mode: str = "strict",
    read_below: Optional[Callable[[], str]] = None,
) -> Tuple[str, Optional[str]]:
    """Classify one fill-band read (fill_bottom .. target_y, inclusive).

    `read_below` is called at most once, only when `name` is a falling block,
    to check whether it has solid support -- depth 1, never recursed further.
    Bedrock, deepslate, dirt, grass_block, ore, and obsidian all PASS: they
    are a floor, and the goal is a solid platform, not a specific block.
    Unrecognised names PASS too (see the module docstring's coverage note);
    the failure this exists to catch (air) is a recognised name.
    """
    if name in AIR:
        return "fail_retryable", "fill_incomplete"
    if name in LIQUIDS:
        return "fail_permanent", "liquid"
    if _is_non_solid(name):
        return "fail_retryable", "fill_incomplete"
    if _is_falling(name):
        below = classify_block(read_below()) if read_below is not None else "unknown"
        if below == "unknown" or below in AIR or below in LIQUIDS or _is_non_solid(below):
            return "fail_retryable", "unstable_fill"
        return "pass", None
    return "pass", None


# --- sampling geometry --------------------------------------------------------
#
# cx, cz must be the chunk's ALREADY floor-divided origin -- the same
# expression terraform_chunk uses ((x // 16) * 16). Recomputing with
# int(x/16)*16 truncates toward zero and silently reads the wrong chunk for
# any negative coordinate.

_ANCHORS: Tuple[Column, ...] = (
    (0, 0), (15, 0), (0, 15), (15, 15),
    (8, 0), (0, 8), (15, 8), (8, 15),
    (8, 8),
)
_PERIMETER: Tuple[Column, ...] = tuple(c for c in _ANCHORS if c != (8, 8))


def lattice_columns(cx: int, cz: int, attempt: int = 0) -> Tuple[Column, ...]:
    """16 columns on a spacing-4 lattice, phase-jittered by (cx, cz, attempt).

    Guarantee: spacing 4 in both axes means any axis-aligned 4x4 patch of
    columns contains at least one lattice point, in EVERY phase. The jitter
    changes which point, so a retry (new `attempt`) probes genuinely new
    ground instead of re-asking a question that just passed by luck -- and a
    globally fixed phase would be a systematic blind spot correlated across
    the whole fleet forever. crc32, not random.random(): deterministic and
    replayable from a checkpoint.
    """
    digest = zlib.crc32(f"{cx},{cz},{attempt}".encode("ascii"))
    a, b = digest & 3, (digest >> 2) & 3
    return tuple((a + 4 * i, b + 4 * j) for i in range(4) for j in range(4))


def sample_columns(cx: int, cz: int, attempt: int = 0) -> Tuple[Column, ...]:
    """Anchors (fixed forever) union the jittered lattice (varies by attempt).

    Coverage is intentionally partial and this is not a proof of flatness: at
    most 25 of a chunk's 256 columns (~10%). It deterministically catches any
    defect at least 4x4 columns wide (a hill, a pond, a missed swath); it
    cannot reliably catch a single missed column or one leftover tree trunk.
    Callers should name the result accordingly (`samples_checked`, not
    "verified exhaustively") and lean on `profile="audit"` for periodic
    denser passes rather than treating a "standard" pass as a guarantee.
    """
    ordered = list(_ANCHORS)
    for column in lattice_columns(cx, cz, attempt):
        if column not in _ANCHORS:
            ordered.append(column)
    return tuple(ordered)


def chunk_sample_positions(
    cx: int,
    cz: int,
    target_y: int,
    clear_top: int,
    fill_bottom: int,
    attempt: int = 0,
    profile: str = "standard",
) -> Tuple[List[Position], List[Position]]:
    """Return (clear_positions, fill_positions) as absolute (x, y, z).

    Every sampled column contributes to BOTH lists at the paired heights
    (target_y for fill, target_y+1 for clear) -- the column-pair rule. Never
    sample one band at one column set and the other band at a different one.

    profile:
      "lean"     -- anchors only. Cheapest; used on the fail path (failure is
                    the common case today, so it should be the cheap path).
      "standard" -- anchors + full jittered lattice, plus a shallow ladder of
                    extra heights. Default; used to confirm a pass.
      "audit"    -- every column in the chunk, for periodic deep re-checks
                    (a re-verified "done" chunk, or a live acceptance gate).
    """
    clear_top = min(clear_top, WORLD_MAX_Y)
    fill_bottom = max(fill_bottom, WORLD_MIN_Y)

    def abs_cols(cols: Sequence[Column]) -> List[Column]:
        return [(cx + dx, cz + dz) for dx, dz in cols]

    anchors_abs = abs_cols(_ANCHORS)
    perimeter_abs = abs_cols(_PERIMETER)

    clear_positions: List[Position] = []
    fill_positions: List[Position] = []

    def add_pair(cols: Sequence[Column]) -> None:
        for x, z in cols:
            clear_positions.append((x, target_y + 1, z))
            fill_positions.append((x, target_y, z))

    # Tier A -- gate, always sampled.
    add_pair(anchors_abs)
    for x, z in (anchors_abs[0], anchors_abs[1], anchors_abs[2], anchors_abs[3], (cx + 8, cz + 8)):
        fill_positions.append((x, fill_bottom, z))

    if profile == "lean":
        return clear_positions, fill_positions

    if profile == "audit":
        for dx in range(16):
            for dz in range(16):
                x, z = cx + dx, cz + dz
                if (dx, dz) in _ANCHORS:
                    continue
                clear_positions.append((x, target_y + 1, z))
                fill_positions.append((x, target_y, z))
        for x, z in perimeter_abs:
            fill_positions.append((x, fill_bottom, z))
            rung = target_y + 4
            while rung <= clear_top:
                clear_positions.append((x, rung, z))
                rung *= 2
        return clear_positions, fill_positions

    # Tier B ("standard") -- lattice plus a shallow height ladder.
    lattice_abs = abs_cols(lattice_columns(cx, cz, attempt))
    lattice_only = [c for c in lattice_abs if c not in anchors_abs]
    add_pair(lattice_only)
    for x, z in lattice_abs:
        clear_positions.append((x, target_y + 2, z))
    for x, z in anchors_abs:
        for extra in (target_y + 4, target_y + 8):
            if extra <= clear_top:
                clear_positions.append((x, extra, z))
    for x, z in perimeter_abs:
        rung = target_y + 16
        while rung <= clear_top:
            clear_positions.append((x, rung, z))
            rung *= 2
    # A couple of extra fill-band depths at the anchors, deduplicated against
    # what add_pair(anchors_abs) already added at target_y.
    mid_depth = target_y - max(1, (target_y - fill_bottom) // 2)
    for x, z in anchors_abs:
        for extra_y in (target_y - 3, mid_depth):
            if fill_bottom <= extra_y < target_y:
                fill_positions.append((x, extra_y, z))

    return clear_positions, fill_positions


def positive_control_positions(cx: int, cz: int) -> Tuple[Position, Position]:
    """Two deep reads that prove the chunk is loaded at all.

    y=-60 is inside the world in every overworld chunk whether it is a cave
    or solid stone, so "unknown" there means "not loaded", full stop -- check
    this before spending the rest of the read budget.
    """
    probe_y = WORLD_MIN_Y + 4
    return (cx, probe_y, cz), (cx + 15, probe_y, cz + 15)


# --- report -------------------------------------------------------------------


@dataclass
class VerifyReport:
    verdict: str  # "pass" | "fail" | "unverified"
    reason_class: Optional[str] = None
    retryable: bool = True
    samples_checked: int = 0
    samples_failed: int = 0
    unknown_count: int = 0
    failures: List[Dict[str, Any]] = field(default_factory=list)
    first_bad: Optional[List[Any]] = None
    tolerated: Dict[str, int] = field(default_factory=dict)
    profile: str = "standard"
    attempt: int = 0
    chunk: Tuple[int, int] = (0, 0)

    @property
    def success(self) -> bool:
        return self.verdict == "pass"


def _read_block(client, position: Position) -> str:
    """Read one block, classified. Never raises -- a read failure classifies
    as "unknown", the same as an unloaded chunk; it is not evidence either
    way and must not consume a retry attempt (see terraform.py's retry loop).
    """
    try:
        response = client.transport.dispatch(
            "get_block",
            {"x": int(position[0]), "y": int(position[1]), "z": int(position[2])},
        )
        if not isinstance(response, dict):
            return "unknown"
        return classify_block(response.get("id"))
    except Exception:
        return "unknown"


def verify_chunk_flat(
    client,
    cx: int,
    cz: int,
    target_y: int,
    *,
    fill_block: str = "minecraft:stone",
    clear_margin: int = 48,
    fill_depth: int = 12,
    attempt: int = 0,
    profile: str = "standard",
    mode: str = "strict",
    bands: Tuple[str, ...] = ("clear", "fill"),
) -> VerifyReport:
    """Read the world and decide whether chunk (cx, cz) is flat at target_y.

    Standalone and read-only: terraforms nothing, so it is safe to call
    against a chunk already recorded done (mode="audit") without risking any
    placement. `cx, cz` must already be a floor-divided chunk origin -- pass
    exactly what terraform_chunk computed, never recompute.
    """
    clear_top = min(target_y + clear_margin, WORLD_MAX_Y)
    fill_bottom = max(target_y - fill_depth, WORLD_MIN_Y)

    report = VerifyReport(verdict="unverified", profile=profile, attempt=attempt, chunk=(cx, cz))

    control_a, control_b = positive_control_positions(cx, cz)
    for control in (control_a, control_b):
        name = _read_block(client, control)
        report.samples_checked += 1
        if name == "unknown":
            report.unknown_count += 1
            report.reason_class = "unloaded"
            report.retryable = True
            return report

    clear_positions, fill_positions = chunk_sample_positions(
        cx, cz, target_y, clear_top, fill_bottom, attempt=attempt, profile=profile
    )

    def check_band(stage: str, positions: Sequence[Position]) -> bool:
        """Returns False (and stops early) on the first permanent failure."""
        for position in positions:
            name = _read_block(client, position)
            report.samples_checked += 1
            if name == "unknown":
                report.unknown_count += 1
                continue
            if stage == "clear":
                verdict, reason = clear_verdict(name, mode=mode)
            else:
                def _below() -> str:
                    below_pos = (position[0], position[1] - 1, position[2])
                    try:
                        resp = client.transport.dispatch(
                            "get_block",
                            {"x": below_pos[0], "y": below_pos[1], "z": below_pos[2]},
                        )
                        return resp.get("id") if isinstance(resp, dict) else None
                    except Exception:
                        return None

                verdict, reason = fill_verdict(name, mode=mode, read_below=_below)

            if verdict == "pass":
                if mode == "audit" and stage == "clear" and _is_regrowth(name):
                    report.tolerated["regrowth"] = report.tolerated.get("regrowth", 0) + 1
                elif stage == "fill" and name not in AIR and name not in LIQUIDS and not _is_non_solid(name) and not _is_falling(name):
                    known = {"stone", "cobblestone", "dirt", "grass_block", "deepslate", "netherrack"}
                    if fill_block.split(":")[-1] not in (name,) and name not in known:
                        report.tolerated["unrecognised_fill_block"] = (
                            report.tolerated.get("unrecognised_fill_block", 0) + 1
                        )
                continue

            report.samples_failed += 1
            failure = {
                "position": list(position),
                "stage": stage,
                "expected": "air" if stage == "clear" else "solid",
                "observed": name,
            }
            report.failures.append(failure)
            if report.first_bad is None:
                report.first_bad = failure["position"] + [name]

            if verdict == "fail_permanent":
                report.reason_class = reason
                report.retryable = False
                return False

            if report.reason_class is None:
                report.reason_class = reason
        return True

    if "clear" in bands:
        if not check_band("clear", clear_positions):
            report.verdict = "fail"
            return report
    if "fill" in bands:
        if not check_band("fill", fill_positions):
            report.verdict = "fail"
            return report

    if report.samples_failed > 0:
        report.verdict = "fail"
        report.retryable = report.retryable and True
        return report

    report.verdict = "pass"
    report.reason_class = None
    report.retryable = False
    return report


# --- builder wait --------------------------------------------------------------


def _await_build_complete(
    client,
    timeout: float = 600.0,
    poll: float = 2.0,
    start_window: float = 15.0,
    settle_checks: int = 3,
) -> Dict[str, Any]:
    """Wait for Baritone's builder to finish, using build{action:"status"}
    (uncached) rather than get_state's is_pathing.

    is_pathing is demoted, not trusted: it is still accepted as a fallback
    when the bridge does not support the "build" route (older builds), but
    the returned dict records which signal was actually used
    (`wait_signal`), so a caller relying on the weaker evidence can see that
    it happened.

    Returns a dict: {"completed": bool, "started": bool, "wait_signal": str,
    "reason": str}. This function proves nothing about WHAT was built -- only
    that the builder process started and then stopped. verify_chunk_flat is
    the only source of truth for correctness.
    """
    start = time.time()
    started = False
    idle_streak = 0
    use_build_status = True

    while time.time() - start < timeout:
        if use_build_status:
            try:
                status = client.transport.dispatch("build", {"action": "status"})
            except Exception:
                use_build_status = False
                continue
            if not isinstance(status, dict) or "is_building" not in status:
                use_build_status = False
                continue
            is_active = bool(status.get("is_building"))
        else:
            try:
                state = client.transport.dispatch("get_state", {})
                is_active = bool(state.get("is_pathing", False))
            except Exception:
                idle_streak = 0
                time.sleep(poll)
                continue

        if is_active:
            started = True
            idle_streak = 0
        else:
            idle_streak += 1
            if idle_streak >= settle_checks:
                return {
                    "completed": True,
                    "started": started,
                    "wait_signal": "is_building" if use_build_status else "is_pathing",
                    "reason": "idle" if started else "never_started",
                }
            if not started and (time.time() - start) > start_window:
                return {
                    "completed": False,
                    "started": False,
                    "wait_signal": "is_building" if use_build_status else "is_pathing",
                    "reason": "builder_never_started",
                }

        time.sleep(poll)

    return {
        "completed": False,
        "started": started,
        "wait_signal": "is_building" if use_build_status else "is_pathing",
        "reason": "timeout",
    }
