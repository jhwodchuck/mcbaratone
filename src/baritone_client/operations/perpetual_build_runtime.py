"""Lifecycle and durable safety holds for one perpetual Survival builder."""

from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Any, Mapping

from ..common.inventory import reset_inventory_cache
from ..core.client import Client
from ..transport.transport import TcpTransport
from .perpetual_builder import (
    BuildProgress,
    BuildTurn,
    PerpetualBuilder,
    non_op_survival_hold_reason,
)
from .perpetual_build_plan import LaneAssignment, ROUTES, fleet_lanes
from .post_dragon_runtime import atomic_status, load_mapping, snapshot


def needs_food_provisioning(*, health: float, food: int, reserve: int) -> bool:
    """Return whether a bot must hold for an external food recovery."""
    return reserve < 8 and (food < 16 or health < 10)


class PerpetualBuildRuntime:
    """Construct until stopped or a durable manual-recovery hold is reached."""

    def __init__(
        self,
        *,
        bot_name: str,
        bridge_port: int,
        assignment: LaneAssignment,
        runtime_dir: Path,
        hours: float = 0,
    ) -> None:
        self.bot_name = bot_name
        self.assignment = assignment
        self.runtime_dir = runtime_dir
        self.deadline = None if hours <= 0 else time.time() + hours * 3600
        self.status_path = runtime_dir / f"{bot_name}.json"
        self.pid_path = runtime_dir / f"{bot_name}.pid"
        self.stop_path = runtime_dir / f"stop-{bot_name}"
        self.checkpoint_path = runtime_dir / "checkpoints" / f"{bot_name}.json"
        self.client = Client(TcpTransport("localhost", bridge_port, timeout=8.0))
        progress = BuildProgress.load(
            self.checkpoint_path,
            expected_y=assignment.expected_y,
        )
        self.worker = PerpetualBuilder(
            self.client,
            assignment,
            progress,
            checkpoint_path=self.checkpoint_path,
        )

    def _recover(self, client: Client) -> BuildTurn | None:
        """Respawn normally, then stop for manual Survival repositioning."""
        live = snapshot(client)
        unsafe = non_op_survival_hold_reason(live, self.bot_name)
        if unsafe is not None:
            return self.worker.manual_recovery_hold(unsafe)
        try:
            health = float(live.get("health", 0) or 0)
        except (TypeError, ValueError):
            return self.worker.manual_recovery_hold(
                "bridge survival telemetry is invalid"
            )
        dead = bool(live.get("is_dead")) or health <= 0
        if not dead:
            return None
        client.transport.dispatch("cancel", {})
        client.transport.dispatch("respawn", {})
        reset_inventory_cache()
        for _ in range(20):
            time.sleep(0.5)
            live = snapshot(client)
            unsafe = non_op_survival_hold_reason(live, self.bot_name)
            if unsafe is not None:
                return self.worker.manual_recovery_hold(unsafe)
            try:
                health = float(live.get("health", 0) or 0)
            except (TypeError, ValueError):
                return self.worker.manual_recovery_hold(
                    "bridge survival telemetry is invalid after respawn"
                )
            if not bool(live.get("is_dead")) and health > 0:
                return self.worker.manual_recovery_hold(
                    "builder respawned normally; manual Survival return to the "
                    "verified Overworld lane is required"
                )
        return self.worker.manual_recovery_hold(
            "builder did not leave the death screen after normal respawn"
        )

    def _publish(self, state: str, detail: str, **values: Any) -> None:
        live = snapshot(self.client)
        progress = self.worker.progress
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
            lane={
                "anchor_x": self.assignment.anchor_x,
                "anchor_z": self.assignment.anchor_z,
            },
            slot=progress.slot,
            route_index=progress.route_index,
            route_slot=progress.route_slot,
            route_direction_x=ROUTES[progress.route_index][0],
            route_row_offset=ROUTES[progress.route_index][1],
            reroutes=progress.reroutes,
            consecutive_skips=progress.consecutive_skips,
            nonproductive_cells=progress.nonproductive_cells,
            blocks_placed=progress.blocks_placed,
            materials_consumed=progress.materials_consumed,
            skipped_cells=progress.skipped_cells,
            last_build_position=progress.last_position,
            manual_recovery_reason=progress.manual_recovery_reason,
            manual_recovery_at=progress.manual_recovery_at,
            **values,
        )

    def run(self) -> int:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.stop_path.unlink(missing_ok=True)
        self.pid_path.write_text(str(os.getpid()), encoding="ascii")
        turns = 0
        try:
            recovery = self._recover(self.client)
            if recovery is not None:
                self._publish(recovery.state, recovery.detail, turns=0)
                return 0
            while not self.stop_path.exists() and (
                self.deadline is None or time.time() < self.deadline
            ):
                self._publish(
                    "active", "selecting the next verified road cell", turns=turns
                )
                turn = self.worker.run_turn()
                turns += 1
                self._publish(
                    turn.state,
                    turn.detail,
                    turns=turns,
                    progressed=turn.progressed,
                    build_position=turn.position,
                    material=turn.material,
                )
                if turn.state == "manual_recovery_hold":
                    return 0
                time.sleep(0.5 if turn.state == "building" else 2.0)
            return 0
        except Exception as error:
            self._publish("failed", f"{type(error).__name__}: {error}", turns=turns)
            return 1
        finally:
            try:
                live = snapshot(self.client)
                if non_op_survival_hold_reason(live, self.bot_name) is None:
                    self.client.transport.dispatch("cancel", {})
            except Exception:
                pass
            self.client.shutdown()
            self.pid_path.unlink(missing_ok=True)


def run_perpetual_builder(
    *,
    bot_name: str,
    manifest_path: Path,
    server_properties: Path,
    runtime_dir: Path,
    hours: float = 0,
    center_x: int = 700,
    center_z: int = -702,
) -> int:
    """Resolve one bot and run its reusable infinite construction runtime."""
    # Retained for compatibility with existing command lines. The safe runtime
    # never opens an administrative server connection.
    del server_properties
    manifest = load_mapping(manifest_path)
    bot = manifest.get("bots", {}).get(bot_name)
    if not isinstance(bot, Mapping):
        raise ValueError(f"unknown bot {bot_name!r}")
    assignment = fleet_lanes(center_x, center_z).get(bot_name)
    if assignment is None:
        raise ValueError(f"no perpetual build lane for {bot_name!r}")
    return PerpetualBuildRuntime(
        bot_name=bot_name,
        bridge_port=int(bot["bridge_port"]),
        assignment=assignment,
        runtime_dir=runtime_dir,
        hours=hours,
    ).run()


__all__ = [
    "PerpetualBuildRuntime",
    "needs_food_provisioning",
    "run_perpetual_builder",
]
