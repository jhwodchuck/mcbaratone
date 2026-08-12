"""Persistent fail-closed wrapper for one approved bounded worker."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


class WorkerRunnerError(RuntimeError):
    """Raised when a bounded worker cannot be started or kept healthy."""


#: Consecutive *rapid* failures allowed before giving up.
#:
#: Counting every restart over a worker's lifetime is the wrong measure for an
#: eight-hour job. Bot17's farmer completes a full harvest -- 16 crops cut,
#: replanted and banked -- and then dies on a bridge stall, once per cycle. It
#: is flaky, not broken, and a lifetime cap of 8 would have retired a
#: productive worker inside twenty minutes. A run that lasted long enough to do
#: real work resets the counter; only a worker that dies immediately, over and
#: over, is actually crash-looping.
MAX_WORKER_RESTARTS = 8
#: A run at least this long counts as progress, not a crash loop.
PRODUCTIVE_RUN_SECONDS = 120.0
#: Restarts inside this window before the backoff lengthens.
RESTART_BURST_WINDOW = 300.0
RESTART_BACKOFF_SECONDS = 60.0


def next_restart_count(restarts: int, ran_for: float) -> int:
    """Return the next rapid-restart count or reject a crash loop."""
    if ran_for >= PRODUCTIVE_RUN_SECONDS:
        return 0
    if restarts >= MAX_WORKER_RESTARTS:
        raise WorkerRunnerError(
            f"Bounded worker exhausted {MAX_WORKER_RESTARTS} rapid restarts "
            f"(last run {ran_for:.0f}s)"
        )
    return restarts + 1


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--server-properties", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--bot", required=True)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    value = manifest["bots"][args.bot]
    worker = value.get("worker")
    if not worker or not worker.get("approved"):
        raise WorkerRunnerError(f"No approved bounded worker for {args.bot}")
    command = [
        sys.executable if part == "{python}" else str(part)
        for part in worker["command"]
    ]
    working_directory = args.repo / worker["working_directory"]
    stop_file = args.repo / "runs" / "live_ops" / f"stop-{args.bot}"
    process: subprocess.Popen[bytes] | None = None
    try:
        restarts = 0
        restart_times: list[float] = []
        while True:
            launched_at = time.monotonic()
            process = subprocess.Popen(
                command,
                cwd=working_directory,
                stdin=subprocess.DEVNULL,
                env=dict(os.environ),
            )
            while process.poll() is None:
                if stop_file.exists():
                    break
                time.sleep(args.poll_seconds)

            code = process.returncode
            if stop_file.exists() or code == 0:
                return 0
            ran_for = time.monotonic() - launched_at
            restarts = next_restart_count(restarts, ran_for)
            now = time.monotonic()
            restart_times = [t for t in restart_times if now - t < RESTART_BURST_WINDOW]
            restart_times.append(now)
            delay = RESTART_BACKOFF_SECONDS if len(restart_times) > 3 else 5.0
            print(
                f"WORKER: bounded worker exited with code {code}; "
                f"restart {restarts}/{MAX_WORKER_RESTARTS} in {delay:.0f}s",
                flush=True,
            )
            time.sleep(delay)
    finally:
        if process is not None:
            _terminate(process)
        stop_file.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
