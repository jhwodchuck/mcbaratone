from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict

from .http_api.client import BaritoneClient, BaritoneError
from .http_api.controller import BaritoneController
from .http_api.spec import load_endpoints


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CLI for the Baritone HTTP control API")
    parser.add_argument("--base-url", default=os.getenv("BARITONE_BASE_URL", "http://localhost:4567"), help="Baritone HTTP endpoint base URL")
    parser.add_argument("--token", default=os.getenv("BARITONE_TOKEN"), help="Bearer token for authentication")
    parser.add_argument("--dry-run", action="store_true", help="Log requests without sending them")

    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="Authenticate and print the returned token")
    login.add_argument("--username", required=True)
    login.add_argument("--password", required=True)

    sub.add_parser("status", help="Fetch current status")
    sub.add_parser("queue", help="Fetch task queue")
    sub.add_parser("pause", help="Pause current task")
    sub.add_parser("resume", help="Resume current task")
    sub.add_parser("stop", help="Stop current task")

    goto = sub.add_parser("goto", help="Move to a coordinate")
    goto.add_argument("--x", type=float, required=True)
    goto.add_argument("--y", type=float, required=True)
    goto.add_argument("--z", type=float, required=True)
    goto.add_argument("--dimension", default="overworld")

    setting_get = sub.add_parser("setting-get", help="Get a setting value")
    setting_get.add_argument("name")
    setting_set = sub.add_parser("setting-set", help="Update a setting")
    setting_set.add_argument("name")
    setting_set.add_argument("value")

    mine = sub.add_parser("mine", help="Mine blocks")
    mine.add_argument("blocks", nargs="+")
    mine.add_argument("--target-count", type=int)

    events = sub.add_parser("events", help="Poll events once")
    events.add_argument("--since-id")

    return parser


def _print(data: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(data, indent=2))
    sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    client = BaritoneClient(args.base_url, token=args.token, dry_run=args.dry_run)
    controller = BaritoneController(client, endpoints=load_endpoints())

    try:
        if args.command == "login":
            data = client.login(args.username, args.password)
        elif args.command == "status":
            data = client.status()
        elif args.command == "queue":
            data = client.queue()
        elif args.command == "goto":
            data = controller.go_to(args.x, args.y, args.z, dimension=args.dimension)
        elif args.command == "pause":
            data = controller.pause()
        elif args.command == "resume":
            data = controller.resume()
        elif args.command == "stop":
            data = controller.stop()
        elif args.command == "setting-get":
            data = controller.get_setting(args.name)
        elif args.command == "setting-set":
            data = controller.set_setting(args.name, _coerce_value(args.value))
        elif args.command == "mine":
            data = controller.mine(args.blocks, target_count=args.target_count)
        elif args.command == "events":
            data = client.events(args.since_id)
        else:
            parser.error(f"Unknown command {args.command}")
            return 2
    except BaritoneError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1

    _print(data if data is not None else {"ok": True})
    return 0


def _coerce_value(raw: str) -> Any:
    lowered = raw.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


if __name__ == "__main__":
    raise SystemExit(main())

