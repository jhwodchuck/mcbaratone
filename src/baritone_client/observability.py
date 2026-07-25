"""Low-overhead structured event telemetry for autonomous runs."""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import platform
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


_SESSION_ID = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{os.getpid()}"
_LOCK = threading.Lock()
_OUTPUT_DIR = Path(os.environ.get("MC_RUN_DIR", Path.cwd())).resolve()
_CURRENT_OPERATION: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "mcbaratone_operation_id", default=None
)
_CURRENT_PHASE: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "mcbaratone_phase", default=None
)
_CURRENT_TASK: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "mcbaratone_task", default=None
)
_SECRET_TOKENS = ("key", "token", "password", "secret", "authorization")
_INVENTORY_LOCK = threading.Lock()
_INVENTORY_COUNTS: Optional[Dict[str, int]] = None
_LAST_STATE_SAMPLE_NS = 0
_RECENT_EVENTS = deque(maxlen=4000)
_ACTIVE_NAVIGATION: Optional[Dict[str, Any]] = None
_ACTIVE_CONTAINER: Optional[Dict[str, Any]] = None
_PLAYER_DEAD = False
_LAST_POSITION: Optional[Dict[str, Any]] = None
_LAST_CHUNK: Optional[tuple[int, int]] = None
_LAST_ENTITY_SAMPLE_NS = 0
_EVENT_MAX_BYTES = int(
    os.environ.get("MC_EVENT_JOURNAL_MAX_BYTES", str(16 * 1024 * 1024))
)
_EVENT_BACKUPS = max(1, int(os.environ.get("MC_EVENT_JOURNAL_BACKUPS", "3")))


def _event_path() -> Path:
    path = _OUTPUT_DIR / "telemetry" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _rotate_event_journal(path: Path, incoming_bytes: int) -> None:
    """Keep each event-journal segment within the configured byte budget."""
    try:
        current_bytes = path.stat().st_size
    except FileNotFoundError:
        return
    if current_bytes + incoming_bytes <= _EVENT_MAX_BYTES:
        return
    oldest = path.with_name(f"{path.name}.{_EVENT_BACKUPS}")
    oldest.unlink(missing_ok=True)
    for index in range(_EVENT_BACKUPS - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        if source.exists():
            source.replace(path.with_name(f"{path.name}.{index + 1}"))
    path.replace(path.with_name(f"{path.name}.1"))


def _safe(value: Any, depth: int = 0) -> Any:
    if depth >= 4:
        return "<max-depth>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 500 else value[:497] + "..."
    if isinstance(value, dict):
        return {
            str(key): "<redacted>"
            if any(token in str(key).lower() for token in _SECRET_TOKENS)
            else _safe(item, depth + 1)
            for key, item in list(value.items())[:50]
        }
    if isinstance(value, (list, tuple, set)):
        return [_safe(item, depth + 1) for item in list(value)[:50]]
    return _safe(str(value), depth + 1)


def emit_event(event: str, **fields: Any) -> None:
    """Append one bounded JSON event; telemetry failures never stop a bot."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "unix_time": time.time(),
        "monotonic_ns": time.monotonic_ns(),
        "event": event,
        "session_id": _SESSION_ID,
        "pid": os.getpid(),
        "thread": threading.current_thread().name,
        "operation_id": _CURRENT_OPERATION.get(),
        "phase": _CURRENT_PHASE.get(),
        "task": _CURRENT_TASK.get(),
    }
    record.update({key: _safe(value) for key, value in fields.items()})
    try:
        line = json.dumps(record, separators=(",", ":"), sort_keys=True)
        with _LOCK:
            _RECENT_EVENTS.append(record)
            path = _event_path()
            _rotate_event_journal(path, len(line.encode("utf-8")) + 1)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception:
        pass


def _artifact_hashes(output_dir: Path) -> Dict[str, str]:
    hashes: Dict[str, str] = {}
    mods_dir = output_dir.parent / "game" / "mods"
    if not mods_dir.is_dir():
        return hashes
    for pattern in ("baritone-api-fabric-*.jar", "baritone-api-bridge-*.jar"):
        for path in sorted(mods_dir.glob(pattern)):
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest().upper()
                hashes[path.name] = digest
            except OSError:
                continue
    return hashes


def configure_event_journal(output_dir: str | Path) -> None:
    """Point the journal at a run directory and record runtime identity."""
    global _OUTPUT_DIR
    _OUTPUT_DIR = Path(output_dir).resolve()
    emit_event(
        "session_start",
        run_dir=str(_OUTPUT_DIR),
        python=platform.python_version(),
        platform=platform.platform(),
        artifacts=_artifact_hashes(_OUTPUT_DIR),
    )


def _inventory_counts(payload: Dict[str, Any]) -> Dict[str, int]:
    raw = payload.get("items")
    if raw is None:
        raw = payload.get("inventory")
    if raw is None:
        raw = payload.get("slots")
    counts: Dict[str, int] = {}
    if isinstance(raw, dict):
        for item_id, value in raw.items():
            if isinstance(value, dict):
                count = value.get("count", value.get("quantity", 0))
                normalized_id = value.get("id", value.get("item", item_id))
            else:
                count = value
                normalized_id = item_id
            try:
                counts[str(normalized_id)] = counts.get(str(normalized_id), 0) + int(count)
            except (TypeError, ValueError):
                continue
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id", item.get("item", item.get("name")))
            if not item_id:
                continue
            try:
                count = int(item.get("count", item.get("quantity", 0)))
            except (TypeError, ValueError):
                continue
            counts[str(item_id)] = counts.get(str(item_id), 0) + count
    return counts


def observe_inventory_response(payload: Dict[str, Any]) -> None:
    """Emit item deltas from an inventory response already requested by code."""
    global _INVENTORY_COUNTS
    counts = _inventory_counts(payload)
    if not counts and _INVENTORY_COUNTS is None:
        return
    with _INVENTORY_LOCK:
        previous = _INVENTORY_COUNTS
        _INVENTORY_COUNTS = counts
    if previous is None:
        emit_event("inventory_baseline", item_counts=counts, total_items=sum(counts.values()))
        return
    task = (_CURRENT_TASK.get() or "").lower()
    for item_id in sorted(set(previous) | set(counts)):
        before = previous.get(item_id, 0)
        after = counts.get(item_id, 0)
        delta = after - before
        if delta == 0:
            continue
        active_container = (
            _ACTIVE_CONTAINER.get("position")
            if _ACTIVE_CONTAINER
            and time.monotonic_ns() - _ACTIVE_CONTAINER["seen_ns"]
            < 60_000_000_000
            else None
        )
        movement = "inventory_change"
        if active_container is not None:
            movement = "player_to_storage" if delta < 0 else "storage_to_player"
        elif delta < 0 and any(token in task for token in ("bank", "deposit", "store")):
            movement = "player_to_storage"
        elif delta > 0 and "withdraw" in task:
            movement = "storage_to_player"
        elif delta > 0 and any(token in task for token in ("mine", "gather", "harvest")):
            movement = "world_to_player"
        elif delta < 0 and "death" in task:
            movement = "death_loss"
        emit_event(
            "item_delta",
            item_id=item_id,
            before=before,
            after=after,
            delta=delta,
            movement=movement,
            container=active_container,
        )
        if active_container is not None:
            emit_event(
                "storage_transfer",
                item_id=item_id,
                count=abs(delta),
                direction=movement,
                container=active_container,
            )


def observe_state_response(payload: Dict[str, Any]) -> None:
    """Sample existing state responses at most once every five seconds."""
    global _LAST_STATE_SAMPLE_NS, _PLAYER_DEAD, _LAST_POSITION, _LAST_CHUNK
    now_ns = time.monotonic_ns()
    is_dead = bool(payload.get("is_dead")) or (
        payload.get("health") is not None and float(payload.get("health")) <= 0
    )
    if is_dead and not _PLAYER_DEAD:
        _flush_death_blackbox(payload)
    _PLAYER_DEAD = is_dead
    if now_ns - _LAST_STATE_SAMPLE_NS < 5_000_000_000:
        return
    _LAST_STATE_SAMPLE_NS = now_ns
    position = payload.get("block_position", payload.get("position"))
    chunk = None
    if isinstance(position, dict):
        try:
            chunk = (int(float(position["x"])) // 16, int(float(position["z"])) // 16)
            _LAST_POSITION = position
        except (KeyError, TypeError, ValueError):
            chunk = None
    if chunk is not None and chunk != _LAST_CHUNK:
        emit_event(
            "chunk_entered",
            previous_chunk=list(_LAST_CHUNK) if _LAST_CHUNK else None,
            chunk=list(chunk),
            position=position,
        )
        _LAST_CHUNK = chunk
    emit_event(
        "state_sample",
        position=position,
        chunk=list(chunk) if chunk is not None else None,
        health=payload.get("health"),
        food=payload.get("food", payload.get("food_level")),
        dimension=payload.get("dimension"),
        is_dead=payload.get("is_dead"),
        active_process=payload.get("active_process", payload.get("process")),
    )
    if _ACTIVE_NAVIGATION and isinstance(position, dict):
        goal = _ACTIVE_NAVIGATION.get("goal")
        if isinstance(goal, dict) and all(key in goal for key in ("x", "y", "z")):
            try:
                distance = sum(
                    (float(position[key]) - float(goal[key])) ** 2
                    for key in ("x", "y", "z")
                ) ** 0.5
                previous = _ACTIVE_NAVIGATION.get("last_distance")
                _ACTIVE_NAVIGATION["last_distance"] = distance
                improvement = (
                    float(previous) - distance if previous is not None else None
                )
                if improvement is not None and improvement < 0.25:
                    _ACTIVE_NAVIGATION["stalled_samples"] = (
                        _ACTIVE_NAVIGATION.get("stalled_samples", 0) + 1
                    )
                else:
                    _ACTIVE_NAVIGATION["stalled_samples"] = 0
                emit_event(
                    "navigation_progress",
                    navigation_id=_ACTIVE_NAVIGATION["navigation_id"],
                    route=_ACTIVE_NAVIGATION["route"],
                    goal=goal,
                    position=position,
                    remaining_distance=round(distance, 3),
                    distance_improvement=(round(improvement, 3) if improvement is not None else None),
                    elapsed_ms=round(
                        (now_ns - _ACTIVE_NAVIGATION["started_ns"]) / 1_000_000,
                        3,
                    ),
                )
                if _ACTIVE_NAVIGATION.get("stalled_samples") == 6:
                    emit_event(
                        "navigation_stalled",
                        navigation_id=_ACTIVE_NAVIGATION["navigation_id"],
                        route=_ACTIVE_NAVIGATION["route"],
                        goal=goal,
                        position=position,
                        remaining_distance=round(distance, 3),
                        stalled_seconds=30,
                    )
            except (TypeError, ValueError, KeyError):
                pass


def observe_entities_response(payload: Dict[str, Any]) -> None:
    """Summarize nearby entities from responses already requested by the bot."""
    global _LAST_ENTITY_SAMPLE_NS
    now_ns = time.monotonic_ns()
    if now_ns - _LAST_ENTITY_SAMPLE_NS < 10_000_000_000:
        return
    raw = payload.get("entities", payload.get("items", []))
    if not isinstance(raw, list):
        return
    _LAST_ENTITY_SAMPLE_NS = now_ns
    by_type: Dict[str, int] = {}
    nearest_distance: Optional[float] = None
    hostile_tokens = (
        "zombie",
        "skeleton",
        "creeper",
        "spider",
        "witch",
        "drowned",
        "husk",
        "pillager",
        "phantom",
        "warden",
        "enderman",
        "slime",
    )
    hostile_count = 0
    for entity in raw:
        if not isinstance(entity, dict):
            continue
        entity_type = str(entity.get("type", entity.get("id", "unknown")))
        by_type[entity_type] = by_type.get(entity_type, 0) + 1
        if any(token in entity_type.lower() for token in hostile_tokens):
            hostile_count += 1
        if _LAST_POSITION is not None:
            entity_position = entity.get("position", entity)
            if isinstance(entity_position, dict):
                try:
                    distance = sum(
                        (
                            float(entity_position[key])
                            - float(_LAST_POSITION[key])
                        )
                        ** 2
                        for key in ("x", "y", "z")
                    ) ** 0.5
                    nearest_distance = (
                        distance
                        if nearest_distance is None
                        else min(nearest_distance, distance)
                    )
                except (KeyError, TypeError, ValueError):
                    pass
    emit_event(
        "entity_sample",
        total_entities=len(raw),
        hostile_entities=hostile_count,
        nearest_entity_distance=(
            round(nearest_distance, 3) if nearest_distance is not None else None
        ),
        by_type=by_type,
    )


def observe_command_response(
    route: str, payload: Dict[str, Any], response: Dict[str, Any]
) -> None:
    """Track navigation goals and active container context from commands."""
    global _ACTIVE_NAVIGATION, _ACTIVE_CONTAINER
    effective_route = route
    effective_payload = payload
    if route == "command":
        effective_route = str(payload.get("command", route))
        candidate = payload.get("params")
        if isinstance(candidate, dict):
            effective_payload = candidate
    if effective_route in {"goto", "explore", "mine"}:
        goal = None
        if effective_route == "goto" and all(
            key in effective_payload for key in ("x", "y", "z")
        ):
            goal = {
                key: effective_payload[key] for key in ("x", "y", "z")
            }
        _ACTIVE_NAVIGATION = {
            "navigation_id": uuid.uuid4().hex,
            "route": effective_route,
            "goal": goal,
            "started_ns": time.monotonic_ns(),
            "last_distance": None,
            "stalled_samples": 0,
        }
        emit_event(
            "navigation_started",
            navigation_id=_ACTIVE_NAVIGATION["navigation_id"],
            route=effective_route,
            goal=goal,
            command_payload=effective_payload,
        )
    elif effective_route in {"cancel", "command/cancel"}:
        if _ACTIVE_NAVIGATION:
            emit_event(
                "navigation_ended",
                navigation_id=_ACTIVE_NAVIGATION["navigation_id"],
                route=_ACTIVE_NAVIGATION["route"],
                outcome="cancelled",
            )
        _ACTIVE_NAVIGATION = None
    elif effective_route in {"open_container", "open_chest"}:
        if all(key in effective_payload for key in ("x", "y", "z")):
            _ACTIVE_CONTAINER = {
                "position": [
                    effective_payload["x"],
                    effective_payload["y"],
                    effective_payload["z"],
                ],
                "seen_ns": time.monotonic_ns(),
            }
            emit_event(
                "container_opened",
                position=_ACTIVE_CONTAINER["position"],
                response=response,
            )


def _flush_death_blackbox(state: Dict[str, Any]) -> None:
    cutoff = time.time() - 60.0
    with _LOCK:
        records = [
            dict(record)
            for record in _RECENT_EVENTS
            if float(record.get("unix_time", 0)) >= cutoff
        ]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = _OUTPUT_DIR / "telemetry" / f"death_blackbox_{timestamp}.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, separators=(",", ":")) + "\n")
            handle.write(
                json.dumps(
                    {
                        "event": "death_state",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "session_id": _SESSION_ID,
                        "state": _safe(state),
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )
        emit_event(
            "death_blackbox_flushed",
            path=str(path),
            buffered_events=len(records),
        )
    except OSError:
        pass


@dataclass
class Operation:
    operation_id: str
    parent_operation_id: Optional[str]
    kind: str
    name: str
    started_ns: int
    operation_token: contextvars.Token
    phase_token: Optional[contextvars.Token] = None
    task_token: Optional[contextvars.Token] = None


def begin_operation(
    kind: str,
    name: str,
    *,
    phase: Optional[str] = None,
    task: Optional[str] = None,
    **fields: Any,
) -> Operation:
    parent = _CURRENT_OPERATION.get()
    operation_id = uuid.uuid4().hex
    operation_token = _CURRENT_OPERATION.set(operation_id)
    phase_token = _CURRENT_PHASE.set(phase) if phase is not None else None
    task_token = _CURRENT_TASK.set(task) if task is not None else None
    operation = Operation(
        operation_id=operation_id,
        parent_operation_id=parent,
        kind=kind,
        name=name,
        started_ns=time.monotonic_ns(),
        operation_token=operation_token,
        phase_token=phase_token,
        task_token=task_token,
    )
    emit_event(
        f"{kind}_start",
        name=name,
        parent_operation_id=parent,
        **fields,
    )
    return operation


def end_operation(
    operation: Operation,
    outcome: str,
    *,
    reason: str = "",
    **fields: Any,
) -> None:
    duration_ms = (time.monotonic_ns() - operation.started_ns) / 1_000_000
    emit_event(
        f"{operation.kind}_end",
        name=operation.name,
        parent_operation_id=operation.parent_operation_id,
        outcome=outcome,
        reason=reason,
        duration_ms=round(duration_ms, 3),
        **fields,
    )
    if operation.task_token is not None:
        _CURRENT_TASK.reset(operation.task_token)
    if operation.phase_token is not None:
        _CURRENT_PHASE.reset(operation.phase_token)
    _CURRENT_OPERATION.reset(operation.operation_token)
