"""Assign missing specialty duties across the controllers that are live now.

Primary roles remain durable in ``fleet-role.txt``.  Coverage is deliberately
ephemeral: when a dedicated worker is stopped, one of the surviving bots may
run a bounded specialty cycle; when that worker returns, the borrowed duty
disappears automatically.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .end_readiness import FleetRole, bot_name, default_fleet_role


FLEET_DIRECTORY_NAMES = {"aternos", "headlessmc"}
ACTIVE_HEARTBEAT_SECONDS = 90.0
SPECIALTY_ROLES = (
    FleetRole.QUARTERMASTER,
    FleetRole.VILLAGE_FOOD,
    FleetRole.WOOD_SUPPLY,
    FleetRole.IRON_SUPPLY,
    FleetRole.NETHER_SUPPLY,
    FleetRole.ENCHANTING,
    FleetRole.END_RUNNER,
)


def _last_json_line(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            end = stream.tell()
            start = max(0, end - 16384)
            stream.seek(start)
            lines = stream.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    for line in reversed(lines):
        try:
            value = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return {}


def _controller_role(controller: Path) -> FleetRole:
    try:
        configured = (controller / "fleet-role.txt").read_text(
            encoding="utf-8"
        ).strip().lower()
        if configured:
            return FleetRole(configured)
    except (OSError, ValueError):
        pass
    return default_fleet_role(controller.parent.name)


def _controller_is_active(
    controller: Path,
    *,
    current: Path,
    now: float,
) -> bool:
    if controller.resolve() == current.resolve():
        return True
    heartbeat_path = controller / "monitor" / "heartbeat.jsonl"
    try:
        if now - heartbeat_path.stat().st_mtime > ACTIVE_HEARTBEAT_SECONDS:
            return False
    except OSError:
        return False
    heartbeat = _last_json_line(heartbeat_path)
    try:
        health = float(heartbeat.get("health", 0) or 0)
        food = float(heartbeat.get("food", 0) or 0)
    except (TypeError, ValueError):
        return False
    return bool(
        not heartbeat.get("dead", False)
        and heartbeat.get("bridge") != "unreachable"
        and health >= 12
        and food >= 6
    )


def borrowed_specialty_roles(
    state: Any,
    primary_role: FleetRole,
    *,
    now: float | None = None,
) -> tuple[FleetRole, ...]:
    """Return missing specialties assigned to this currently running bot.

    Assignments are deterministic for one active-roster snapshot.  This keeps
    a reduced fleet useful without rewriting any bot's durable primary role.
    """
    checkpoint_dir = getattr(state, "checkpoint_dir", None)
    if not checkpoint_dir:
        return ()
    current = Path(checkpoint_dir).resolve()
    if current.name.lower() != "controller":
        return ()
    fleet_root = current.parent.parent
    if fleet_root.name.lower() not in FLEET_DIRECTORY_NAMES:
        return ()

    current_name = bot_name(state)
    if not current_name:
        return ()
    observed = time.time() if now is None else float(now)
    active: list[tuple[str, FleetRole]] = []
    try:
        controllers = sorted(
            (
                bot_dir / "controller"
                for bot_dir in fleet_root.iterdir()
                if bot_dir.is_dir()
                and re.fullmatch(r"Bot\d{2}", bot_dir.name, re.IGNORECASE)
                and (bot_dir / "controller").is_dir()
            ),
            key=lambda path: path.parent.name.lower(),
        )
    except OSError:
        return ()
    for controller in controllers:
        if _controller_is_active(controller, current=current, now=observed):
            active.append((controller.parent.name, _controller_role(controller)))

    if not active:
        active = [(current_name, primary_role)]
    active_names = [name.lower() for name, _role in active]
    try:
        current_index = active_names.index(current_name.lower())
    except ValueError:
        return ()
    staffed = {role for _name, role in active}
    missing = [role for role in SPECIALTY_ROLES if role not in staffed]
    assigned = [
        role
        for index, role in enumerate(missing)
        if index % len(active) == current_index
    ]
    return tuple(assigned)


__all__ = [
    "ACTIVE_HEARTBEAT_SECONDS",
    "SPECIALTY_ROLES",
    "borrowed_specialty_roles",
]
