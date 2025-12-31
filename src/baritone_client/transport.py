import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, Optional

from .enums import TransportEvent
from .events import EventRegistry
from .exceptions import TransportError


class Transport:
    def __init__(self) -> None:
        self.events = EventRegistry()

    def dispatch(self, route: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def subscribe(self, event: TransportEvent, callback) -> None:
        self.events.subscribe(event, callback)

    def emit(self, event: TransportEvent, payload: Dict[str, Any]) -> None:
        self.events.dispatch(event, payload)

    def shutdown(self) -> None:
        """Clean up transport resources."""


class HttpTransport(Transport):
    def __init__(self, base_url: str, timeout: int = 10, headers: Optional[Dict[str, str]] = None) -> None:
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.headers = headers or {"Content-Type": "application/json"}

    def _request(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/') }"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=self.headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise TransportError(f"Transport request failed: {exc}") from exc
        try:
            return json.loads(body) if body else {}
        except json.JSONDecodeError as exc:
            raise TransportError(f"Invalid JSON response from {url}: {body}") from exc

    def dispatch(self, route: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request(route, payload)

    def stream(self, route: str, events: Iterable[TransportEvent]) -> None:
        """Placeholder for long-lived event streaming (e.g., SSE/WebSocket)."""
        # Implementation can be extended when the Baritone bridge exposes streaming endpoints.
        for event in events:
            self.emit(event, {"route": route})
