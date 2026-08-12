"""Cross-process serialization for workers sharing one physical input chest."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from ..common.inventory import deposit_excess_to_chest
from .bridge_guard import WorkerSafetyError

REPO = Path(__file__).resolve().parents[3]
LOCK_FILE = REPO / "runs" / "headlessmc" / "shared" / "storage_ingress.lockfile"


def _try_lock(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _deposit_positions(primary: tuple[int, int, int]) -> list[tuple[int, int, int]]:
    """Return the configured input followed by shared overflow containers."""
    positions = [tuple(int(value) for value in primary)]
    try:
        manifest = json.loads(
            (REPO / "industrial_camp_manifest.json").read_text(encoding="utf-8")
        )
        configured_input = tuple(int(value) for value in manifest["storage_input"])
        if positions[0] == configured_input:
            positions.extend(
                tuple(int(value) for value in candidate)
                for candidate in manifest.get("storage_overflow", [])
            )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        pass
    return list(dict.fromkeys(positions))


def _acquire(timeout: float):
    deadline = time.monotonic() + max(1.0, timeout)
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK_FILE.open("a+b")
    handle.seek(0, 2)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    while True:
        try:
            handle.seek(0)
            _try_lock(handle)
            return handle
        except OSError:
            if time.monotonic() >= deadline:
                handle.close()
                raise TimeoutError("shared storage input remained busy")
            time.sleep(1.0)


def serialized_deposit(
    client: Any,
    chest_pos: tuple[int, int, int],
    *,
    timeout: float = 180.0,
    attempts: int = 1,
    **kwargs: Any,
) -> int:
    """Deposit while excluding other local workers from the same container."""
    if int(attempts) != 1:
        raise ValueError("storage mutations must be attempted exactly once per target")
    try:
        handle = _acquire(timeout)
    except TimeoutError:
        return -1
    try:
        for position in _deposit_positions(chest_pos):
            try:
                result = deposit_excess_to_chest(client, position, **kwargs)
            except WorkerSafetyError:
                raise
            except Exception:
                # The mutation outcome is unknown. Never replay it elsewhere.
                return -1
            if result >= 0:
                return result
        return -1
    finally:
        try:
            handle.seek(0)
            _unlock(handle)
        finally:
            handle.close()
