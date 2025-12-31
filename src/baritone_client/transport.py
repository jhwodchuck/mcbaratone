import abc
import atexit
import json
import logging
import queue
import socket
import threading
import time
import uuid
from typing import Any, Callable, Dict, Optional

from .enums import TransportEvent
from .events import EventRegistry
from .exceptions import CommandError, RouteError, TransportError
from .schemas import (
    JsonRpcRequest,
    JsonRpcResponse,
    RpcEventPushParams,
)

try:
    from py4j.java_gateway import CallbackServerParameters, GatewayParameters, JavaGateway
except ImportError:
    JavaGateway = None

try:
    from websockets.sync.client import connect
except ImportError:
    connect = None

logger = logging.getLogger(__name__)

class Transport(abc.ABC):
    def __init__(self) -> None:
        self.events = EventRegistry()
        self._shutdown_event = threading.Event()

    @abc.abstractmethod
    def dispatch(self, route: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        pass

    def subscribe(self, event: TransportEvent, callback: Callable[[Any], None]) -> None:
        self.events.subscribe(event, callback)

    def emit(self, event: TransportEvent, payload: Dict[str, Any]) -> None:
        self.events.dispatch(event, payload)

    @abc.abstractmethod
    def shutdown(self) -> None:
        self._shutdown_event.set()


class Py4JTransport(Transport):
    def __init__(self, gateway_params: Optional[Dict[str, Any]] = None) -> None:
        super().__init__()
        if JavaGateway is None:
            raise ImportError("py4j is required for Py4JTransport")

        # Setup callback queue and thread
        self._callback_queue: queue.Queue = queue.Queue()
        self._dispatcher_thread = threading.Thread(target=self._process_queue, daemon=True)
        self._dispatcher_thread.start()

        # Initialize Gateway
        params = gateway_params or {}
        self.gateway = JavaGateway(
             gateway_parameters=GatewayParameters(**params.get("gateway_parameters", {})),
             callback_server_parameters=CallbackServerParameters(**params.get("callback_server_parameters", {}))
        )
        self.entry_point = self.gateway.entry_point
        atexit.register(self.shutdown)

        # Register generic python event handler on Java side
        # Assuming Java side has: registerPythonHandler(object handler)
        # self.entry_point.registerPythonHandler(self)

    def dispatch(self, route: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        # Map route to Java method calls using reflection or strict mapping
        # For prototype, we assume the entry_point exposes a generic 'runCommand'
        try:
            # Simplistic mapping: "command/run" -> entry_point.runCommand(json)
            response_json = self.entry_point.dispatch(route, json.dumps(payload))
            return json.loads(response_json)
        except Exception as e:
            raise TransportError(f"Py4J dispatch failed: {e}") from e

    def _process_queue(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                item = self._callback_queue.get(timeout=0.1)
                event_name, payload = item
                # Map string event name to TransportEvent enum if possible
                try:
                    event_enum = TransportEvent(event_name)
                    self.emit(event_enum, payload)
                except ValueError:
                    logger.warning(f"Unknown event received: {event_name}")
            except queue.Empty:
                continue

    # Py4J Callback Interface (called from Java)
    def handleEvent(self, event_name: str, payload_json: str) -> None:
        """Java calls this method."""
        try:
            payload = json.loads(payload_json)
            self._callback_queue.put((event_name, payload))
        except Exception as e:
            logger.error(f"Error handling Java event: {e}")

    class Java:
        implements = ["baritone.api.python.IPythonEventHandler"]

    def shutdown(self) -> None:
        super().shutdown()
        if hasattr(self, 'gateway'):
            self.gateway.shutdown()


class WebSocketTransport(Transport):
    def __init__(self, url: str) -> None:
        super().__init__()
        if connect is None:
            raise ImportError("websockets is required for WebSocketTransport")

        self.url = url
        self._socket = connect(self.url)
        self._msg_id = 0
        self._lock = threading.Lock()
        
        # Responses meant for synchronous requests: id -> queue
        self._response_queues: Dict[int, queue.Queue] = {}
        # Buffer for responses received before a dispatch has registered its queue
        self._buffered_responses: Dict[int, JsonRpcResponse] = {}

        self._receiver_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._receiver_thread.start()
        
        # Backpressure / ACK state
        self._active_subscriptions: Dict[str, Any] = {} # subId -> metadata
        self._last_event_ts: Dict[str, int] = {} # subId -> timestamp
        self._ack_thread = threading.Thread(target=self._ack_loop, daemon=True)
        self._ack_thread.start()

        atexit.register(self.shutdown)

    def _ack_loop(self) -> None:
        """Periodically send ACKs for active subscriptions."""
        while not self._shutdown_event.is_set():
            time.sleep(1.0) # Send ACK every 1s
            if not self._active_subscriptions:
                continue

            for sub_id, ts in list(self._last_event_ts.items()):
                try:
                    self.dispatch("events.ack", {
                        "subscriptionId": sub_id,
                        "lastTimestamp": ts
                    })
                    # Use internal send to avoid waiting for response?
                    # dispatch waits for response. ACK might be notification or return result.
                    # Design says "shim pauses after 500 unacked".
                    # If ACK returns result, dispatch is fine.
                except Exception as e:
                    logger.warning(f"Failed to send ACK for {sub_id}: {e}")

    def dispatch(self, route: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        # Map route to JSON-RPC method
        # e.g. "command/run" -> "commands.run"
        method_map = {
            "command/run": "commands.run",
            "settings/set": "settings.patch", # simplified
            "settings/get": "settings.get",
        }
        rpc_method = method_map.get(route, route.replace("/", "."))

        with self._lock:
            self._msg_id += 1
            request_id = self._msg_id
            logger.debug(f"Generated request ID: {request_id}")
        
        req = JsonRpcRequest(method=rpc_method, params=payload, id=request_id)
        
        # Prepare queue for response
        q = queue.Queue()
        with self._lock:
            self._response_queues[request_id] = q

        # If a response already arrived (race where read_loop ran before dispatch), deliver it now
        with self._lock:
            if request_id in self._buffered_responses:
                logger.debug(f"Delivering buffered response for ID {request_id}")
                q.put(self._buffered_responses.pop(request_id))

        try:
            self._socket.send(req.model_dump_json(exclude_none=True))
            logger.debug(f"Sent request {request_id}")
            response: JsonRpcResponse = q.get(timeout=10) # 10s timeout
            
            if response.error:
                 raise TransportError(f"RPC Error {response.error.code}: {response.error.message}")
            return response.result or {}
        except Exception as e:
            raise TransportError(f"WebSocket dispatch error: {e}") from e
        finally:
             with self._lock:
                 self._response_queues.pop(request_id, None)

    def _read_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                message = self._socket.recv()
                data = json.loads(message)
                logger.debug(f"Received: {data}")
                
                # Check if it's a response or a notification
                if "id" in data and data["id"] is not None:
                     # It is a response
                     req_id = data["id"]
                     with self._lock:
                         if req_id in self._response_queues:
                             logger.debug(f"Putting response in queue for ID {req_id}")
                             self._response_queues[req_id].put(JsonRpcResponse(**data))
                         else:
                             # Buffer the response in case dispatch registers the queue shortly after
                             logger.warning(f"Response for unknown/expired ID {req_id}. Buffering for later. Active: {list(self._response_queues.keys())}")
                             try:
                                 self._buffered_responses[req_id] = JsonRpcResponse(**data)
                             except Exception:
                                 logger.exception("Failed to buffer response for unknown ID")
                else:
                    # Notification / Event Push
                    if data.get("method") == "events.push":
                        params = RpcEventPushParams(**data["params"])
                        # Track last TS for backpressure
                        if params.subId:
                            self._last_event_ts[params.subId] = params.ts
                            self._active_subscriptions[params.subId] = {} # Mark active

                        try:
                            event_enum = TransportEvent(params.event)
                            self.emit(event_enum, params.payload)
                        except ValueError:
                             pass
            except Exception as e:
                logger.error(f"Error in read_loop: {e}")
                if self._shutdown_event.is_set():
                    break
                time.sleep(0.1)

    def shutdown(self) -> None:
        super().shutdown()
        if hasattr(self, '_socket'):
            self._socket.close()


class TcpTransport(Transport):
    """
    Transport for the 'BaritoneAPIBridge' mod which uses a custom JSON-over-TCP protocol.
    Acts as an adapter effectively, translating standard routes to the bridge's commands.
    """
    def __init__(self, host: str = "localhost", port: int = 5555) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.connect((self.host, self.port))
        
        # Threading for responses
        self._response_queues: Dict[str, queue.Queue] = {}
        self._lock = threading.RLock()
        self._shutdown_event = threading.Event()
        
        # Track schematic uploads (uploadId -> name mapping)
        self._schematic_uploads: Dict[str, str] = {}
        
        self._receiver_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._receiver_thread.start()
        
        atexit.register(self.shutdown)

    def dispatch(self, route: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        req_id = str(uuid.uuid4())
        
        # --- Protocol Translation ---
        bridge_cmd = {}
        
        if route == "command/run":
            cmd_str = payload.get("command", "")
            if cmd_str.startswith("goto "):
                parts = cmd_str.split(" ")
                if len(parts) >= 4:
                    bridge_cmd = {
                        "command": "goto",
                        "params": {
                            "x": int(parts[1]),
                            "y": int(parts[2]),
                            "z": int(parts[3])
                        }
                    }
                else:
                    bridge_cmd = {"command": "chat", "params": {"message": "#" + cmd_str}}
            else:
                ctrl_msg = cmd_str if cmd_str.startswith("#") else "#" + cmd_str
                bridge_cmd = {"command": "chat", "params": {"message": ctrl_msg}}
                
        elif route == "goal/apply":
            g_type = payload.get("type")
            g_pl = payload.get("payload", {})
            
            if g_type == "GoalBlock":
                bridge_cmd = {
                    "command": "goto",
                    "params": {"x": g_pl["x"], "y": g_pl["y"], "z": g_pl["z"]}
                }
            elif g_type == "GoalXZ":
                bridge_cmd = {
                    "command": "chat", 
                    "params": {"message": f"#goal {g_pl['x']} {g_pl['z']}"}
                }
            elif g_type == "GoalYLevel":
                bridge_cmd = {
                    "command": "goal",
                    "params": {"type": "yLevel", "value": str(g_pl["y"])}
                }
                
        elif route == "process/status":
             bridge_cmd = {"command": "get_state", "params": {}}
        
        # Direct command routes (bypass chat for better performance)
        elif route == "command/explore":
             bridge_cmd = {
                 "command": "explore",
                 "params": {
                     "x": payload.get("x", 0),
                     "z": payload.get("z", 0)
                 }
             }
        
        elif route == "command/follow":
             bridge_cmd = {
                 "command": "follow",
                 "params": {
                     "entity": payload.get("entity", "player")
                 }
             }
        
        elif route == "command/come":
             bridge_cmd = {
                 "command": "come",
                 "params": payload
             }
        
        elif route == "command/cancel":
             bridge_cmd = {"command": "cancel", "params": {}}
        
        elif route == "command/tunnel":
             bridge_cmd = {
                 "command": "tunnel",
                 "params": {
                     "x": payload.get("x", 0),
                     "y": payload.get("y", 64),
                     "z": payload.get("z", 0),
                     "radius": payload.get("radius", 1)
                 }
             }
        
        elif route == "command/get_block":
             bridge_cmd = {
                 "command": "get_block",
                 "params": {
                     "x": payload.get("x", 0),
                     "y": payload.get("y", 64),
                     "z": payload.get("z", 0)
                 }
             }
        
        elif route == "command/selection":
             action = payload.get("action", "set")
             if action == "set":
                 bridge_cmd = {
                     "command": "sel",
                     "params": {
                         "action": "set",
                         "x1": payload.get("x1") or payload.get("start", {}).get("x", 0),
                         "y1": payload.get("y1") or payload.get("start", {}).get("y", 64),
                         "z1": payload.get("z1") or payload.get("start", {}).get("z", 0),
                         "x2": payload.get("x2") or payload.get("end", {}).get("x", 0),
                         "y2": payload.get("y2") or payload.get("end", {}).get("y", 64),
                         "z2": payload.get("z2") or payload.get("end", {}).get("z", 0)
                     }
                 }
             elif action == "clear":
                 bridge_cmd = {
                     "command": "sel",
                     "params": {"action": "clear"}
                 }
             else:
                 bridge_cmd = {
                     "command": "sel",
                     "params": {
                         "action": action,
                         "direction": payload.get("direction", "NORTH"),
                         "blocks": payload.get("blocks", 1)
                     }
                 }
        
        # Process route mappings
        elif route == "process/mine/start":
             bridge_cmd = {
                 "command": "mine",
                 "params": {
                     "block_type": payload.get("target"),
                     "count": payload.get("quantity", 0)
                 }
             }
        
        elif route == "process/farm/start":
             bridge_cmd = {
                 "command": "farm",
                 "params": {
                     "range": payload.get("radius", 0)
                 }
             }
        
        elif route == "process/builder/start":
             bridge_cmd = {
                 "command": "build",
                 "params": {
                     "schematic": payload.get("schematicId"),
                     "x": payload.get("x", 0),
                     "y": payload.get("y", 64),
                     "z": payload.get("z", 0)
                 }
             }
        
        elif route == "process/pause":
             bridge_cmd = {"command": "pause", "params": {}}
        
        elif route == "process/resume":
             # Bridge doesn't have explicit resume, but we can use chat
             bridge_cmd = {"command": "chat", "params": {"message": "#resume"}}
        
        # Settings routes
        elif route == "settings/set":
             bridge_cmd = {
                 "command": "settings",
                 "params": {
                     "set": payload.get("name"),
                     "value": str(payload.get("value"))
                 }
             }
        
        elif route == "settings/get":
             bridge_cmd = {
                 "command": "settings",
                 "params": {
                     "get": payload.get("name")
                 }
             }
        
        # Goal routes
        elif route == "goal/clear":
             bridge_cmd = {"command": "stop", "params": {}}

        # Schematic Upload Mappings (support both dot and slash notation)
        # Note: Bridge protocol uses "name" throughout, but schematics.py uses "uploadId"
        # We need to track the mapping between uploadId and name
        elif route in ("schematics/upload/init", "schematics.upload.init"):
             name = payload.get("name")
             upload_id = payload.get("uploadId", name)  # If no uploadId provided, use name
             self._schematic_uploads[upload_id] = name
             
             bridge_cmd = {
                 "command": "schematic_init",
                 "params": {
                     "name": name,
                     "size": payload.get("size")
                 }
             }
        
        elif route in ("schematics/upload/chunk", "schematics.upload.chunk"):
             upload_id = payload.get("uploadId")
             # Look up name from uploadId mapping, or use uploadId as name if not found
             name = self._schematic_uploads.get(upload_id, upload_id)
             
             bridge_cmd = {
                 "command": "schematic_chunk",
                 "params": {
                     "name": name,
                     "data": payload.get("data")
                     # Bridge doesn't use offset, it appends chunks sequentially
                 }
             }
        
        elif route in ("schematics/upload/commit", "schematics.upload.commit"):
             upload_id = payload.get("uploadId")
             name = self._schematic_uploads.get(upload_id, upload_id)
             
             bridge_cmd = {
                 "command": "schematic_commit",
                 "params": {
                     "name": name
                 }
             }
             
             # Clean up mapping after commit
             self._schematic_uploads.pop(upload_id, None)

        elif route == "command/build":
             # Support build command if sent via command/run usually, but if specific route used:
             bridge_cmd = {
                 "command": "build", 
                 "params": {
                     "schematic": payload.get("schematic"),
                     "x": payload.get("x"), 
                     "y": payload.get("y"), 
                     "z": payload.get("z")
                 }
             }

        else:
             # Raise error instead of returning empty dict
             raise RouteError(route, "TcpTransport")

        bridge_cmd["id"] = req_id
        
        # Setup queue
        q = queue.Queue()
        with self._lock:
            self._response_queues[req_id] = q

        try:
            msg = json.dumps(bridge_cmd) + "\n"
            self._socket.sendall(msg.encode("utf-8"))
            
            resp = q.get(timeout=5)
            
            if resp.get("status") == "error":
                error_msg = resp.get("error", "Unknown error")
                raise CommandError(
                    f"Bridge command failed: {error_msg}",
                    command=bridge_cmd.get("command"),
                    error_code=resp.get("error_code")
                )
            
            data = resp.get("data", {})
            
            # Transform responses to match expected format
            # For schematic_init, bridge returns {"ready": true}, but schematics.py expects {"uploadId": "...", "maxChunkSize": ...}
            if route in ("schematics/upload/init", "schematics.upload.init"):
                upload_id = payload.get("uploadId", payload.get("name"))
                return {
                    "uploadId": upload_id,
                    "maxChunkSize": 65536  # Default chunk size
                }
            
            # For schematic_commit, bridge returns {"saved": true, "sha256": "..."}, but schematics.py expects {"verified": true, "fileId": "..."}
            if route in ("schematics/upload/commit", "schematics.upload.commit"):
                upload_id = payload.get("uploadId")
                name = self._schematic_uploads.get(upload_id, upload_id) if upload_id else payload.get("name", "unknown")
                return {
                    "verified": data.get("saved", False),
                    "fileId": name  # Use name as fileId
                }
            
            return data
            
        except queue.Empty:
            raise TransportError(
                f"Timeout waiting for bridge response (route: {route})",
                original_error=None
            )
        except RouteError:
            raise  # Re-raise route errors as-is
        except Exception as e:
            raise TransportError(
                f"Dispatch error for route '{route}': {e}",
                original_error=e
            )
        finally:
            with self._lock:
                self._response_queues.pop(req_id, None)

    def subscribe(self, event: TransportEvent, callback) -> None:
        """
        Subscribe to events. 
        
        Note: TCP bridge does not support push-based events. 
        Use polling via get_state() or switch to WebSocketTransport for real-time events.
        """
        logger.warning(
            f"Event subscription for {event.value} not fully supported by TCP bridge (polling only). "
            "Consider using WebSocketTransport for real-time events."
        )
        # Still register the callback in case we implement polling-based event emission
        super().subscribe(event, callback)

    def emit(self, event: TransportEvent, payload: Dict[str, Any]) -> None:
        pass

    def shutdown(self) -> None:
        self._shutdown_event.set()
        try:
            self._socket.close()
        except:
            pass

    def _read_loop(self) -> None:
        buffer = ""
        while not self._shutdown_event.is_set():
            try:
                try:
                    chunk = self._socket.recv(4096).decode("utf-8")
                    if not chunk:
                        break
                    buffer += chunk
                except socket.timeout:
                    continue
                except OSError:
                    break
                    
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
                    except json.JSONDecodeError:
                        logger.error(f"Invalid JSON: {line}")
                        
            except Exception as e:
                logger.error(f"TcpTransport read error: {e}")
                time.sleep(1)
