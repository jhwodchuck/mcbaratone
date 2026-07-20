"""Credential-free operator monitor utilities for the autonomous bot."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from typing import Protocol


CHECKPOINT_FILES = (
    "spawn_to_dragon_checkpoint.json",
    "endgame_checkpoint.json",
)
CHECKPOINT_PREFIX = "checkpoint_"
CHECKPOINT_GLOB = f"{CHECKPOINT_PREFIX}*.json"

METRICS_FILES = ("metrics_*.json",)


def _iso_utc_from_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec="seconds")


class ProcessProvider(Protocol):
    """Abstract process-probe interface used by the operator monitor."""

    def is_running(self, pid: int) -> bool:
        ...

    def command_for_pid(self, pid: int) -> Optional[str]:
        ...

    def find_matching_processes(self, command_fragments: Sequence[str]) -> List[Tuple[int, Optional[str]]]:
        ...


class ProcessProbeResult(dict):
    """Typed dict-like container for process pointer status."""


class TcpProbeResult(dict):
    """Typed dict-like container for TCP probe results."""


class WindowsProcessProvider:
    """Windows process provider with a strict, injectable-friendly interface."""

    def is_running(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def command_for_pid(self, pid: int) -> Optional[str]:
        # Command-line lookup is optional and intentionally permissive. We try
        # to get command context on Windows for safer validation, but we never
        # fail if this is unavailable.
        if os.name != "nt":
            return None

        command = None
        try:
            cp = subprocess.run(
                ["wmic", "process", "where", f"ProcessId={pid}", "get", "CommandLine", "/format:list"],
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            for line in cp.stdout.splitlines():
                if line.lower().startswith("commandline="):
                    candidate = line.split("=", 1)[1].strip()
                    if candidate:
                        command = candidate
                    break
        except Exception:
            return None

        return command

    def find_matching_processes(self, command_fragments: Sequence[str]) -> List[Tuple[int, Optional[str]]]:
        # Best-effort process enumeration for safety checks.
        if os.name != "nt":
            return []

        fragments = [fragment.lower() for fragment in command_fragments]
        if not fragments:
            return []

        try:
            cp = subprocess.run(
                [
                    "wmic",
                    "process",
                    "where",
                    "name like '%python%'",
                    "get",
                    "ProcessId,CommandLine",
                    "/format:csv",
                ],
                capture_output=True,
                text=True,
                timeout=1.0,
            )
        except Exception:
            return []

        results: List[Tuple[int, Optional[str]]] = []
        # `WMIC` CSV output has a header, then rows, then blanks.
        for line in cp.stdout.splitlines():
            line = line.strip()
            if "," not in line:
                continue
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 3:
                continue
            # Expected schema: Node,CommandLine,ProcessId
            command = parts[1]
            pid_text = parts[2]
            if not pid_text.isdigit():
                continue
            try:
                pid = int(pid_text)
            except ValueError:
                continue
            command_text = (command or "").lower()
            if any(fragment in command_text for fragment in fragments):
                results.append((pid, command))
        return results


def _default_process_provider() -> ProcessProvider:
    return WindowsProcessProvider()


def _default_tcp_probe(host: str, port: int, timeout: float = 1.0) -> TcpProbeResult:
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            elapsed_ms = (time.perf_counter() - start) * 1000.0
        return TcpProbeResult({
            "reachable": True,
            "latency_ms": round(elapsed_ms, 3),
            "error": None,
            "host": host,
            "port": port,
        })
    except Exception as exc:
        return TcpProbeResult({
            "reachable": False,
            "latency_ms": None,
            "error": str(exc),
            "host": host,
            "port": port,
        })


def _safe_now() -> float:
    return time.time()


def _read_json_file(path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload, None
        return None, "json payload is not an object"
    except Exception as exc:
        return None, str(exc)


def _safe_resolve_pointer_path(raw_value: str, workspace: Path) -> Path:
    candidate = Path(raw_value.strip())
    if not candidate.is_absolute():
        candidate = (workspace / candidate).resolve()
    else:
        try:
            candidate = candidate.resolve()
        except FileNotFoundError:
            # Resolve on non-existing file may fail on some OSes; leave relative
            # resolution conservative and explicit.
            candidate = candidate
    return candidate


def _is_within(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def _discover_artifacts(workspace: Path, patterns: Sequence[str]) -> List[Path]:
    discovered: List[Path] = []
    for pattern in patterns:
        discovered.extend(sorted(workspace.glob(pattern)))
    # Remove duplicates while preserving order.
    deduped: List[Path] = []
    for path in discovered:
        if path not in deduped:
            deduped.append(path)
    return deduped


def _collect_checkpoint_records(workspace: Path) -> Dict[str, Any]:
    patterns = list(CHECKPOINT_FILES) + [CHECKPOINT_GLOB]
    found: List[Dict[str, Any]] = []

    for path in _discover_artifacts(workspace, patterns):
        payload, error = _read_json_file(path) if path.is_file() else (None, "not a file")
        stat = path.stat()
        found.append({
            "path": str(path),
            "exists": path.is_file(),
            "mtime": stat.st_mtime,
            "mtime_utc": _iso_utc_from_timestamp(stat.st_mtime),
            "size": stat.st_size,
            "parse_error": error,
            "payload": payload,
        })

    found.sort(key=lambda item: item["mtime"], reverse=True)

    latest = None
    for item in found:
        if item["payload"] is not None:
            latest = item
            break

    return {
        "found": found,
        "latest": latest,
    }


def _collect_metrics_records(workspace: Path) -> Dict[str, Any]:
    found: List[Dict[str, Any]] = []
    for path in _discover_artifacts(workspace, METRICS_FILES):
        if not path.is_file():
            continue
        payload, error = _read_json_file(path)
        stat = path.stat()
        found.append({
            "path": str(path),
            "exists": True,
            "mtime": stat.st_mtime,
            "mtime_utc": _iso_utc_from_timestamp(stat.st_mtime),
            "size": stat.st_size,
            "parse_error": error,
            "payload": payload,
        })

    found.sort(key=lambda item: item["mtime"], reverse=True)
    latest = None
    for item in found:
        if item["payload"] is not None:
            latest = item
            break

    return {"found": found, "latest": latest}


def _collect_log_candidates(workspace: Path) -> List[Path]:
    candidates = []
    for pattern in ("autonomous-*.out.log", "Logs/autonomous-*.out.log", "logs/autonomous-*.out.log"):
        candidates.extend(sorted(workspace.glob(pattern)))
    candidates = [path for path in candidates if path.is_file()]
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates


def _collect_log_pointer_status(pointer_path: Path, workspace: Path) -> Dict[str, Any]:
    status: Dict[str, Any] = {
        "path": str(pointer_path),
        "target_path": None,
        "exists": False,
        "valid": False,
        "is_latest": False,
        "reason": None,
        "mtime_utc": None,
    }

    if not pointer_path.exists():
        status["reason"] = "missing"
        return status
    status["exists"] = True
    if not pointer_path.is_file():
        status["reason"] = "not a file"
        return status
    if not _is_within(workspace, pointer_path):
        status["reason"] = "outside workspace"
        return status

    try:
        target_text = pointer_path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        status["reason"] = f"failed to read pointer: {exc}"
        return status

    if not target_text:
        status["reason"] = "empty"
        return status

    target = _safe_resolve_pointer_path(target_text, workspace)
    status["target_path"] = str(target)
    if not target.exists():
        status["reason"] = "target does not exist"
        return status
    if not target.is_file():
        status["reason"] = "target is not a file"
        return status
    if not _is_within(workspace, target):
        status["reason"] = "target outside workspace"
        return status

    latest_logs = _collect_log_candidates(workspace)
    if latest_logs:
        is_latest = target.resolve() == latest_logs[0].resolve()
        status["is_latest"] = is_latest
        if not is_latest:
            status["reason"] = "pointer stale or non-active log"
    else:
        status["reason"] = "no discovered logs"

    status["valid"] = status["exists"] and status["is_latest"]
    try:
        status["mtime_utc"] = _iso_utc_from_timestamp(target.stat().st_mtime)
    except OSError:
        status["mtime_utc"] = None
    return status


def _tail_text(path: Optional[Path], max_bytes: int = 8192) -> Optional[str]:
    if path is None or not path.is_file():
        return None
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            lines = handle.read().decode("utf-8", errors="replace").splitlines()
        return lines[-1] if lines else None
    except OSError:
        return None


def _collect_pid_pointer_status(pointer_path: Path, provider: ProcessProvider, command_fragment: str) -> Dict[str, Any]:
    status: Dict[str, Any] = {
        "raw": None,
        "pid": None,
        "running": False,
        "valid": False,
        "command_line": None,
        "reason": None,
    }

    if not pointer_path.exists():
        status["reason"] = "missing"
        return status
    if not pointer_path.is_file():
        status["reason"] = "not a file"
        return status

    raw = pointer_path.read_text(encoding="utf-8").strip()
    status["raw"] = raw
    if not raw:
        status["reason"] = "empty"
        return status

    try:
        pid = int(raw)
    except ValueError:
        status["reason"] = "non-numeric"
        return status

    if pid <= 0:
        status["reason"] = "invalid pid"
        return status

    status["pid"] = pid

    if not provider.is_running(pid):
        status["reason"] = "not running"
        return status
    status["running"] = True

    command = provider.command_for_pid(pid)
    if command is not None:
        status["command_line"] = command
        if command_fragment and command_fragment.lower() not in command.lower():
            status["reason"] = "command mismatch"
            return status

    status["valid"] = True
    return status


@dataclass
class OperatorMonitorConfig:
    workspace: Path
    host: str = "localhost"
    port: int = 5555
    expected_command_fragment: str = "spawn_to_dragon.py"
    pid_pointer: str = ".current_run_pid"
    log_pointer: str = ".current_run_log"
    process_provider: Optional[ProcessProvider] = None
    tcp_probe: Optional[Callable[[str, int, float], TcpProbeResult]] = None
    probe_timeout: float = 1.0
    now_fn: Callable[[], float] = _safe_now


@dataclass
class OperatorMonitor:
    config: OperatorMonitorConfig

    def __post_init__(self) -> None:
        self._process_provider = self.config.process_provider or _default_process_provider()
        self._tcp_probe = self.config.tcp_probe or _default_tcp_probe
        self.workspace = self.config.workspace

    def collect(self) -> Dict[str, Any]:
        now = self.config.now_fn()
        workspace = self.workspace.resolve()
        timestamp = _iso_utc_from_timestamp(now)

        pid_pointer_path = _safe_resolve_pointer_path(self.config.pid_pointer, workspace)
        log_pointer_path = _safe_resolve_pointer_path(self.config.log_pointer, workspace)

        process_pointer = _collect_pid_pointer_status(
            pid_pointer_path,
            self._process_provider,
            self.config.expected_command_fragment,
        )

        log_pointer = _collect_log_pointer_status(log_pointer_path, workspace)

        bridge = self._tcp_probe(self.config.host, self.config.port, self.config.probe_timeout)

        checkpoints = _collect_checkpoint_records(workspace)
        metrics = _collect_metrics_records(workspace)

        process_matches = []
        if process_pointer["pid"] is not None:
            command_line = process_pointer.get("command_line")
            process_matches.append({
                "pid": process_pointer["pid"],
                "command_line": command_line,
                "from_pointer": True,
            })
        for pid, command in self._process_provider.find_matching_processes([self.config.expected_command_fragment]):
            if any(item["pid"] == pid for item in process_matches):
                continue
            process_matches.append({
                "pid": pid,
                "command_line": command,
                "from_pointer": False,
            })

        pointer_valid = bool(process_pointer["valid"])
        bridge_reachable = bool(bridge.get("reachable"))
        if pointer_valid and bridge_reachable:
            overall_state = "running"
        elif pointer_valid or bridge_reachable:
            overall_state = "degraded"
        elif process_matches:
            overall_state = "unknown"
        else:
            overall_state = "stopped"

        selected_log = None
        if log_pointer.get("valid") and log_pointer.get("target_path"):
            selected_log = Path(log_pointer["target_path"])
        else:
            candidates = _collect_log_candidates(workspace)
            selected_log = candidates[0] if candidates else None

        return {
            "schema_version": "1.0",
            "timestamp_utc": timestamp,
            "overall_state": overall_state,
            "workspace": str(workspace),
            "bridge": bridge,
            "process": {
                "pointer": process_pointer,
                "log_pointer": log_pointer,
                "running_candidates": process_matches,
            },
            "checkpoints": checkpoints,
            "metrics": metrics,
            "evidence": {
                "selected_log": str(selected_log) if selected_log else None,
                "last_log_line": _tail_text(selected_log),
                "stale_reasons": [
                    reason
                    for reason in (process_pointer.get("reason"), log_pointer.get("reason"))
                    if reason
                ],
            },
        }


__all__ = [
    "OperatorMonitor",
    "OperatorMonitorConfig",
    "WindowsProcessProvider",
    "ProcessProvider",
    "TcpProbeResult",
    "ProcessProbeResult",
    "CHECKPOINT_FILES",
    "CHECKPOINT_PREFIX",
]
