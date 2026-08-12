"""Per-client ownership for multi-command combat actions."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from functools import wraps
from typing import Any, Iterator

_LOCK_ATTRIBUTE = "_mcbaratone_combat_action_lock"
_CREATION_LOCK = threading.Lock()
_RLOCK_TYPE = type(threading.RLock())
HELD_ITEM_RELEASE_BUFFER_MS = 100


def _action_lock(client: Any) -> threading.RLock:
    transport = getattr(client, "transport", None)
    # The transport is the physical input session. Multiple facade/wrapper
    # client objects may share it and therefore must share one action lock.
    owner = transport if transport is not None else client
    lock = getattr(owner, _LOCK_ATTRIBUTE, None)
    if isinstance(lock, _RLOCK_TYPE):
        return lock
    with _CREATION_LOCK:
        lock = getattr(owner, _LOCK_ATTRIBUTE, None)
        if isinstance(lock, _RLOCK_TYPE):
            return lock
        lock = threading.RLock()
        try:
            setattr(owner, _LOCK_ATTRIBUTE, lock)
        except Exception as exc:
            raise RuntimeError(
                "client transport does not support combat action ownership"
            ) from exc
        return lock


@contextmanager
def exclusive_combat_action(client: Any, *, blocking: bool = True) -> Iterator[bool]:
    """Own selection/orientation/use as one operation on one client."""
    lock = _action_lock(client)
    acquired = lock.acquire(blocking=blocking)
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()


def held_item_quarantine_seconds(
    duration_ms: int,
    *,
    release_buffer_ms: int = HELD_ITEM_RELEASE_BUFFER_MS,
) -> float:
    """Return how long an asynchronous held-use can affect client input."""
    duration = int(duration_ms)
    if duration < 0:
        raise ValueError("duration_ms must be non-negative")
    return (duration + max(0, int(release_buffer_ms))) / 1000.0


def dispatch_held_item_use(
    client: Any,
    duration_ms: int,
    *,
    blocking: bool = True,
    release_buffer_ms: int = HELD_ITEM_RELEASE_BUFFER_MS,
    **parameters: Any,
) -> Any:
    """Dispatch ``use_item`` and own input through its scheduled release.

    The bridge acknowledges a held use when it schedules the future key
    release, not when that release has happened. Even a timeout or declined
    response is ambiguous after dispatch, so quarantine the full requested
    duration plus a client-tick buffer before another item sequence can run.
    """
    duration = int(duration_ms)
    quarantine = held_item_quarantine_seconds(
        duration,
        release_buffer_ms=release_buffer_ms,
    )
    with exclusive_combat_action(client, blocking=blocking) as acquired:
        if not acquired:
            return None
        attempted = False
        try:
            attempted = True
            payload = dict(parameters)
            payload.setdefault("hand", "MAIN_HAND")
            payload["duration_ms"] = duration
            return client.transport.dispatch("use_item", payload)
        finally:
            if attempted:
                time.sleep(quarantine)


def exclusive_client_action(function):
    """Defer a background client method while combat owns item use."""

    @wraps(function)
    def wrapped(self, *args, **kwargs):
        with exclusive_combat_action(self.client, blocking=False) as acquired:
            if not acquired:
                return None
            return function(self, *args, **kwargs)

    return wrapped


def exclusive_client_function(function):
    """Own item-use commands for a function whose first argument is client."""

    @wraps(function)
    def wrapped(client, *args, **kwargs):
        with exclusive_combat_action(client) as acquired:
            if not acquired:
                return False
            return function(client, *args, **kwargs)

    return wrapped


__all__ = [
    "HELD_ITEM_RELEASE_BUFFER_MS",
    "dispatch_held_item_use",
    "exclusive_client_action",
    "exclusive_client_function",
    "exclusive_combat_action",
    "held_item_quarantine_seconds",
]
