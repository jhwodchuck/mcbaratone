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
from .role_profile import load_role_profile


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


def _controller_bot_name(controller: Path) -> str:
    return (
        controller.parent.name
        if controller.name.lower() == "controller"
        else controller.name
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
    fallback = default_fleet_role(_controller_bot_name(controller))
    try:
        profile = load_role_profile(
            controller,
            primary_role=fallback.value,
            env={},
        )
        return FleetRole(profile.primary_role)
    except ValueError:
        try:
            configured = (controller / "fleet-role.txt").read_text(
                encoding="utf-8"
            ).strip().lower()
            if configured:
                return FleetRole(configured)
        except (OSError, ValueError):
            pass
    return fallback


def _configured_controller_specialties(
    controller: Path,
    primary_role: FleetRole,
) -> tuple[FleetRole, ...]:
    """Return recognized recurring secondary roles from a durable profile."""
    profile = load_role_profile(
        controller,
        primary_role=primary_role.value,
        env={},
    )
    roles: list[FleetRole] = []
    for name in profile.secondary_roles:
        try:
            role = FleetRole(name)
        except ValueError:
            continue
        if role not in {FleetRole.BALANCED, primary_role} and role not in roles:
            roles.append(role)
    return tuple(roles)


def configured_specialty_roles(
    state: Any,
    primary_role: FleetRole,
) -> tuple[FleetRole, ...]:
    """Return this controller's explicit recurring secondary specialties."""
    checkpoint_dir = getattr(state, "checkpoint_dir", None)
    if not checkpoint_dir:
        return ()
    return _configured_controller_specialties(Path(checkpoint_dir), primary_role)


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
    if current.name.lower() == "controller":
        fleet_root = current.parent.parent
    elif re.fullmatch(r"Bot\d{2}", current.name, re.IGNORECASE):
        fleet_root = current.parent
    else:
        return ()
    if fleet_root.name.lower() not in FLEET_DIRECTORY_NAMES:
        return ()

    current_name = bot_name(state)
    if not current_name:
        return ()
    observed = time.time() if now is None else float(now)
    active: list[tuple[str, FleetRole, tuple[FleetRole, ...]]] = []
    try:
        controllers = sorted(
            (
                bot_dir / "controller"
                if (bot_dir / "controller").is_dir()
                else bot_dir
                for bot_dir in fleet_root.iterdir()
                if bot_dir.is_dir()
                and re.fullmatch(r"Bot\d{2}", bot_dir.name, re.IGNORECASE)
            ),
            key=lambda path: _controller_bot_name(path).lower(),
        )
    except OSError:
        return ()
    for controller in controllers:
        if _controller_is_active(controller, current=current, now=observed):
            controller_role = _controller_role(controller)
            active.append(
                (
                    _controller_bot_name(controller),
                    controller_role,
                    _configured_controller_specialties(controller, controller_role),
                )
            )

    if not active:
        active = [
            (
                current_name,
                primary_role,
                configured_specialty_roles(state, primary_role),
            )
        ]
    active_names = [name.lower() for name, _role, _secondary in active]
    try:
        current_index = active_names.index(current_name.lower())
    except ValueError:
        return ()
    staffed = {
        role
        for _name, primary, secondary in active
        for role in (primary, *secondary)
    }
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
    "configured_specialty_roles",
]
