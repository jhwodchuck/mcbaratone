"""
terraform_forever.py - Continuous, unattended world terraforming.

Meant to run AFTER the main end-game mission has reached COMPLETE (Ender
Dragon defeated). It connects to the same Baritone bridge and flattens the
world outward from a center point, one ring of chunks at a time, forever -
saving progress after every chunk so it can be killed and resumed at any
point.

This intentionally uses its own checkpoint file (terraform_checkpoint.json)
and does not touch the mission's own checkpoint or any of the
StateManager/Phase machinery in automator/ - it only reads mission
checkpoints (read-only) to decide whether it's safe to start.

Usage:
    python terraform_forever.py [--host HOST] [--port PORT]
                                 [--center-x X] [--center-z Z] [--target-y Y]
                                 [--ring-step CHUNKS] [--fill-block BLOCK]
                                 [--clear-margin N] [--fill-depth N]
                                 [--sweep-failed-every N] [--mission-checkpoint PATH]
                                 [--force]

Examples:
    # Wait for the dragon to be defeated, then terraform outward from wherever
    # the bot currently is, forever:
    python terraform_forever.py

    # Start immediately regardless of mission phase, centered on a specific
    # point, leveling everything to y=70:
    python terraform_forever.py --force --center-x 0 --center-z 0 --target-y 70
"""

import argparse
import glob
import json
import os
import sys
import time

sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from baritone_client import Client, TcpTransport
from baritone_client.common.terraform import terraform_ring
from baritone_client.common.terraform_ledger import migrate_ledger

CHECKPOINT_FILE = "terraform_checkpoint.json"
MISSION_CHECKPOINT_FILE = "spawn_to_dragon_checkpoint.json"
# Live-ops runners (e.g. a coordinated multi-bot dragon fight) keep their own
# checkpoint outside the single-bot campaign path above. The dragon that
# gates this script has been killed from there before, with the campaign
# checkpoint left stale at an earlier phase -- so `mission_is_complete`
# checks both rather than trusting one fixed path.
MISSION_CHECKPOINT_GLOBS = (
    "runs/live_ops/*/checkpoint.json",
    "runs/**/spawn_to_dragon_checkpoint.json",
)
# Backing off after a circuit-breaker abort so a systemic failure (no
# materials, bot dead, bridge down) doesn't hot-loop retrying the same ring.
BACKOFF_SECONDS = 60.0
BACKOFF_MAX_SECONDS = 900.0


def load_json(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_json(path: str, data: dict) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# Neither checkpoint schema records which bridge host/port produced it, so a
# COMPLETE checkpoint found on disk cannot be trusted on its phase string
# alone -- it could belong to any world, not the one currently at --host/
# --port. A fresh spawn on a different seed is essentially always far from
# another fresh spawn, so correlating by (dimension, distance) against the
# LIVE bridge's current position is a cheap, effective proxy for "is this
# actually the same save". Generous on purpose: the bot may have travelled
# thousands of blocks since the checkpoint was written.
CORRELATION_RADIUS_BLOCKS = 5000.0


def _checkpoint_position(data: dict):
    """Extract (dimension, x, y, z) from either checkpoint schema, or None."""
    pos = data.get("position")  # campaign (spawn_to_dragon) schema
    if isinstance(pos, dict) and "x" in pos:
        dimension = data.get("world_identity") or data.get("custom_data", {}).get("dimension")
        return (str(dimension), pos.get("x", 0), pos.get("y", 0), pos.get("z", 0))
    snapshot = data.get("leader_snapshot")  # live-ops (family_expedition) schema
    if isinstance(snapshot, dict) and isinstance(snapshot.get("position"), dict):
        return (
            str(snapshot.get("dimension")),
            snapshot["position"].get("x", 0),
            snapshot["position"].get("y", 0),
            snapshot["position"].get("z", 0),
        )
    return None


def mission_is_complete(explicit_path: str = None, live_position=None) -> bool:
    """
    live_position, when given as (dimension, x, y, z) read from the bridge
    terraform_forever is actually about to terraform, correlates a candidate
    checkpoint to that specific world instead of trusting "COMPLETE exists
    somewhere in the repo" -- see CORRELATION_RADIUS_BLOCKS. Without it (the
    offline pre-check before a bridge connection exists), any COMPLETE
    checkpoint passes, same as before; callers that can supply live_position
    should always do so before allowing the run to actually start.
    """
    candidates = [explicit_path] if explicit_path else [MISSION_CHECKPOINT_FILE]
    if not explicit_path:
        for pattern in MISSION_CHECKPOINT_GLOBS:
            candidates.extend(glob.glob(pattern, recursive=True))
    for path in candidates:
        if not path:
            continue
        data = load_json(path)
        if data.get("phase") != "COMPLETE":
            continue
        if live_position is None:
            return True
        checkpoint_position = _checkpoint_position(data)
        if checkpoint_position is None:
            continue  # cannot correlate; do not trust it blindly
        c_dim, c_x, _c_y, c_z = checkpoint_position
        l_dim, l_x, _l_y, l_z = live_position
        if str(c_dim) != str(l_dim):
            continue
        try:
            distance = ((float(c_x) - float(l_x)) ** 2 + (float(c_z) - float(l_z)) ** 2) ** 0.5
        except (TypeError, ValueError):
            continue
        if distance <= CORRELATION_RADIUS_BLOCKS:
            return True
    return False


def get_player_state(client):
    """(dimension, x, y, z) from one get_state call."""
    state = client.transport.dispatch("get_state", {})
    pos = state.get("block_position", state.get("position", {}))
    dimension = state.get("dimension", "minecraft:overworld")
    return dimension, int(pos.get("x", 0)), int(pos.get("y", 64)), int(pos.get("z", 0))


def get_player_position(client):
    _dimension, x, y, z = get_player_state(client)
    return x, y, z


def main():
    parser = argparse.ArgumentParser(
        description="Unattended, ever-expanding world terraforming.",
    )
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=5555)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--center-x", type=int, default=None)
    parser.add_argument("--center-z", type=int, default=None)
    parser.add_argument("--target-y", type=int, default=None)
    parser.add_argument("--ring-step", type=int, default=1, help="How many chunk-rings to advance per lap")
    parser.add_argument("--fill-block", default="minecraft:stone")
    parser.add_argument("--clear-margin", type=int, default=48)
    parser.add_argument("--fill-depth", type=int, default=12)
    parser.add_argument(
        "--sweep-failed-every", type=int, default=10,
        help="Every N rings, re-attempt every failed/unverified chunk accumulated so far "
             "(0 disables the periodic sweep)",
    )
    parser.add_argument(
        "--mission-checkpoint", default=None,
        help="Explicit path to the checkpoint that must show phase=COMPLETE. "
             "Default: search the campaign checkpoint plus runs/live_ops/*/checkpoint.json.",
    )
    parser.add_argument("--force", action="store_true", help="Start even if the endgame mission hasn't reached COMPLETE")
    args = parser.parse_args()

    if not args.force and not mission_is_complete(args.mission_checkpoint):
        print("No checkpoint shows the endgame mission at COMPLETE yet.")
        print(f"  (checked {args.mission_checkpoint or MISSION_CHECKPOINT_FILE!r} "
              f"and {', '.join(MISSION_CHECKPOINT_GLOBS)}).")
        print("Run the mission first, or pass --force to terraform anyway.")
        sys.exit(1)

    print(f"Connecting to Baritone Bridge at {args.host}:{args.port}...")
    transport = TcpTransport(host=args.host, port=args.port, timeout=args.timeout)
    client = Client(transport)

    if not args.force:
        # The offline pre-check above only proved a COMPLETE checkpoint
        # exists SOMEWHERE in the repo -- not that it belongs to the world at
        # --host/--port. Now that a bridge is connected, correlate for real:
        # neither checkpoint schema records the host/port it was run
        # against, so trusting phase=="COMPLETE" alone would let a finished
        # run on one bot authorize terraforming a different, still-in-
        # progress bot's world. Reproduced live in this repo's own runs/
        # tree: family_expedition/checkpoint.json shows COMPLETE while
        # several bot checkpoints sit at BOOT_SEQUENCE/BASE_CONSTRUCTION.
        dimension, live_x, live_y, live_z = get_player_state(client)
        if not mission_is_complete(args.mission_checkpoint, live_position=(dimension, live_x, live_y, live_z)):
            print(
                f"A COMPLETE checkpoint exists, but none is within "
                f"{CORRELATION_RADIUS_BLOCKS:.0f} blocks of the bot currently "
                f"connected at {args.host}:{args.port} "
                f"({dimension} ({live_x}, {live_y}, {live_z}))."
            )
            print(
                "That checkpoint likely belongs to a different world/bot. "
                "Pass --mission-checkpoint to point at the right one explicitly, "
                "or --force if this really is the completed world."
            )
            try:
                client.shutdown()
            except Exception:
                pass
            sys.exit(1)

    progress = load_json(CHECKPOINT_FILE)

    if args.center_x is not None and args.center_z is not None and args.target_y is not None:
        center_x, center_z, target_y = args.center_x, args.center_z, args.target_y
    else:
        px, py, pz = get_player_position(client)
        center_x = args.center_x if args.center_x is not None else progress.get("center_x", px)
        center_z = args.center_z if args.center_z is not None else progress.get("center_z", pz)
        target_y = args.target_y if args.target_y is not None else progress.get("target_y", py)

    progress.setdefault("center_x", center_x)
    progress.setdefault("center_z", center_z)
    progress.setdefault("target_y", target_y)
    ring = progress.get("ring", 0)
    ring_progress = progress.setdefault("ring_progress", {})
    # Hoisted at the TOP level of the checkpoint, not inside ring_progress:
    # ring_progress is reset every lap below, and chunk_ring_offsets
    # guarantees each ring's chunks are disjoint from every other ring's, so
    # one shared ledger across the whole unbounded run never collides and
    # lets a periodic sweep find failures from rings already passed.
    ledger = migrate_ledger(progress)
    consecutive_aborts = 0

    print(f"Terraforming outward from ({center_x}, {target_y}, {center_z}), starting at ring {ring}")
    print("This runs forever, expanding outward one ring at a time. Ctrl+C to stop (progress is saved).")

    def on_chunk_done(done, total, chunk_coord):
        save_json(CHECKPOINT_FILE, progress)
        print(f"  ring {ring}: chunk {done}/{total} -> {chunk_coord}")

    def sweep_failed_ledger() -> bool:
        """Re-attempt every accumulated failed/unverified chunk, wherever its
        ring was. Not bounded to the current ring's offsets -- this is the
        only mechanism that revisits a chunk from a ring already passed.

        Returns True if the circuit breaker tripped mid-sweep. Mirrors the
        main loop's own abort handling below: sweep_pass's return value must
        be checked, not discarded -- the same bug the main loop was fixed
        for previously (result.success never checked before advancing) can
        recur at any call site that ignores what sweep_pass reports. Without
        this, a bot that died on chunk 1 of 40 stale chunks would silently
        leave the other 39 untouched and the run would carry on into the
        next ring against a still-dead bot.
        """
        stale = [
            tuple(int(part) for part in key.split(","))
            for key, entry in ledger.items()
            if entry.get("status") in ("failed", "unverified")
            and entry.get("retryable") is not False
        ]
        if not stale:
            print("  sweep: no accumulated failures to retry")
            return False
        print(f"  sweep: re-attempting {len(stale)} accumulated failed/unverified chunk(s)")
        from baritone_client.common.terraform import sweep_pass, CircuitBreaker
        breaker = CircuitBreaker()
        abort = sweep_pass(
            client, stale, target_y,
            fill_block=args.fill_block, clear_margin=args.clear_margin, fill_depth=args.fill_depth,
            progress=progress, ledger=ledger, on_chunk_done=None,
            breaker=breaker, index_key="_sweep_all_index",
        )
        save_json(CHECKPOINT_FILE, progress)
        if abort is not None:
            reason_class = (abort.data or {}).get("reason_class", "unknown")
            print(f"  sweep aborted ({reason_class}): {abort.reason}")
            progress["last_error"] = abort.reason
            save_json(CHECKPOINT_FILE, progress)
            return True
        return False

    try:
        while True:
            result = terraform_ring(
                client,
                center_x, center_z, target_y,
                ring=ring,
                fill_block=args.fill_block,
                clear_margin=args.clear_margin,
                fill_depth=args.fill_depth,
                progress=ring_progress,
                on_chunk_done=on_chunk_done,
                ledger=ledger,
            )
            aborted = bool(result.data and result.data.get("abort_scope") == "lap")

            if aborted:
                # Systemic failure (no materials, bot dead, bridge down): the
                # SAME ring is retried, not skipped, and ring_progress is
                # deliberately NOT reset -- advancing would just spread the
                # same failure across more chunks, and result.success was
                # never checked here before, which is how one bad chunk used
                # to make the runner silently discard the entire rest of the
                # ring.
                consecutive_aborts += 1
                backoff = min(BACKOFF_SECONDS * consecutive_aborts, BACKOFF_MAX_SECONDS)
                reason_class = result.data.get("reason_class", "unknown")
                print(f"!!! Ring {ring} aborted ({reason_class}): {result.reason}")
                print(f"    Backing off {backoff:.0f}s before retrying the same ring...")
                progress["last_error"] = result.reason
                save_json(CHECKPOINT_FILE, progress)
                time.sleep(backoff)
                continue

            consecutive_aborts = 0
            if result.success:
                print(f">>> Ring {ring} complete: {result.reason}")
            else:
                incomplete = progress.setdefault("rings_incomplete", [])
                if ring not in incomplete:
                    incomplete.append(ring)
                print(f"!!! Ring {ring} swept but incomplete: {result.reason}")
                print("    Continuing outward; accumulated failures are retried via --sweep-failed-every.")

            ring += args.ring_step
            progress["ring"] = ring
            ring_progress = {}
            progress["ring_progress"] = ring_progress
            save_json(CHECKPOINT_FILE, progress)

            if args.sweep_failed_every > 0 and ring % args.sweep_failed_every == 0:
                if sweep_failed_ledger():
                    # Same treatment as a ring-level abort: back off before
                    # touching the world again rather than plowing straight
                    # into the next ring against what may still be a dead or
                    # unresponsive bot.
                    consecutive_aborts += 1
                    backoff = min(BACKOFF_SECONDS * consecutive_aborts, BACKOFF_MAX_SECONDS)
                    print(f"    Backing off {backoff:.0f}s after the sweep aborted...")
                    time.sleep(backoff)
    except KeyboardInterrupt:
        print("\nStopping terraforming (progress saved - re-run this script to resume).")
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        save_json(CHECKPOINT_FILE, progress)
        try:
            client.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    main()
