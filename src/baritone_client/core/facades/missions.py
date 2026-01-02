from typing import Any, Dict, List, Optional

from ...transport.transport import Transport


class MissionFacade:
    """Facade for bridge-side mission coordination helpers."""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    def status(self) -> Dict[str, Any]:
        """Retrieve aggregated mission telemetry."""
        return self.transport.dispatch("mission/status", {})

    def checkpoint(self, phase: str, note: Optional[str] = None) -> Dict[str, Any]:
        """
        Update the bridge's mission checkpoint.

        Args:
            phase: Mission phase identifier
            note: Optional note or context
        """
        payload: Dict[str, Any] = {"phase": phase}
        if note:
            payload["note"] = note
        return self.transport.dispatch("mission/checkpoint", payload)

    def queue(self, actions: List[str], clear: bool = False) -> Dict[str, Any]:
        """Queue macros on the bridge for later execution."""
        payload: Dict[str, Any] = {"actions": actions}
        if clear:
            payload["clear"] = True
        return self.transport.dispatch("mission/queue", payload)

    def macro(
        self,
        name: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        dequeue: bool = False,
    ) -> Dict[str, Any]:
        """
        Trigger a macro on the bridge.

        Args:
            name: Macro identifier. If omitted and dequeue=True, the oldest queued macro runs.
            params: Optional parameters forwarded to the bridge.
            dequeue: Whether to consume the earliest queued macro before execution.
        """
        payload: Dict[str, Any] = {}
        if name:
            payload["name"] = name
        if params:
            payload["params"] = params
        if dequeue:
            payload["dequeue"] = True
        return self.transport.dispatch("mission/macro", payload)
