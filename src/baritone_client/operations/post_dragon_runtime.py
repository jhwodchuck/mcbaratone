"""Runtime wiring for one dedicated post-dragon bot worker."""

from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any, Mapping

from ..automator.resource_manager import ResourceManager
from ..automator.state_manager import StateManager
from ..core.client import Client
from ..transport.transport import TcpTransport
from .post_dragon_worker import (
    PostDragonWorker,
    non_op_survival_hold_reason,
)


def load_mapping(path: Path) -> Mapping[str, Any]:
    """Load a JSON object from an operational configuration file."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"expected an object in {path}")
    return value


def atomic_status(path: Path, **values: Any) -> None:
    """Publish one complete worker status without exposing partial JSON."""
    values["timestamp"] = time.time()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(values, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def snapshot(client: Client) -> Mapping[str, Any]:
    """Read and normalize one bridge state snapshot."""
    value = client.transport.dispatch("get_state", {})
    if not isinstance(value, Mapping):
        return {}
    data = value.get("data", value)
    return data if isinstance(data, Mapping) else {}


class PostDragonBotRuntime:
    """Own status and lifecycle for one fail-closed configured bot."""

    def __init__(
        self,
        *,
        bot_name: str,
        bridge_port: int,
        checkpoint_dir: Path,
        runtime_dir: Path,
        hours: float,
    ) -> None:
        self.bot_name = bot_name
        self.runtime_dir = runtime_dir
        self.deadline = time.time() + max(60.0, hours * 3600.0)
        self.status_path = runtime_dir / f"{bot_name}.json"
        self.pid_path = runtime_dir / f"{bot_name}.pid"
        self.stop_path = runtime_dir / f"stop-{bot_name}"
        self.client = Client(TcpTransport("localhost", bridge_port, timeout=8.0))
        self.state = StateManager(checkpoint_dir)
        if not self.state.load_checkpoint():
            raise RuntimeError(
                f"checkpoint unavailable for {bot_name}: {checkpoint_dir}"
            )
        self.resources = ResourceManager(self.client)
        self.worker = PostDragonWorker(
            self.client,
            self.resources,
            self.state,
            bot_name=bot_name,
        )

    def _publish(self, state: str, detail: str, **values: Any) -> None:
        live = snapshot(self.client)
        atomic_status(
            self.status_path,
            bot=self.bot_name,
            pid=os.getpid(),
            state=state,
            detail=detail,
            dimension=live.get("dimension"),
            position=live.get("block_position", live.get("position")),
            health=live.get("health"),
            food=live.get("food_level", live.get("food")),
            is_dead=live.get("is_dead"),
            is_pathing=live.get("is_pathing"),
            city=list(self.worker.city),
            **values,
        )

    def run(self) -> int:
        """Run until the configured deadline, stop marker, or fatal error."""
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.stop_path.unlink(missing_ok=True)
        self.pid_path.write_text(str(os.getpid()), encoding="ascii")
        turns = progress = 0
        try:
            while time.time() < self.deadline and not self.stop_path.exists():
                self._publish(
                    "active",
                    "selecting the next bounded post-dragon turn",
                    turns=turns,
                    productive_turns=progress,
                )
                turn = self.worker.run_turn()
                turns += 1
                progress += int(turn.progressed)
                self._publish(
                    turn.state,
                    turn.detail,
                    progressed=turn.progressed,
                    before=turn.before,
                    after=turn.after,
                    turns=turns,
                    productive_turns=progress,
                )
                if turn.state == "manual_recovery_hold":
                    return 0
                delay = 3.0 if turn.state in {"working", "searching"} else 10.0
                time.sleep(delay)
            return 0
        except Exception as error:
            atomic_status(
                self.status_path,
                bot=self.bot_name,
                pid=os.getpid(),
                state="failed",
                detail=f"{type(error).__name__}: {error}",
                turns=turns,
                productive_turns=progress,
            )
            return 1
        finally:
            self.close()

    def close(self) -> None:
        """Cancel the active goal and release the bridge connection."""
        try:
            live = snapshot(self.client)
            if non_op_survival_hold_reason(live, self.bot_name) is None:
                self.client.transport.dispatch("cancel", {})
        except Exception:
            pass
        self.client.shutdown()
        self.pid_path.unlink(missing_ok=True)


def run_post_dragon_bot(
    *,
    bot_name: str,
    hours: float,
    manifest_path: Path,
    server_properties: Path,
    runtime_dir: Path,
    checkpoint_dir: Path,
) -> int:
    """Resolve one manifest entry and run its reusable post-dragon runtime."""
    # Retained for compatibility with existing command lines. The safe runtime
    # never opens an administrative server connection.
    del server_properties
    manifest = load_mapping(manifest_path)
    bot = manifest.get("bots", {}).get(bot_name)
    if not isinstance(bot, Mapping):
        raise ValueError(f"unknown bot {bot_name!r}")
    runtime = PostDragonBotRuntime(
        bot_name=bot_name,
        bridge_port=int(bot["bridge_port"]),
        checkpoint_dir=checkpoint_dir,
        runtime_dir=runtime_dir,
        hours=hours,
    )
    return runtime.run()


__all__ = [
    "PostDragonBotRuntime",
    "atomic_status",
    "load_mapping",
    "run_post_dragon_bot",
    "snapshot",
]
