"""
Task abstraction - Composable, testable automation tasks.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable
import time


@dataclass
class TaskResult:
    """Result of task execution."""
    success: bool
    reason: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, reason: str = "Success", **data) -> "TaskResult":
        return cls(success=True, reason=reason, data=data)

    @classmethod
    def fail(cls, reason: str, **data) -> "TaskResult":
        return cls(success=False, reason=reason, data=data)


class Task(ABC):
    """
    Base task abstraction.

    Tasks are composable units of automation that return success/failure.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable task name."""
        pass

    @abstractmethod
    def run(self, client) -> TaskResult:
        """
        Execute the task.

        Args:
            client: Baritone client

        Returns:
            TaskResult with success/failure
        """
        pass


class ActionTask(Task):
    """Task for wrapping functions."""

    def __init__(self, name: str, action: Callable, **kwargs):
        self._name = name
        self.action = action
        self.kwargs = kwargs

    @property
    def name(self) -> str:
        return self._name

    def run(self, client) -> TaskResult:
        try:
            # Record that the action was invoked if the client exposes a calls list
            try:
                if hasattr(client, "calls") and isinstance(client.calls, list):
                    client.calls.append(("action_run", self._name))
            except Exception:
                pass

            result = self.action(client, **self.kwargs)
            if isinstance(result, TaskResult):
                return result
            elif isinstance(result, bool):
                return TaskResult.ok() if result else TaskResult.fail("Action returned False")
            else:
                return TaskResult.ok(data={"result": result})
        except Exception as e:
            return TaskResult.fail(str(e))


class SequentialTask(Task):
    """Task for ordered execution."""

    def __init__(self, name: str, tasks: List[Task]):
        self._name = name
        self.tasks = tasks

    @property
    def name(self) -> str:
        return self._name

    def run(self, client) -> TaskResult:
        for task in self.tasks:
            result = task.run(client)
            if not result.success:
                return TaskResult.fail(f"Sequential task failed at {task.name}: {result.reason}")
        return TaskResult.ok(f"All {len(self.tasks)} tasks completed successfully")


class RetryTask(Task):
    """Task for automatic retries."""

    def __init__(self, task: Task, max_retries: int = 3, delay: float = 1.0):
        self.task = task
        self.max_retries = max_retries
        self.delay = delay

    @property
    def name(self) -> str:
        return f"RetryTask({self.task.name})"

    def run(self, client) -> TaskResult:
        for attempt in range(self.max_retries + 1):
            result = self.task.run(client)
            if result.success:
                return TaskResult.ok(f"Task succeeded on attempt {attempt + 1}")
            if attempt < self.max_retries:
                time.sleep(self.delay)
        return TaskResult.fail(f"Task failed after {self.max_retries + 1} attempts")


class ConditionalTask(Task):
    """Task for conditional execution."""

    def __init__(self, task: Task, condition: Callable):
        self.task = task
        self.condition = condition

    @property
    def name(self) -> str:
        return f"ConditionalTask({self.task.name})"

    def run(self, client) -> TaskResult:
        if self.condition(client):
            return self.task.run(client)
        else:
            return TaskResult.ok("Condition not met, task skipped")


class TaskContext:
    """Execution context for tasks."""

    def __init__(self, client):
        self.client = client
        self.shared_data: Dict[str, Any] = {}

    def set(self, key: str, value: Any):
        self.shared_data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.shared_data.get(key, default)


class MockClient:
    """Mock client for unit tests."""

    def __init__(self):
        self.calls = []
        # Provide a minimal transport stub so higher-level code that calls
        # `client.transport.dispatch(...)` will record calls in `self.calls`.
        class _TransportStub:
            def __init__(self, owner):
                self._owner = owner

            def dispatch(self, route, payload):
                # record the dispatch call
                self._owner.calls.append(("dispatch", route, payload))
                # Return a minimal empty inventory for get_inventory
                if route == "get_inventory":
                    return {"inventory": [], "armor": [], "offhand": []}
                return {}

        self.transport = _TransportStub(self)

    def __getattr__(self, name):
        def mock_method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return None
        return mock_method
