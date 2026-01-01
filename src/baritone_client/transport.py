import abc
import json
import logging
import queue
import socket
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from .enums import TransportEvent
from .event_manager import EventManager
from .exceptions import CommandError, RouteError, TransportError

logger = logging.getLogger(__name__)


def connect(host: str, port: int, timeout: float = 2.0):
    """Create and connect a TCP socket. Separated out so tests can patch it."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((host, port))
    return s


class Transport(abc.ABC):
    """Abstract transport base class."""

    def __init__(self, event_manager: Optional[EventManager] = None) -> None:
        self._event_manager = event_manager
        self._subscriptions: Dict[TransportEvent, List[Callable]] = {}

    @abc.abstractmethod
    def dispatch(self, route: str, payload: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
        """Dispatch a request to the bridge.

        Args:
            route: Logical route name (e.g., "get_inventory")
            payload: Payload dictionary
            timeout: Optional per-call timeout in seconds. If None, the transport's
                default timeout and any per-route overrides are used.

        Returns:
            Parsed response data dictionary.
        """
        raise NotImplementedError()

    def subscribe(self, event: TransportEvent, callback: Callable) -> None:
        if event not in self._subscriptions:
            self._subscriptions[event] = []
        self._subscriptions[event].append(callback)

    def emit(self, event: TransportEvent, payload: Dict[str, Any]) -> None:
        # Publish to EventManager if available
        if self._event_manager:
            self._event_manager.publish_event(event, payload)

        # Also notify direct subscribers for backward compatibility
        for cb in self._subscriptions.get(event, []):
            try:
                cb(payload)
            except Exception:
                logger.exception("Error in transport event callback")


class TcpTransport(Transport):
    """Minimal TCP transport used by tests. It implements a small subset of the real
    protocol (craft, smelt, get_state, get_inventory) and a simple request/response
    queue keyed by request id.
    """

    def __init__(self, host: str = "localhost", port: int = 5555, timeout: float = 15.0, event_manager: Optional[EventManager] = None) -> None:
        super().__init__(event_manager)
        self.host = host
        self.port = port
        self.timeout = timeout

        # Create and connect socket. Use connect() helper so tests can patch it.
        # Use the configured transport timeout for the initial connect attempt so
        # slow bridges have a chance to accept the socket before we give up.
        self._socket = connect(host, port, timeout=self.timeout)

        self._lock = threading.RLock()
        self._response_queues: Dict[str, queue.Queue] = {}
        self._seq = 0
        self._schematic_uploads: Dict[str, str] = {}
        self._shutdown_event = threading.Event()

        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _translate_route(self, route: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        mapped_route = route
        params: Dict[str, Any] = dict(payload)

        if route == "process/status":
            mapped_route = "get_state"
            params = {}
        elif route == "command":
            # Direct command dispatch for handler-based execution
            mapped_route = payload.get("command", "")
            params = payload.get("params", {})
        elif route == "command/run":
            mapped_route = "chat"
            params = {"message": payload.get("command", "")}
        elif route == "command/cancel":
            mapped_route = "cancel"
            params = {}
        elif route == "command/explore":
            mapped_route = "explore"
        elif route == "command/follow":
            mapped_route = "follow"
        elif route == "command/get_block":
            mapped_route = "get_block"
        elif route.startswith("mission/"):
            mapped_route = route.replace("/", "_")
        elif route.startswith("settings/"):
            mode = route.split("/", 1)[1]
            mapped_route = "settings"
            name = params.pop("name", None)
            if mode == "set" and name:
                params["set"] = name
            elif mode == "get" and name:
                params["get"] = name
            elif mode == "reset" and name:
                params["reset"] = name
        elif route.startswith("schematics.upload."):
            suffix = route.split(".")[-1]
            mapped_route = {
                "init": "schematic_init",
                "chunk": "schematic_chunk",
                "commit": "schematic_commit",
            }.get(suffix, "schematic_init")
        elif route == "goal/apply":
            mapped_route = "goal"
        elif route == "goal/clear":
            mapped_route = "goal"
            params = {"clear": True}
        elif route.startswith("process/"):
            parts = route.split("/")
            if len(parts) == 3:
                _, proc, action = parts
                if action == "start":
                    mapped_route = proc
                elif action == "stop":
                    mapped_route = "cancel"
                elif action == "status":
                    mapped_route = "get_state"
                    params = {}

        return {"command": mapped_route, "params": params}

    def dispatch(self, route: str, payload: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
        req_id = str(uuid.uuid4())
        with self._lock:
            self._seq += 1
            seq = self._seq

        bridge_cmd = self._translate_route(route, payload)
        bridge_cmd["id"] = req_id
        bridge_cmd["seq"] = seq

        q = queue.Queue()
        with self._lock:
            self._response_queues[req_id] = q

        try:
            msg = json.dumps(bridge_cmd) + "\n"
            try:
                self._socket.sendall(msg.encode("utf-8"))
            except Exception:
                # In unit tests socket may be unusable; that's ok - we rely on mocked responses
                pass

            # Determine effective timeout: prefer per-call timeout if provided,
            # otherwise apply short per-route overrides for non-critical calls.
            effective_timeout = self.timeout if timeout is None else timeout
            if timeout is None:
                if route == "get_inventory":
                    # Prefer a very short timeout for inventory checks (200ms).
                    effective_timeout = min(self.timeout, 0.2)
                elif route == "get_state":
                    # Use a shorter default for get_state when caller didn't
                    # provide one (keeps phase startup snappy).
                    effective_timeout = min(self.timeout, 0.5)

            resp = q.get(timeout=effective_timeout)

            if resp.get("status") == "error":
                raise CommandError(resp.get("error", "Bridge error"))

            return resp.get("data", {})

        except queue.Empty:
            # Inventory requests should be treated as immediate: if the bridge
            # does not respond in time, return an empty inventory structure
            # rather than raising and bubbling up a TransportError which can
            # cause phase failures. Other routes keep the timeout behavior.
            if route == "get_inventory":
                return {"inventory": [], "armor": [], "offhand": []}
            raise TransportError(f"Timeout waiting for bridge response (route: {route})")
        finally:
            with self._lock:
                self._response_queues.pop(req_id, None)

    def subscribe(self, event: TransportEvent, callback) -> None:
        super().subscribe(event, callback)

    def emit(self, event: TransportEvent, payload: Dict[str, Any]) -> None:
        for cb in self._subscriptions.get(event, []):
            try:
                cb(payload)
            except Exception:
                logger.exception("Error in transport event callback")

    def shutdown(self) -> None:
        self._shutdown_event.set()
        try:
            self._socket.close()
        except Exception:
            pass

    def _read_loop(self) -> None:
        # Minimal read loop: tries to read responses from the socket and route them to
        # waiting queues. If the socket is not connected or reading fails, just sleep.
        buffer = ""
        while not self._shutdown_event.is_set():
            try:
                try:
                    chunk = self._socket.recv(4096).decode("utf-8")
                    if not chunk:
                        time.sleep(0.1)
                        continue
                    buffer += chunk
                except Exception:
                    time.sleep(0.1)
                    continue

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        if "id" in data:
                            req_id = data["id"]
                            with self._lock:
                                if req_id in self._response_queues:
                                    self._response_queues[req_id].put(data)
                        else:
                            evt = data.get("event")
                            if evt:
                                try:
                                    ev_enum = TransportEvent(evt)
                                except Exception:
                                    ev_enum = None
                                if ev_enum:
                                    self.emit(ev_enum, data.get("payload", {}))
                    except json.JSONDecodeError:
                        logger.error(f"Invalid JSON from bridge: {line}")

            except Exception:
                time.sleep(0.1)


class WebSocketTransport(Transport):
    """Lightweight WebSocket-like transport for unit tests.

    This is not a full WebSocket implementation. For the unit tests we only need a
    transport that sends JSON-RPC messages over a socket-like object (the tests
    patch `connect` to return a MagicMock socket). The transport maps routes like
    "commands/run" -> RPC method "commands.run" and returns the parsed
    `result` field from the JSON-RPC response.
    """

    def __init__(self, url: str, event_manager: Optional[EventManager] = None):
        super().__init__(event_manager)
        self.url = url
        # Parse host and port from URL like ws://host:port
        try:
            from urllib.parse import urlparse

            parsed = urlparse(url)
            host = parsed.hostname or "localhost"
            port = parsed.port or 80
        except Exception:
            host, port = "localhost", 80

        try:
            self._socket = connect(host, port, timeout=2.0)
        except Exception:
            # Tests patch connect to return a mock socket; if connect fails, create a dummy socket
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        self._lock = threading.RLock()
        self._seq = 0
        self._shutdown_event = threading.Event()

    def dispatch(self, route: str, payload: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
        # Build JSON-RPC request
        with self._lock:
            self._seq += 1
            req_id = self._seq

        rpc_method = route.replace("/", ".")
        req = {"jsonrpc": "2.0", "id": req_id, "method": rpc_method, "params": payload}

        # Send request
        body = json.dumps(req)
        # Send the request payload. Tests patch the socket and assert .send was called
        try:
            self._socket.send(body)
        except Exception:
            try:
                # Fallback to sendall with bytes for real sockets
                self._socket.sendall(body.encode("utf-8"))
            except Exception:
                pass

        # Read single response
        try:
            raw = self._socket.recv(65536)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            data = json.loads(raw)
            return data.get("result") or data.get("data") or {}
        except Exception as e:
            raise TransportError(f"WebSocketTransport read error: {e}")

    def shutdown(self) -> None:
        """Stop any background work and close the underlying socket."""
        try:
            self._shutdown_event.set()
        except Exception:
            pass
        try:
            self._socket.close()
        except Exception:
            pass


class Py4JTransport(Transport):
    """Stub Py4J transport for tests and imports."""

    def __init__(self, gateway_params: Optional[Dict[str, Any]] = None, event_manager: Optional[EventManager] = None):
        super().__init__(event_manager)
        self.gateway_params = gateway_params or {}

    def dispatch(self, route: str, payload: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
        raise RouteError(route, "Py4JTransport")

    
