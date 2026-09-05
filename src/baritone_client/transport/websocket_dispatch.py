"""WebSocket request dispatch kept separate from the transport lifecycle."""

from __future__ import annotations

import asyncio
import queue
import time
import uuid
from typing import Any, Dict, Optional

from ..core.exceptions import BridgeResponseTimeout, CommandError, TransportError
from ..inventory_evidence import valid_inventory
from ..observability import emit_event
from .tcp_protocol import is_traced_command, observe_response, validate_response_sequence


def dispatch(transport, route: str, payload: Dict[str, Any], timeout: Optional[float]):
    attempts = (
        transport._read_retry_attempts if route in transport._read_only_retry_routes
        else 3 if route in transport._best_effort_retry_routes else 1
    )
    traced = is_traced_command(route)
    command_id = uuid.uuid4().hex if traced else None
    started = time.monotonic_ns()
    if traced:
        emit_event("command_requested", command_id=command_id, route=route, payload=payload, timeout=timeout)
    last_error = None
    for attempt in range(attempts):
        req_id = None
        sent = False
        try:
            with transport._lock:
                transport._seq += 1
                req_id = transport._seq
            request = {"jsonrpc": "2.0", "id": req_id, "method": route.replace("/", "."), "params": payload}
            response_queue = queue.Queue()
            with transport._response_lock:
                transport._response_queues[req_id] = response_queue
            future = asyncio.run_coroutine_threadsafe(transport._send_message(request), transport._loop)
            future.result(timeout=1.0)
            sent = True
            response = response_queue.get(timeout=transport.timeout if timeout is None else timeout)
            validate_response_sequence(response, req_id)
            if response.get("error"):
                error = response["error"]
                message = error.get("message", "RPC error") if isinstance(error, dict) else str(error)
                raise CommandError(message, response=response)
            result = response.get("result", {})
            if route == "get_inventory" and not valid_inventory(result):
                raise CommandError("Malformed inventory snapshot; current inventory unknown", response=response)
            observe_response(route, payload, result)
            if traced:
                emit_event("command_completed", command_id=command_id, route=route,
                           latency_ms=round((time.monotonic_ns() - started) / 1_000_000, 3), response=result)
            return result
        except queue.Empty:
            last_error = BridgeResponseTimeout(route, transport_type="websocket", request_sent=sent)
            if attempt + 1 < attempts:
                time.sleep(0.5)
                continue
            if route in transport._best_effort_retry_routes:
                return {}
            if traced:
                emit_event("command_failed", command_id=command_id, route=route,
                           latency_ms=round((time.monotonic_ns() - started) / 1_000_000, 3),
                           error_type=type(last_error).__name__, reason=str(last_error))
            raise last_error
        except (CommandError, TransportError) as exc:
            retryable = isinstance(exc, TransportError) or (
                isinstance(exc, CommandError)
                and ("player not available" in str(exc).lower() or "current inventory unknown" in str(exc).lower())
            )
            if attempt + 1 < attempts and route in transport._read_only_retry_routes and retryable:
                time.sleep(0.5)
                continue
            if traced:
                emit_event("command_failed", command_id=command_id, route=route,
                           latency_ms=round((time.monotonic_ns() - started) / 1_000_000, 3),
                           error_type=type(exc).__name__, reason=str(exc), response=getattr(exc, "response", None))
            raise
        except Exception as exc:
            if traced:
                emit_event("command_failed", command_id=command_id, route=route,
                           latency_ms=round((time.monotonic_ns() - started) / 1_000_000, 3),
                           error_type=type(exc).__name__, reason=str(exc))
            raise TransportError(f"WebSocket dispatch error: {exc}", original_error=exc) from exc
        finally:
            if req_id is not None:
                with transport._response_lock:
                    transport._response_queues.pop(req_id, None)
    raise last_error
