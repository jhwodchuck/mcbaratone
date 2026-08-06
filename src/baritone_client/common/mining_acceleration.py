"""Best-effort, server-aware mining acceleration.

The bridge remains server-authoritative: accelerated mining only changes the
client's packet timing. Existing gather code continues to verify inventory and
world-state outcomes, and older bridges fall back to ordinary mining.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from functools import wraps
from typing import Any, Callable, Iterator, Mapping, TypeVar
from urllib.parse import urlsplit

from ..core.exceptions import CommandError, TransportError


LOGGER = logging.getLogger(__name__)
VALID_MODES = frozenset({"off", "legit", "aggressive"})
_Result = TypeVar("_Result")


def _server_address(state: Any, client: Any = None) -> str | None:
    identity = getattr(state, "bound_world_identity", None)
    if isinstance(identity, Mapping):
        value = identity.get("server_address")
    else:
        value = getattr(identity, "server_address", None)
    if value is None and client is not None:
        transport = getattr(client, "transport", None)
        host = getattr(transport, "host", None)
        port = getattr(transport, "port", None)
        value = f"{host}:{port}" if host is not None and port is not None else host
    text = str(value).strip() if value is not None else ""
    return text or None


def _is_loopback(address: str | None) -> bool:
    if not address:
        return False
    parsed = urlsplit(address if "://" in address else f"//{address}")
    host = (parsed.hostname or "").lower()
    return host in {"127.0.0.1", "::1", "localhost"}


def selected_fast_break_mode(state: Any, client: Any = None) -> str:
    """Choose aggressive timing only for an explicitly local server.

    ``MC_FAST_BREAK_MODE`` is an operator override. Invalid explicit values
    fail closed to off instead of silently enabling aggression.
    """

    requested = os.environ.get("MC_FAST_BREAK_MODE", "").strip().lower()
    if requested in VALID_MODES:
        return requested
    if requested:
        LOGGER.warning("Ignoring invalid MC_FAST_BREAK_MODE value: %s", requested)
        return "off"
    return "aggressive" if _is_loopback(_server_address(state, client)) else "legit"


@contextmanager
def accelerated_mining(client: Any, state: Any) -> Iterator[str]:
    """Temporarily enable the best supported fast-break mode.

    Yields the applied mode, or ``unavailable`` when an older bridge does not
    expose the command. Cleanup is best effort so mining errors are preserved.
    """

    requested = selected_fast_break_mode(state, client)
    if requested == "off":
        yield "off"
        return

    applied = "unavailable"
    try:
        client.transport.dispatch("set_fast_break", {"mode": requested})
        applied = requested
    except (CommandError, TransportError) as exception:
        LOGGER.info("Fast-break bridge capability unavailable: %s", exception)

    try:
        yield applied
    finally:
        if applied != "unavailable":
            try:
                client.transport.dispatch("set_fast_break", {"mode": "off"})
            except (CommandError, TransportError) as exception:
                LOGGER.warning("Could not disable fast-break mode: %s", exception)


def with_mining_acceleration(
    function: Callable[..., _Result],
) -> Callable[..., _Result]:
    """Wrap a shared mining primitive without changing its public signature."""

    @wraps(function)
    def accelerated(client: Any, *args: Any, **kwargs: Any) -> _Result:
        with accelerated_mining(client, None):
            return function(client, *args, **kwargs)

    return accelerated


__all__ = [
    "accelerated_mining",
    "selected_fast_break_mode",
    "with_mining_acceleration",
]
