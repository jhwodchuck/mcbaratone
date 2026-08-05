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
            transport.dispatch(
                "chat",
                {"message": "/setblock 0 99 0 minecraft:bedrock"},
            )
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                block = transport.dispatch(
                    "get_block", {"x": 0, "y": 99, "z": 0}
                )
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
    stop_server(args.rcon_port, args.password)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
