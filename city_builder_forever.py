"""
city_builder_forever.py - Continuous, unattended megabase city build-out.

Runs AFTER the main end-game mission (spawn_to_dragon.py) reaches
Phase COMPLETE. Connects to the same Baritone bridge and builds a functional
megabase city outward from a center point, one ring of districts at a time,
forever - flattening each district's footprint before building on it, and
saving progress after every district so it can be killed and resumed.

Like terraform_forever.py, this uses its own checkpoint file
(city_checkpoint.json) and does not touch spawn_to_dragon_checkpoint.json or
any of the StateManager/Phase machinery in automator/.

See plans/CITY_BUILD_PLAN.md for the full design.

Usage:
    python city_builder_forever.py [--host HOST] [--port PORT]
                                    [--center-x X] [--center-z Z] [--target-y Y]
                                    [--no-flatten] [--no-roads] [--rail]
                                    [--schematic ROLE=ID ...] [--force]

Examples:
    # Wait for the dragon to be defeated, then build a city outward from
    # wherever the bot currently is, forever:
    python city_builder_forever.py

    # Start immediately, centered on a specific point, building at y=70,
    # with rail laid along the roads:
    python city_builder_forever.py --force --center-x 0 --center-z 0 --target-y 70 --rail

    # Use a schematic for the industrial district instead of the procedural shell:
    python city_builder_forever.py --schematic industrial=super_smelter_v1.schem
"""

import argparse
import json
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from baritone_client import Client, TcpTransport
from baritone_client.common import city as city_mod
from baritone_client.common.city import build_ring, role_for, district_ring_offsets

CHECKPOINT_FILE = "city_checkpoint.json"
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
    return load_json(MISSION_CHECKPOINT_FILE).get("phase") == "COMPLETE"


def get_player_position(client):
    state = client.transport.dispatch("get_state", {})
    pos = state.get("block_position", state.get("position", {}))
    return int(pos.get("x", 0)), int(pos.get("y", 64)), int(pos.get("z", 0))


def main():
    parser = argparse.ArgumentParser(description="Unattended megabase city build-out.")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=5555)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--center-x", type=int, default=None)
    parser.add_argument("--center-z", type=int, default=None)
    parser.add_argument("--target-y", type=int, default=None)
    parser.add_argument("--ring-step", type=int, default=1)
    parser.add_argument("--no-flatten", action="store_true",
                        help="Skip terraforming each district footprint (assume already flat)")
    parser.add_argument("--no-roads", action="store_true", help="Skip paving road margins")
    parser.add_argument("--rail", action="store_true", help="Best-effort rail along road centerlines")
    parser.add_argument("--build-timeout", type=float, default=600.0,
                        help="Max seconds to wait for each fill/build operation")
    parser.add_argument("--schematic", action="append", default=[], metavar="ROLE=ID",
                        help="Register a schematic for a district role (repeatable)")
    parser.add_argument("--force", action="store_true",
                        help="Start even if the endgame mission hasn't reached COMPLETE")
    args = parser.parse_args()

    if not args.force and not mission_is_complete():
        print("The endgame mission checkpoint isn't at COMPLETE yet")
        print(f"  (checked {MISSION_CHECKPOINT_FILE}).")
        print("Run spawn_to_dragon.py first, or pass --force to build anyway.")
        sys.exit(1)

    # Register any schematic overrides for district roles.
    for entry in args.schematic:
        if "=" not in entry:
            print(f"Ignoring malformed --schematic '{entry}' (expected ROLE=ID)")
            continue
        role, schematic_id = entry.split("=", 1)
        city_mod.SCHEMATIC_REGISTRY[role.strip()] = schematic_id.strip()
        print(f"Registered schematic for role '{role.strip()}': {schematic_id.strip()}")

    print(f"Connecting to Baritone Bridge at {args.host}:{args.port}...")
    transport = TcpTransport(host=args.host, port=args.port, timeout=args.timeout)
    client = Client(transport)

    progress = load_json(CHECKPOINT_FILE)

    px, py, pz = (None, None, None)
    if args.center_x is None or args.center_z is None or args.target_y is None:
        px, py, pz = get_player_position(client)

    center_x = args.center_x if args.center_x is not None else progress.get("center_x", px)
    center_z = args.center_z if args.center_z is not None else progress.get("center_z", pz)
    target_y = args.target_y if args.target_y is not None else progress.get("target_y", py)

    progress.setdefault("center_x", center_x)
    progress.setdefault("center_z", center_z)
    progress.setdefault("target_y", target_y)
    ring = progress.get("ring", 0)
    ring_progress = progress.setdefault("ring_progress", {})

    print(f"Building megabase city from ({center_x}, {target_y}, {center_z}), starting at ring {ring}")
    print(f"  district size: {city_mod.DISTRICT_SIZE}x{city_mod.DISTRICT_SIZE} blocks "
          f"({city_mod.DISTRICT_CHUNKS}x{city_mod.DISTRICT_CHUNKS} chunks)")
    print(f"  flatten={not args.no_flatten}  roads={not args.no_roads}  rail={args.rail}")
    print("Runs forever, expanding outward one ring at a time. Ctrl+C to stop (progress is saved).")

    def on_district_done(done, total, data):
        save_json(CHECKPOINT_FILE, progress)
        print(f"  ring {ring}: district {done}/{total} "
              f"role={data.get('role')} origin={data.get('origin')}")

    try:
        while True:
            result = build_ring(
                client,
                center_x, center_z, target_y,
                ring=ring,
                flatten=not args.no_flatten,
                with_roads=not args.no_roads,
                with_rail=args.rail,
                progress=ring_progress,
                on_district_done=on_district_done,
                timeout=args.build_timeout,
            )
            print(f">>> Ring {ring} complete: {result.reason}")

            ring += args.ring_step
            progress["ring"] = ring
            ring_progress = {}
            progress["ring_progress"] = ring_progress
            save_json(CHECKPOINT_FILE, progress)
    except KeyboardInterrupt:
        print("\nStopping city build-out (progress saved - re-run this script to resume).")
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
