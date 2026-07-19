"""
terraform_forever.py - Continuous, unattended world terraforming.

Meant to run AFTER the main end-game mission (spawn_to_dragon.py) has reached
Phase COMPLETE (Ender Dragon defeated). It connects to the same Baritone
bridge and flattens the world outward from a center point, one ring of
chunks at a time, forever - saving progress after every chunk so it can be
killed and resumed at any point.

This intentionally uses its own checkpoint file (terraform_checkpoint.json)
and does not touch spawn_to_dragon_checkpoint.json or any of the
StateManager/Phase machinery in automator/ - it only reads the mission
checkpoint (read-only) to decide whether it's safe to start.

Usage:
    python terraform_forever.py [--host HOST] [--port PORT]
                                 [--center-x X] [--center-z Z] [--target-y Y]
                                 [--ring-step CHUNKS] [--fill-block BLOCK]
                                 [--clear-margin N] [--fill-depth N]
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
import json
import os
import sys
import time

sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from baritone_client import Client, TcpTransport
from baritone_client.common.terraform import terraform_ring

CHECKPOINT_FILE = "terraform_checkpoint.json"
MISSION_CHECKPOINT_FILE = "spawn_to_dragon_checkpoint.json"


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


def mission_is_complete() -> bool:
    data = load_json(MISSION_CHECKPOINT_FILE)
    return data.get("phase") == "COMPLETE"


def get_player_position(client):
    state = client.transport.dispatch("get_state", {})
    pos = state.get("block_position", state.get("position", {}))
    return int(pos.get("x", 0)), int(pos.get("y", 64)), int(pos.get("z", 0))


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
    parser.add_argument("--force", action="store_true", help="Start even if the endgame mission hasn't reached COMPLETE")
    args = parser.parse_args()

    if not args.force and not mission_is_complete():
        print("The endgame mission checkpoint isn't at COMPLETE yet")
        print(f"  (checked {MISSION_CHECKPOINT_FILE}).")
        print("Run spawn_to_dragon.py first, or pass --force to terraform anyway.")
        sys.exit(1)

    print(f"Connecting to Baritone Bridge at {args.host}:{args.port}...")
    transport = TcpTransport(host=args.host, port=args.port, timeout=args.timeout)
    client = Client(transport)

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

    print(f"Terraforming outward from ({center_x}, {target_y}, {center_z}), starting at ring {ring}")
    print("This runs forever, expanding outward one ring at a time. Ctrl+C to stop (progress is saved).")

    def on_chunk_done(done, total, chunk_coord):
        save_json(CHECKPOINT_FILE, progress)
        print(f"  ring {ring}: chunk {done}/{total} -> {chunk_coord}")

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
            )
            print(f">>> Ring {ring} complete: {result.reason}")

            ring += args.ring_step
            progress["ring"] = ring
            ring_progress = {}
            progress["ring_progress"] = ring_progress
            save_json(CHECKPOINT_FILE, progress)
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
