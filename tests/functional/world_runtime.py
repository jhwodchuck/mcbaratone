"""Probe and administer the local disposable functional-test runtimes."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from baritone_client import TcpTransport  # noqa: E402

from world_safety import validate_disposable_world  # noqa: E402


def _block_id(response: object) -> str:
    if not isinstance(response, dict):
        return ""
    payload = response.get("data", response)
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("id") or payload.get("block") or "")


def verify_bridge(port: int, expected_server: str, require_admin: bool) -> dict:
    """Verify player readiness, world identity, and optional command authority."""
    transport = TcpTransport(host="localhost", port=port, timeout=8.0)
    try:
        state = transport.dispatch("get_state", {})
        if not isinstance(state, dict):
            raise RuntimeError("bridge returned a non-object state")
        normalized_server = validate_disposable_world(expected_server, state)
        position = state.get("block_position")
        if not isinstance(position, dict):
            raise RuntimeError("bridge is listening but the player is not ready")

        if require_admin:
            marker = {
                "x": int(position["x"]),
                "y": int(position["y"]) - 1,
                "z": int(position["z"]),
            }
            transport.dispatch(
                "chat",
                {
                    "message": (
                        f"/setblock {marker['x']} {marker['y']} {marker['z']} "
                        "minecraft:bedrock"
                    )
                },
            )
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                block = transport.dispatch("get_block", marker)
                if _block_id(block) == "minecraft:bedrock":
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError(
                    "test player lacks command authority; arena marker was not created"
                )

        return {
            "bridge_port": port,
            "server": normalized_server,
            "dimension": state.get("dimension"),
            "position": position,
            "health": state.get("health"),
            "food": state.get("food_level"),
            "dead": state.get("is_dead"),
            "admin_verified": require_admin,
        }
    finally:
        transport.shutdown()


def stop_server(port: int, password: str) -> None:
    """Ask a disposable server to save and stop through its private RCON port."""
    from scripts.maintenance.locate_biome_atlas import RconClient

    with RconClient("127.0.0.1", port, password, 5.0) as rcon:
        rcon.command("save-all flush")
        rcon.command("stop")


#: Hostile spawning is a GAMERULE in 26.2, not a server.properties key.
#: `spawn-monsters` appears in zero of the 16804 entries of the 26.2 server
#: jar, so writing it into server.properties is silently inert -- dragon-a ran
#: a supposedly peaceful world and still died 16 times (Zombie x8, Skeleton x4,
#: Spider x2, Pillager x1, drowned x1). The jar's own GameRuleRegistryFix maps
#: the legacy `spawnMonsters` to `minecraft:spawn_monsters`, which is the id
#: this must use. `spawn_patrols` covers the pillager that killed it once.
PEACEFUL_GAMERULES = ("spawn_monsters", "spawn_patrols")


def apply_gamerules(port: int, password: str, rules: dict[str, str]) -> dict:
    """Set gamerules over RCON and read each one back to prove it applied.

    An unknown rule id fails as an unrecognised command rather than raising,
    so the read-back is the only thing that distinguishes "applied" from
    "silently ignored" -- which is exactly how the inert server.properties key
    went unnoticed.
    """
    from scripts.maintenance.locate_biome_atlas import RconClient

    applied: dict[str, str] = {}
    with RconClient("127.0.0.1", port, password, 5.0) as rcon:
        for rule, value in rules.items():
            rcon.command(f"gamerule {rule} {value}")
            applied[rule] = str(rcon.command(f"gamerule {rule}") or "").strip()
    mismatched = {
        rule: applied.get(rule, "")
        for rule, value in rules.items()
        if str(value).lower() not in applied.get(rule, "").lower()
    }
    return {"applied": applied, "mismatched": mismatched, "ok": not mismatched}


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--port", type=int, required=True)
    verify.add_argument("--expect-server", required=True)
    verify.add_argument("--require-admin", action="store_true")

    stop = subparsers.add_parser("stop-server")
    stop.add_argument("--rcon-port", type=int, required=True)
    stop.add_argument("--password", required=True)

    peaceful = subparsers.add_parser("apply-peaceful")
    peaceful.add_argument("--rcon-port", type=int, required=True)
    peaceful.add_argument("--password", required=True)

    args = parser.parse_args()
    if args.command == "verify":
        print(
            json.dumps(
                verify_bridge(args.port, args.expect_server, args.require_admin),
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "apply-peaceful":
        result = apply_gamerules(
            args.rcon_port,
            args.password,
            {rule: "false" for rule in PEACEFUL_GAMERULES},
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["ok"] else 1
    stop_server(args.rcon_port, args.password)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
