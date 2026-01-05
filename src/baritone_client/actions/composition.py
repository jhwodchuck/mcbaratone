"""
Composition utilities for combining actions into complex workflows.
"""

from typing import List, Callable, Any, Union, Dict, Optional
from abc import ABC, abstractmethod
import concurrent.futures
import threading
import time

from ..core.interfaces import IAction, ActionContext, ActionResult


class CompositeAction(IAction, ABC):
    """Base class for actions that combine multiple sub-actions."""

    def __init__(self, actions: List[IAction]):
        self.actions = actions

    @abstractmethod
    def execute(self, context: ActionContext) -> ActionResult:
        """Execute the composite action."""
        pass


class SequenceAction(CompositeAction):
    """
    Execute actions in sequence, stopping on first failure.

    Useful for ordered workflows where each step depends on the previous.
    """

    def __init__(self, actions: List[IAction], continue_on_failure: bool = False):
        super().__init__(actions)
        self.continue_on_failure = continue_on_failure

    def execute(self, context: ActionContext) -> ActionResult:
        """Execute actions sequentially."""
        results = []
        for i, action in enumerate(self.actions):
            result = action.execute(context)
            results.append(result)

            if not result.success and not self.continue_on_failure:
                return ActionResult.fail(
                    f"Sequence failed at step {i}: {result.message}",
                    step=i,
                    results=results
                )

        success_count = sum(1 for r in results if r.success)
        return ActionResult.ok(
            f"Sequence completed: {success_count}/{len(results)} successful",
            results=results
        )


class ParallelAction(CompositeAction):
    """
    Execute actions in parallel using thread pools.

    Useful for independent operations that can run concurrently.
    """

    def __init__(self, actions: List[IAction], max_workers: Optional[int] = None,
                 require_all_success: bool = False):
        super().__init__(actions)
        self.max_workers = max_workers or min(len(actions), 4)
        self.require_all_success = require_all_success

    def execute(self, context: ActionContext) -> ActionResult:
        """Execute actions in parallel."""
        results = {}
        lock = threading.Lock()

        def execute_single_action(action: IAction, index: int):
            result = action.execute(context)
            with lock:
                results[index] = result
            return result

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [
                executor.submit(execute_single_action, action, i)
                for i, action in enumerate(self.actions)
            ]

            # Wait for all to complete
            concurrent.futures.wait(futures)

        # Collect results in order
        ordered_results = [results[i] for i in range(len(self.actions))]

        if self.require_all_success:
            failed_indices = [i for i, r in enumerate(ordered_results) if not r.success]
            if failed_indices:
                return ActionResult.fail(
                    f"Parallel execution failed at steps: {failed_indices}",
                    failed_steps=failed_indices,
                    results=ordered_results
                )

        success_count = sum(1 for r in ordered_results if r.success)
        return ActionResult.ok(
            f"Parallel execution completed: {success_count}/{len(ordered_results)} successful",
            results=ordered_results
        )


class ConditionalAction(IAction):
    """
    Execute different actions based on a condition.

    Useful for branching logic in automation workflows.
    """

    def __init__(self, condition: Callable[[ActionContext], bool],
                 true_action: IAction, false_action: Optional[IAction] = None):
        self.condition = condition
        self.true_action = true_action
        self.false_action = false_action

    def execute(self, context: ActionContext) -> ActionResult:
        """Execute conditional logic."""
        if self.condition(context):
            result = self.true_action.execute(context)
            return ActionResult.ok(
                f"Condition true, executed action: {result.message}",
                condition_result=True,
                action_result=result
            )
        elif self.false_action:
            result = self.false_action.execute(context)
            return ActionResult.ok(
                f"Condition false, executed alternative: {result.message}",
                condition_result=False,
                action_result=result
            )
        else:
            return ActionResult.ok(
                "Condition false, no alternative action",
                condition_result=False
            )


class RetryAction(IAction):
    """
    Retry an action on failure up to a maximum number of attempts.

    Useful for handling transient failures.
    """

    def __init__(self, action: IAction, max_attempts: int = 3,
                 delay_between_attempts: float = 1.0):
        self.action = action
        self.max_attempts = max_attempts
        self.delay = delay_between_attempts

    def execute(self, context: ActionContext) -> ActionResult:
        """Execute with retry logic."""
        last_result = None

        for attempt in range(self.max_attempts):
            result = self.action.execute(context)
            if result.success:
                return ActionResult.ok(
                    f"Action succeeded on attempt {attempt + 1}",
                    attempts=attempt + 1,
                    final_result=result
                )

            last_result = result
            if attempt < self.max_attempts - 1:
                time.sleep(self.delay)

        return ActionResult.fail(
            f"Action failed after {self.max_attempts} attempts: {last_result.message}",
            attempts=self.max_attempts,
            final_result=last_result
        )


class LoopAction(IAction):
    """
    Execute an action repeatedly until a condition is met.

    Useful for repetitive tasks like gathering resources until a threshold.
    """

    def __init__(self, action: IAction, condition: Callable[[ActionContext, ActionResult], bool],
                 max_iterations: int = 10):
        self.action = action
        self.condition = condition
        self.max_iterations = max_iterations

    def execute(self, context: ActionContext) -> ActionResult:
        """Execute action in a loop."""
        results = []

        for iteration in range(self.max_iterations):
            result = self.action.execute(context)
            results.append(result)

            if not self.condition(context, result):
                break

        final_result = results[-1] if results else ActionResult.fail("No iterations executed")

        return ActionResult.ok(
            f"Loop completed after {len(results)} iterations",
            iterations=len(results),
            results=results,
            final_result=final_result
        )


class RaceAction(IAction):
    """
    Execute multiple actions and return the result of the first to succeed.

    Useful for trying multiple strategies until one works.
    """

    def __init__(self, actions: List[IAction], timeout: Optional[float] = None):
        self.actions = actions
        self.timeout = timeout

    def execute(self, context: ActionContext) -> ActionResult:
        """Execute actions in race condition."""
        results = {}
        lock = threading.Lock()
        completed = threading.Event()

        def execute_single_action(action: IAction, index: int):
            try:
                result = action.execute(context)
                with lock:
                    results[index] = result
                    if result.success:
                        completed.set()
                return result
            except Exception as e:
                with lock:
                    results[index] = ActionResult.fail(f"Exception: {e}")
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.actions)) as executor:
            futures = [
                executor.submit(execute_single_action, action, i)
                for i, action in enumerate(self.actions)
            ]

            # Wait for first success or all failures
            if self.timeout:
                try:
                    completed.wait(timeout=self.timeout)
                except:
                    pass
            else:
                completed.wait()

        # Find first successful result
        for i in range(len(self.actions)):
            if i in results and results[i].success:
                return ActionResult.ok(
                    f"Race won by action {i}",
                    winning_index=i,
                    winning_result=results[i]
                )

        # All failed
        failed_results = [results.get(i, ActionResult.fail("No result")) for i in range(len(self.actions))]
        return ActionResult.fail(
            "All actions in race failed",
            results=failed_results
        )


# Utility functions for common composition patterns

def sequence(*actions: IAction) -> SequenceAction:
    """Create a sequence of actions."""
    return SequenceAction(list(actions))


def parallel(*actions: IAction, require_all_success: bool = False) -> ParallelAction:
    """Create a parallel execution of actions."""
    return ParallelAction(list(actions), require_all_success=require_all_success)


def conditional(condition: Callable[[ActionContext], bool],
               true_action: IAction, false_action: Optional[IAction] = None) -> ConditionalAction:
    """Create a conditional action."""
    return ConditionalAction(condition, true_action, false_action)


def retry(action: IAction, max_attempts: int = 3) -> RetryAction:
    """Create a retry action."""
    return RetryAction(action, max_attempts)


def loop(action: IAction, condition: Callable[[ActionContext, ActionResult], bool],
         max_iterations: int = 10) -> LoopAction:
    """Create a loop action."""
    return LoopAction(action, condition, max_iterations)


def race(*actions: IAction, timeout: Optional[float] = None) -> RaceAction:
    """Create a race action."""
    return RaceAction(list(actions), timeout)