"""Bounded outer retries for idempotent bridge reads used by live workers."""

from __future__ import annotations

import time
from typing import Any

try:
    from ..core.exceptions import BridgeResponseTimeout
except ImportError:  # Removed once the typed transport-timeout change is merged.
    from ..core.exceptions import TransportError

    class BridgeResponseTimeout(TransportError):
        """Compatibility type for branches predating typed bridge timeouts."""

        def __init__(
            self,
            route: str,
            *,
            transport_type: str,
            request_sent: bool,
        ) -> None:
            super().__init__(f"Timeout waiting for bridge response (route: {route})")
            self.route = route
            self.transport_type = transport_type
            self.request_sent = request_sent


READ_ONLY_ROUTES = {
    "find_blocks",
    "get_block",
    "get_entities",
    "get_inventory",
    "get_screen",
    "get_state",
    "get_view",
    "get_dimension",
    "get_version",
}


class ResilientReadTransport:
    """Delegate mutations once while retrying transient read timeouts."""

    def __init__(self, transport: Any, attempts: int = 5) -> None:
        self.transport = transport
        self.attempts = max(1, int(attempts))

    def dispatch(
        self,
        route: str,
        payload: dict[str, Any],
        timeout: float | None = None,
    ) -> Any:
        attempts = self.attempts if route in READ_ONLY_ROUTES else 1
        for attempt in range(1, attempts + 1):
            try:
                return self.transport.dispatch(route, payload, timeout)
            except BridgeResponseTimeout:
                if attempt == attempts:
                    raise
                time.sleep(float(attempt) * 2.0)
        raise RuntimeError("unreachable read retry state")
