"""
Reusable helpers that glue low-level tasks to higher-level mission phases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from .tasks import Task, TaskResult


PhaseExecutor = Callable[[Any, "PhaseContext"], TaskResult]


@dataclass
class PhaseContext:
    """Holds references shared across spawn-to-dragon phases."""

    state: Any = None
    resources: Any = None
    phase: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def update_progress(self, progress: float) -> None:
        if self.state and hasattr(self.state, "update_progress"):
            self.state.update_progress(progress)

    def checkpoint(self, payload: Dict[str, Any]) -> None:
        if not self.state or not hasattr(self.state, "custom_data"):
            return
        self.state.custom_data.setdefault("phase_payloads", {})[self.phase] = payload

    def is_ready(self) -> bool:
        if self.resources and hasattr(self.resources, "is_phase_ready") and self.phase:
            self.resources.refresh_inventory()
            return bool(self.resources.is_phase_ready(self.phase))
        return False


class PhaseTask(Task):
    """A Task wrapper that injects StateManager/ResourceManager context."""

    def __init__(
        self,
        name: str,
        executor: PhaseExecutor,
        context: PhaseContext,
        skip_if_ready: bool = True,
    ) -> None:
        self._name = name
        self.executor = executor
        self.context = context
        self.skip_if_ready = skip_if_ready

    @property
    def name(self) -> str:
        return self._name

    def run(self, client) -> TaskResult:
        if self.skip_if_ready and self.context.is_ready():
            return TaskResult.ok("Phase requirements already met", skipped=True)

        self.context.update_progress(0.05)
        result = self.executor(client, self.context)
        if isinstance(result, bool):
            result = TaskResult.ok() if result else TaskResult.fail("Phase task returned False")

        if result.success:
            self.context.update_progress(1.0)
            if result.data:
                self.context.checkpoint(result.data)

        return result


def phase_task(
    name: str,
    executor: PhaseExecutor,
    state: Optional[Any],
    resources: Optional[Any],
    phase: Optional[Any],
    skip_if_ready: bool = True,
) -> PhaseTask:
    """
    Helper factory that binds StateManager/ResourceManager context to a task.

    Args:
        name: Human readable task name
        executor: Callable receiving (client, PhaseContext)
        state: StateManager instance
        resources: ResourceManager instance
        phase: Current Phase enum value
        skip_if_ready: Skip execution when ResourceManager already satisfies the requirements
    """
    context = PhaseContext(state=state, resources=resources, phase=phase)
    return PhaseTask(name, executor, context, skip_if_ready=skip_if_ready)
