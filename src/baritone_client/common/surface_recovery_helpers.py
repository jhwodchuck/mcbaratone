"""Small bounded helpers shared by surface recovery entry points."""

from __future__ import annotations

import time
from typing import Callable, Optional

from .movement_recovery import block_position


def _get_block_bounded(
    client: object,
    payload: dict,
    *,
    deadline: Optional[float],
    clock: Callable[[], float],
) -> object:
    """Read one block without allowing a loaded-column scan to overrun."""
    if deadline is None:
        return client.transport.dispatch("get_block", payload)
    remaining = max(0.0, float(deadline) - clock())
    try:
        return client.transport.dispatch("get_block", payload, timeout=remaining)
    except TypeError:
        # Compatibility for simple transports/test doubles without timeout.
        return client.transport.dispatch("get_block", payload)


def _state_has_breathing_air(state: object) -> bool:
    """Use oxygen telemetry as an additional gate when the bridge exposes it."""
    if not isinstance(state, dict):
        return False
    if state.get("eyes_in_water") is True:
        return False
    for key in ("oxygen", "air", "air_supply", "air_level"):
        if key not in state:
            continue
        try:
            return float(state[key]) > 0
        except (TypeError, ValueError):
            return False
    return True


def _loaded_breathing_level_above(
    client: object,
    position: tuple[int, int, int],
    *,
    scan_height: int = 32,
    deadline: Optional[float] = None,
    clock: Callable[[], float] = time.monotonic,
) -> Optional[int]:
    """Return the feet Y just below loaded breathing air in this column."""
    x, y, z = position
    for head_y in range(y + 1, y + max(1, int(scan_height)) + 1):
        if deadline is not None and clock() >= deadline:
            return None
        try:
            block = _get_block_bounded(
                client, {"x": x, "y": head_y, "z": z},
                deadline=deadline, clock=clock,
            ).get("id", "")
        except Exception:
            return None
        if _block_is_breathable(block):
            return head_y - 1
    return None


def _loaded_two_block_air_level_above(
    client: object,
    position: tuple[int, int, int],
    *,
    scan_height: int = 32,
    deadline: Optional[float] = None,
    clock: Callable[[], float] = time.monotonic,
) -> Optional[int]:
    """Return the first loaded feet level with open feet and head blocks."""
    x, y, z = position
    for feet_y in range(y + 1, y + max(2, int(scan_height))):
        if deadline is not None and clock() >= deadline:
            return None
        try:
            feet = _get_block_bounded(
                client, {"x": x, "y": feet_y, "z": z},
                deadline=deadline, clock=clock,
            ).get("id", "")
            if deadline is not None and clock() >= deadline:
                return None
            head = _get_block_bounded(
                client, {"x": x, "y": feet_y + 1, "z": z},
                deadline=deadline, clock=clock,
            ).get("id", "")
        except Exception:
            return None
        if (
            str(feet).split(":")[-1] in {"air", "cave_air", "void_air"}
            and str(head).split(":")[-1] in {"air", "cave_air", "void_air"}
        ):
            return feet_y
    return None


def _block_is_breathable(block_id: object) -> bool:
    from .surface_recovery import _block_is_breathable as check

    return check(block_id)


def wait_for_dry_level(
    client: object,
    *,
    origin_y: int,
    expected_y: int,
    timeout: float,
    deadline: Optional[float] = None,
    clock: Optional[Callable[[], float]] = None,
    sleep: Optional[Callable[[float], None]] = None,
) -> Optional[tuple[int, int, int]]:
    """Wait for an ascent route to reach breathing terrain near the surface."""
    from . import surface_recovery

    if clock is None:
        clock = surface_recovery.time.monotonic
    if sleep is None:
        sleep = surface_recovery.time.sleep
    limit = float(deadline) if deadline is not None else clock() + max(0.0, timeout)
    try:
        while True:
            now = clock()
            if now >= limit:
                break
            state = client.transport.dispatch("get_state", {})
            current = block_position(state)
            if current[1] < origin_y - 2:
                return None
            if (
                current[1] >= expected_y - 3
                and surface_recovery._head_is_dry(client, current)
                and not surface_recovery.position_is_aquatic(client, current)
                and _state_has_breathing_air(state)
            ):
                return current
            sleep(min(0.5, max(0.0, limit - now)))
    finally:
        client.transport.dispatch("chat", {"message": "#stop"})
        client.transport.dispatch("cancel", {})
    return None
