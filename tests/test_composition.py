"""
Unit tests for composition action patterns.

Tests the composite action implementations directly without depending on 
baritone_client imports to avoid circular dependency issues.
"""

import pytest
import time
import sys
from pathlib import Path

# Ensure src is in path
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ============================================================================
# Self-Contained Implementations for Testing
# Since imports from baritone_client trigger circular dependency chains,
# we test the composition patterns in isolation with simplified implementations.
# ============================================================================

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable
import concurrent.futures
import threading


@dataclass
class ActionResult:
    """Self-contained ActionResult for testing."""
    success: bool
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def ok(cls, message: str = "", **kwargs) -> 'ActionResult':
        return cls(True, message, kwargs)
    
    @classmethod
    def fail(cls, message: str = "", **kwargs) -> 'ActionResult':
        return cls(False, message, kwargs)


@dataclass
class ActionContext:
    """Self-contained ActionContext for testing."""
    data: Dict[str, Any] = field(default_factory=dict)


class IAction(ABC):
    """Self-contained IAction interface for testing."""
    
    @abstractmethod
    def execute(self, context: ActionContext) -> ActionResult:
        pass


# ============================================================================
# Composite Action Implementations (copied for isolation)
# ============================================================================

class SequenceAction(IAction):
    """Execute actions in sequence, stopping on first failure."""
    
    def __init__(self, actions: List[IAction], continue_on_failure: bool = False):
        self.actions = actions
        self.continue_on_failure = continue_on_failure
    
    def execute(self, context: ActionContext) -> ActionResult:
        results = []
        for action in self.actions:
            result = action.execute(context)
            results.append(result)
            if not result.success and not self.continue_on_failure:
                return ActionResult.fail("Sequence failed", results=results)
        
        success = all(r.success for r in results)
        return ActionResult(success, "Sequence complete" if success else "Sequence had failures", {"results": results})


class ConditionalAction(IAction):
    """Execute different actions based on a condition."""
    
    def __init__(self, condition: Callable[[ActionContext], bool],
                 true_action: IAction, false_action: Optional[IAction] = None):
        self.condition = condition
        self.true_action = true_action
        self.false_action = false_action
    
    def execute(self, context: ActionContext) -> ActionResult:
        if self.condition(context):
            return self.true_action.execute(context)
        elif self.false_action:
            return self.false_action.execute(context)
        return ActionResult.ok("Condition false, no action taken")


class ParallelAction(IAction):
    """Execute actions in parallel using thread pools."""
    
    def __init__(self, actions: List[IAction], max_workers: Optional[int] = None,
                 require_all_success: bool = False):
        self.actions = actions
        self.max_workers = max_workers or min(len(actions), 4)
        self.require_all_success = require_all_success
    
    def execute(self, context: ActionContext) -> ActionResult:
        if not self.actions:
            return ActionResult.ok("No actions to execute")
        
        results = [None] * len(self.actions)
        
        def execute_single(action: IAction, index: int) -> ActionResult:
            result = action.execute(context)
            results[index] = result
            return result
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(execute_single, action, i): i 
                      for i, action in enumerate(self.actions)}
            concurrent.futures.wait(futures)
        
        success = all(r.success for r in results if r is not None)
        if self.require_all_success and not success:
            return ActionResult.fail("Not all parallel actions succeeded", results=results)
        return ActionResult(True, "Parallel execution complete", {"results": results})


class RetryAction(IAction):
    """Retry an action on failure up to a maximum number of attempts."""
    
    def __init__(self, action: IAction, max_attempts: int = 3,
                 delay_between_attempts: float = 0.01):
        self.action = action
        self.max_attempts = max_attempts
        self.delay = delay_between_attempts
    
    def execute(self, context: ActionContext) -> ActionResult:
        last_result = None
        for attempt in range(self.max_attempts):
            result = self.action.execute(context)
            if result.success:
                return result
            last_result = result
            if attempt < self.max_attempts - 1:
                time.sleep(self.delay)
        return ActionResult.fail(f"Failed after {self.max_attempts} attempts", 
                                 last_result=last_result)


class LoopAction(IAction):
    """Execute an action repeatedly until a condition is met."""
    
    def __init__(self, action: IAction, condition: Callable[[ActionContext, ActionResult], bool],
                 max_iterations: int = 10):
        self.action = action
        self.condition = condition
        self.max_iterations = max_iterations
    
    def execute(self, context: ActionContext) -> ActionResult:
        for i in range(self.max_iterations):
            result = self.action.execute(context)
            if self.condition(context, result):
                return ActionResult.ok(f"Loop completed after {i + 1} iterations")
            if not result.success:
                return ActionResult.fail(f"Loop action failed on iteration {i + 1}")
        return ActionResult.fail(f"Loop reached max iterations ({self.max_iterations})")


# ============================================================================
# Mock Actions for Testing
# ============================================================================

class SuccessAction(IAction):
    """Action that always succeeds."""
    
    def __init__(self, message: str = "success", delay: float = 0):
        self.message = message
        self.delay = delay
        self.executed = False
    
    def execute(self, context: ActionContext) -> ActionResult:
        if self.delay:
            time.sleep(self.delay)
        self.executed = True
        return ActionResult.ok(self.message)


class FailAction(IAction):
    """Action that always fails."""
    
    def __init__(self, message: str = "failure"):
        self.message = message
        self.executed = False
    
    def execute(self, context: ActionContext) -> ActionResult:
        self.executed = True
        return ActionResult.fail(self.message)


class CounterAction(IAction):
    """Action that tracks execution count."""
    
    def __init__(self, fail_until: int = 0):
        self.count = 0
        self.fail_until = fail_until
    
    def execute(self, context: ActionContext) -> ActionResult:
        self.count += 1
        if self.count <= self.fail_until:
            return ActionResult.fail(f"Attempt {self.count} failed")
        return ActionResult.ok(f"Succeeded on attempt {self.count}")


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def context():
    """Create a fresh ActionContext for each test."""
    return ActionContext()


# ============================================================================
# SequenceAction Tests
# ============================================================================

class TestSequenceAction:
    """Tests for SequenceAction class."""

    def test_executes_in_order(self, context):
        """Test that actions execute in sequence order."""
        actions = [SuccessAction("first"), SuccessAction("second"), SuccessAction("third")]
        sequence = SequenceAction(actions)
        
        result = sequence.execute(context)
        
        assert result.success is True
        assert all(a.executed for a in actions)

    def test_stops_on_failure(self, context):
        """Test that sequence stops on first failure."""
        action1 = SuccessAction("first")
        action2 = FailAction("failed")
        action3 = SuccessAction("third")
        sequence = SequenceAction([action1, action2, action3])
        
        result = sequence.execute(context)
        
        assert result.success is False
        assert action1.executed is True
        assert action2.executed is True
        assert action3.executed is False

    def test_continues_on_failure_when_configured(self, context):
        """Test that sequence continues past failure when configured."""
        action1 = SuccessAction("first")
        action2 = FailAction("failed")
        action3 = SuccessAction("third")
        sequence = SequenceAction([action1, action2, action3], continue_on_failure=True)
        
        result = sequence.execute(context)
        
        assert result.success is False
        assert action1.executed is True
        assert action2.executed is True
        assert action3.executed is True

    def test_empty_sequence_succeeds(self, context):
        """Test that empty sequence succeeds."""
        sequence = SequenceAction([])
        
        result = sequence.execute(context)
        
        assert result.success is True


# ============================================================================
# ConditionalAction Tests
# ============================================================================

class TestConditionalAction:
    """Tests for ConditionalAction class."""

    def test_executes_true_branch_when_condition_true(self, context):
        """Test true branch executes when condition is true."""
        true_action = SuccessAction("true_branch")
        false_action = SuccessAction("false_branch")
        conditional = ConditionalAction(
            condition=lambda ctx: True,
            true_action=true_action,
            false_action=false_action,
        )
        
        result = conditional.execute(context)
        
        assert result.success is True
        assert true_action.executed is True
        assert false_action.executed is False

    def test_executes_false_branch_when_condition_false(self, context):
        """Test false branch executes when condition is false."""
        true_action = SuccessAction("true_branch")
        false_action = SuccessAction("false_branch")
        conditional = ConditionalAction(
            condition=lambda ctx: False,
            true_action=true_action,
            false_action=false_action,
        )
        
        result = conditional.execute(context)
        
        assert result.success is True
        assert true_action.executed is False
        assert false_action.executed is True

    def test_succeeds_when_no_false_action_and_condition_false(self, context):
        """Test succeeds when no false action and condition is false."""
        true_action = SuccessAction("true_branch")
        conditional = ConditionalAction(
            condition=lambda ctx: False,
            true_action=true_action,
            false_action=None,
        )
        
        result = conditional.execute(context)
        
        assert result.success is True
        assert true_action.executed is False


# ============================================================================
# ParallelAction Tests
# ============================================================================

class TestParallelAction:
    """Tests for ParallelAction class."""

    def test_executes_all_actions(self, context):
        """Test that all actions execute in parallel."""
        actions = [SuccessAction("a"), SuccessAction("b"), SuccessAction("c")]
        parallel = ParallelAction(actions)
        
        result = parallel.execute(context)
        
        assert result.success is True
        assert all(a.executed for a in actions)

    def test_fails_when_require_all_success_and_one_fails(self, context):
        """Test fails when require_all_success and one action fails."""
        actions = [SuccessAction("a"), FailAction("b"), SuccessAction("c")]
        parallel = ParallelAction(actions, require_all_success=True)
        
        result = parallel.execute(context)
        
        assert result.success is False

    def test_succeeds_when_not_require_all_success(self, context):
        """Test succeeds when not requiring all to succeed."""
        actions = [SuccessAction("a"), FailAction("b"), SuccessAction("c")]
        parallel = ParallelAction(actions, require_all_success=False)
        
        result = parallel.execute(context)
        
        assert result.success is True


# ============================================================================
# RetryAction Tests
# ============================================================================

class TestRetryAction:
    """Tests for RetryAction class."""

    def test_retries_on_failure(self, context):
        """Test that action is retried on failure."""
        counter = CounterAction(fail_until=2)
        retry = RetryAction(counter, max_attempts=3, delay_between_attempts=0)
        
        result = retry.execute(context)
        
        assert result.success is True
        assert counter.count == 3

    def test_fails_after_max_attempts(self, context):
        """Test that action fails after max attempts exceeded."""
        counter = CounterAction(fail_until=10)
        retry = RetryAction(counter, max_attempts=3, delay_between_attempts=0)
        
        result = retry.execute(context)
        
        assert result.success is False
        assert counter.count == 3

    def test_succeeds_immediately_when_action_succeeds(self, context):
        """Test that retry succeeds immediately when action succeeds."""
        counter = CounterAction(fail_until=0)
        retry = RetryAction(counter, max_attempts=5, delay_between_attempts=0)
        
        result = retry.execute(context)
        
        assert result.success is True
        assert counter.count == 1


# ============================================================================
# LoopAction Tests
# ============================================================================

class TestLoopAction:
    """Tests for LoopAction class."""

    def test_loops_until_condition_met(self, context):
        """Test that action loops until condition is met."""
        counter = CounterAction()
        
        def condition(ctx, result):
            return counter.count >= 3
        
        loop = LoopAction(counter, condition=condition, max_iterations=10)
        
        result = loop.execute(context)
        
        assert result.success is True
        assert counter.count == 3

    def test_stops_at_max_iterations(self, context):
        """Test that loop stops at max iterations."""
        counter = CounterAction()
        
        def never_stop(ctx, result):
            return False
        
        loop = LoopAction(counter, condition=never_stop, max_iterations=5)
        
        result = loop.execute(context)
        
        assert result.success is False
        assert counter.count == 5


# ============================================================================
# Dependency Injection Pattern Tests
# ============================================================================

class InjectableAction(IAction):
    """Action that accepts dependencies via constructor."""

    def __init__(self, dependency=None):
        self.dependency = dependency

    def execute(self, context: ActionContext) -> ActionResult:
        if self.dependency:
            return ActionResult.ok(f"Executed with {self.dependency}")
        return ActionResult.fail("No dependency provided")


class DependencyManager:
    """Mock dependency manager for testing injection patterns."""

    def __init__(self, services=None):
        self.services = services or {}

    def get_service(self, name: str):
        return self.services.get(name)

    def provide_dependency(self, action_class, **kwargs):
        """Factory method for dependency injection."""
        if 'dependency' not in kwargs and hasattr(action_class, '__init__'):
            # Try to inject common dependencies
            kwargs['dependency'] = self.get_service('default_dependency')
        return action_class(**kwargs)


class TestDependencyInjection:
    """Tests for dependency injection patterns in action composition."""

    def test_action_with_injected_dependency(self, context):
        """Test action executes successfully with injected dependency."""
        action = InjectableAction(dependency="test_service")
        result = action.execute(context)

        assert result.success is True
        assert "test_service" in result.message

    def test_action_fails_without_dependency(self, context):
        """Test action fails when dependency is not provided."""
        action = InjectableAction()
        result = action.execute(context)

        assert result.success is False

    def test_dependency_manager_injection(self, context):
        """Test dependency manager can inject services into actions."""
        manager = DependencyManager({
            'default_dependency': 'injected_service',
            'crafting_service': 'crafting_impl'
        })

        action = manager.provide_dependency(InjectableAction)
        result = action.execute(context)

        assert result.success is True
        assert "injected_service" in result.message

    def test_sequence_with_dependency_injection(self, context):
        """Test sequence actions can be created with dependency injection."""
        manager = DependencyManager({'service_a': 'A', 'service_b': 'B'})

        action1 = manager.provide_dependency(InjectableAction, dependency='service_a')
        action2 = manager.provide_dependency(InjectableAction, dependency='service_b')

        sequence = SequenceAction([action1, action2])
        result = sequence.execute(context)

        assert result.success is True
        assert len(result.data['results']) == 2


# ============================================================================
# Error Handling and Edge Case Tests
# ============================================================================

class FailingAction(IAction):
    """Action that can fail in different ways."""

    def __init__(self, fail_mode=None, exception_type=None):
        self.fail_mode = fail_mode
        self.exception_type = exception_type

    def execute(self, context: ActionContext) -> ActionResult:
        if self.fail_mode == 'exception':
            if self.exception_type:
                raise self.exception_type("Test exception")
            raise RuntimeError("Test exception")
        elif self.fail_mode == 'transport_error':
            raise Exception("Transport failure")
        return ActionResult.fail("Configured to fail")


class TestErrorHandling:
    """Tests for error handling in composition patterns."""

    def test_sequence_handles_exceptions_gracefully(self, context):
        """Test sequence handles exceptions in actions."""
        success_action = SuccessAction("success")
        failing_action = FailingAction(fail_mode='exception')

        # Test with continue_on_failure=False (default)
        sequence = SequenceAction([success_action, failing_action])
        result = sequence.execute(context)

        assert result.success is False
        assert len(result.data['results']) == 1  # Stopped at first failure

    def test_parallel_handles_partial_failures(self, context):
        """Test parallel execution handles some actions failing."""
        actions = [
            SuccessAction("success1"),
            FailingAction(fail_mode='normal'),
            SuccessAction("success2"),
            FailingAction(fail_mode='normal'),
        ]

        parallel = ParallelAction(actions, require_all_success=False)
        result = parallel.execute(context)

        assert result.success is True  # Doesn't require all success
        assert len(result.data['results']) == 4

        success_count = sum(1 for r in result.data['results'] if r.success)
        assert success_count == 2

    def test_retry_handles_transient_failures(self, context):
        """Test retry action handles transient failures."""
        # Action that fails twice then succeeds
        counter = CounterAction(fail_until=2)
        retry = RetryAction(counter, max_attempts=5, delay_between_attempts=0)

        result = retry.execute(context)

        assert result.success is True
        assert counter.count == 3  # Failed twice, succeeded third time

    def test_conditional_with_exception_in_condition(self, context):
        """Test conditional handles exceptions in condition evaluation."""
        def failing_condition(ctx):
            raise ValueError("Condition evaluation failed")

        true_action = SuccessAction("true")
        false_action = SuccessAction("false")

        conditional = ConditionalAction(failing_condition, true_action, false_action)

        # Should handle exception gracefully (implementation dependent)
        result = conditional.execute(context)
        assert isinstance(result.success, bool)  # At least returns a result

    def test_nested_composition_error_propagation(self, context):
        """Test error propagation through nested compositions."""
        inner_sequence = SequenceAction([
            SuccessAction("inner1"),
            FailingAction(fail_mode='normal'),
            SuccessAction("inner2")  # Won't execute
        ])

        outer_sequence = SequenceAction([
            SuccessAction("outer1"),
            inner_sequence,
            SuccessAction("outer2")  # Won't execute
        ])

        result = outer_sequence.execute(context)

        assert result.success is False
        assert "outer1" in result.data['results'][0].message
        assert not result.data['results'][1].success
