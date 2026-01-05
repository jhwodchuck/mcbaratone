"""
Phase builder pattern for constructing automation phases.
"""

from typing import List, Dict, Any, Optional, Callable, Protocol
from dataclasses import dataclass, field
from abc import ABC, abstractmethod

from ..core.interfaces import IAction, ActionContext, ActionResult
from ..actions import (
    MovementAction, InventoryAction, CraftingAction, CombatAction,
    RecoveryAction, SensingAction, TravelAction
)


class IPhaseStrategy(Protocol):
    """Strategy for executing phase logic."""

    def execute(self, context: ActionContext, actions: Dict[str, IAction]) -> ActionResult:
        """Execute the phase using available actions."""
        ...


@dataclass
class PhaseStep:
    """Represents a single step in a phase."""
    name: str
    action: Callable[[ActionContext], ActionResult]
    required: bool = True
    retry_count: int = 0
    timeout: Optional[float] = None
    dependencies: List[str] = field(default_factory=list)


@dataclass
class PhaseConfiguration:
    """Configuration for a phase."""
    name: str
    description: str = ""
    steps: List[PhaseStep] = field(default_factory=list)
    strategy: Optional[IPhaseStrategy] = None
    actions: Dict[str, IAction] = field(default_factory=dict)
    requirements: Dict[str, int] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


class PhaseBuilder:
    """
    Builder for constructing automation phases using fluent interface.

    Provides a clean, composable way to build complex automation phases
    with proper dependency management and error handling.
    """

    def __init__(self, name: str):
        self.config = PhaseConfiguration(name=name)

    def description(self, description: str) -> 'PhaseBuilder':
        """Set phase description."""
        self.config.description = description
        return self

    def with_action(self, name: str, action: IAction) -> 'PhaseBuilder':
        """Add an action to the phase."""
        self.config.actions[name] = action
        return self

    def with_actions(self, actions: Dict[str, IAction]) -> 'PhaseBuilder':
        """Add multiple actions to the phase."""
        self.config.actions.update(actions)
        return self

    def with_default_actions(self) -> 'PhaseBuilder':
        """Add standard actions (movement, inventory, crafting, combat, sensing, travel)."""
        self.config.actions.update({
            'movement': MovementAction(),
            'inventory': InventoryAction(),
            'crafting': CraftingAction(),
            'combat': CombatAction(),
            'recovery': RecoveryAction(),
            'sensing': SensingAction(),
            'travel': TravelAction(),
        })
        return self

    def add_step(self, name: str, action: Callable[[ActionContext], ActionResult],
                required: bool = True, retry_count: int = 0,
                timeout: Optional[float] = None,
                dependencies: Optional[List[str]] = None) -> 'PhaseBuilder':
        """Add a step to the phase."""
        step = PhaseStep(
            name=name,
            action=action,
            required=required,
            retry_count=retry_count,
            timeout=timeout,
            dependencies=dependencies or []
        )
        self.config.steps.append(step)
        return self

    def with_strategy(self, strategy: IPhaseStrategy) -> 'PhaseBuilder':
        """Set the execution strategy for the phase."""
        self.config.strategy = strategy
        return self

    def with_requirements(self, requirements: Dict[str, int]) -> 'PhaseBuilder':
        """Set resource requirements for the phase."""
        self.config.requirements = requirements
        return self

    def metadata(self, key: str, value: Any) -> 'PhaseBuilder':
        """Add metadata to the phase."""
        self.config.metadata[key] = value
        return self

    def build(self) -> 'Phase':
        """Build the phase configuration."""
        if not self.config.strategy:
            # Default to sequential strategy
            self.config.strategy = SequentialPhaseStrategy()

        return Phase(self.config)


class Phase:
    """Represents a complete automation phase."""

    def __init__(self, config: PhaseConfiguration):
        self.config = config
        self._validate_configuration()

    def _validate_configuration(self):
        """Validate phase configuration."""
        if not self.config.name:
            raise ValueError("Phase name is required")

        if not self.config.actions:
            raise ValueError("Phase must have at least one action")

        # Validate step dependencies
        step_names = {step.name for step in self.config.steps}
        for step in self.config.steps:
            for dep in step.dependencies:
                if dep not in step_names:
                    raise ValueError(f"Step '{step.name}' depends on unknown step '{dep}'")

    def execute(self, context: ActionContext) -> ActionResult:
        """Execute the phase using its configured strategy."""
        return self.config.strategy.execute(context, self.config.actions)

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def description(self) -> str:
        return self.config.description

    @property
    def requirements(self) -> Dict[str, int]:
        return self.config.requirements.copy()


class SequentialPhaseStrategy(IPhaseStrategy):
    """Executes phase steps in sequential order with dependency checking."""

    def execute(self, context: ActionContext, actions: Dict[str, IAction]) -> ActionResult:
        """Execute steps sequentially, respecting dependencies."""
        completed_steps = set()
        step_results = {}

        for step in self._get_execution_order():
            # Check dependencies
            if not self._dependencies_satisfied(step, completed_steps):
                return ActionResult.fail(f"Dependencies not satisfied for step: {step.name}")

            # Execute step
            result = self._execute_step_with_retry(step, context)

            step_results[step.name] = result
            if result.success:
                completed_steps.add(step.name)
            elif step.required:
                return ActionResult.fail(f"Required step '{step.name}' failed: {result.message}")
            # Non-required steps can fail without stopping execution

        return ActionResult.ok("Phase completed successfully", results=step_results)

    def _get_execution_order(self) -> List[PhaseStep]:
        """Get steps in dependency-satisfied execution order."""
        # Simple topological sort for dependencies
        # This is a basic implementation - could be enhanced for complex graphs
        ordered = []
        remaining = list(self.config.steps)

        while remaining:
            ready = [step for step in remaining if all(dep in ordered for dep in step.dependencies)]
            if not ready:
                raise ValueError("Circular dependency detected in phase steps")
            ordered.extend(ready)
            remaining = [step for step in remaining if step not in ready]

        return ordered

    def _dependencies_satisfied(self, step: PhaseStep, completed: set) -> bool:
        """Check if all dependencies for a step are satisfied."""
        return all(dep in completed for dep in step.dependencies)

    def _execute_step_with_retry(self, step: PhaseStep, context: ActionContext) -> ActionResult:
        """Execute a step with retry logic."""
        import time

        for attempt in range(step.retry_count + 1):
            try:
                if step.timeout:
                    # Basic timeout implementation
                    import threading
                    result = [None]
                    exception = [None]

                    def run_step():
                        try:
                            result[0] = step.action(context)
                        except Exception as e:
                            exception[0] = e

                    thread = threading.Thread(target=run_step)
                    thread.start()
                    thread.join(timeout=step.timeout)

                    if thread.is_alive():
                        return ActionResult.fail(f"Step '{step.name}' timed out")
                    elif exception[0]:
                        raise exception[0]
                    else:
                        return result[0]
                else:
                    return step.action(context)

            except Exception as e:
                if attempt == step.retry_count:
                    return ActionResult.fail(f"Step '{step.name}' failed after {step.retry_count + 1} attempts: {e}")
                time.sleep(0.5)  # Brief pause between retries

        return ActionResult.fail(f"Step '{step.name}' failed")


class ParallelPhaseStrategy(IPhaseStrategy):
    """Executes phase steps in parallel where possible."""

    def execute(self, context: ActionContext, actions: Dict[str, IAction]) -> ActionResult:
        """Execute steps in parallel, respecting dependencies."""
        import concurrent.futures
        import threading

        completed_steps = set()
        step_results = {}
        lock = threading.Lock()

        def execute_step(step: PhaseStep):
            # Wait for dependencies
            while not self._dependencies_satisfied(step, completed_steps):
                time.sleep(0.1)

            result = self._execute_step_with_retry(step, context)

            with lock:
                step_results[step.name] = result
                if result.success:
                    completed_steps.add(step.name)

            return result

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(execute_step, step) for step in self.steps]
            results = [future.result() for future in concurrent.futures.as_completed(futures)]

        # Check if all required steps succeeded
        failed_required = [step for step in self.config.steps if step.required and not step_results.get(step.name, ActionResult.fail()).success]

        if failed_required:
            return ActionResult.fail(f"Required steps failed: {[s.name for s in failed_required]}")

        return ActionResult.ok("Parallel phase completed", results=step_results)


# Advanced Phase Strategies for complex workflows

class ConditionalPhaseStrategy(IPhaseStrategy):
    """
    Executes phase based on a condition.

    Useful for adaptive workflows that change based on state.
    """

    def __init__(self, condition: Callable[[ActionContext], bool],
                 true_strategy: IPhaseStrategy, false_strategy: Optional[IPhaseStrategy] = None):
        self.condition = condition
        self.true_strategy = true_strategy
        self.false_strategy = false_strategy

    def execute(self, context: ActionContext, actions: Dict[str, IAction]) -> ActionResult:
        """Execute conditional phase logic."""
        if self.condition(context):
            result = self.true_strategy.execute(context, actions)
            return ActionResult.ok(
                f"Condition true, executed strategy: {result.message}",
                condition_result=True,
                strategy_result=result
            )
        elif self.false_strategy:
            result = self.false_strategy.execute(context, actions)
            return ActionResult.ok(
                f"Condition false, executed alternative: {result.message}",
                condition_result=False,
                strategy_result=result
            )
        else:
            return ActionResult.ok(
                "Condition false, no alternative strategy",
                condition_result=False
            )


class RetryPhaseStrategy(IPhaseStrategy):
    """
    Retries phase execution on failure.

    Useful for handling transient failures in complex operations.
    """

    def __init__(self, base_strategy: IPhaseStrategy, max_attempts: int = 3,
                 delay_between_attempts: float = 1.0):
        self.base_strategy = base_strategy
        self.max_attempts = max_attempts
        self.delay = delay_between_attempts

    def execute(self, context: ActionContext, actions: Dict[str, IAction]) -> ActionResult:
        """Execute with retry logic."""
        import time

        last_result = None
        for attempt in range(self.max_attempts):
            result = self.base_strategy.execute(context, actions)
            if result.success:
                return ActionResult.ok(
                    f"Strategy succeeded on attempt {attempt + 1}",
                    attempts=attempt + 1,
                    final_result=result
                )
            last_result = result
            if attempt < self.max_attempts - 1:
                time.sleep(self.delay)

        return ActionResult.fail(
            f"Strategy failed after {self.max_attempts} attempts: {last_result.message}",
            attempts=self.max_attempts,
            final_result=last_result
        )


class LoopPhaseStrategy(IPhaseStrategy):
    """
    Executes phase repeatedly until a condition is met.

    Useful for repetitive tasks like resource gathering until threshold.
    """

    def __init__(self, base_strategy: IPhaseStrategy,
                 condition: Callable[[ActionContext, ActionResult], bool],
                 max_iterations: int = 10):
        self.base_strategy = base_strategy
        self.condition = condition
        self.max_iterations = max_iterations

    def execute(self, context: ActionContext, actions: Dict[str, IAction]) -> ActionResult:
        """Execute strategy in a loop."""
        results = []

        for iteration in range(self.max_iterations):
            result = self.base_strategy.execute(context, actions)
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


class CompositePhaseStrategy(IPhaseStrategy):
    """
    Composes multiple strategies into complex workflows.

    Supports sequence, parallel, and race patterns.
    """

    def __init__(self, strategies: List[IPhaseStrategy],
                 pattern: str = "sequence",  # sequence, parallel, race
                 require_all_success: bool = False):
        self.strategies = strategies
        self.pattern = pattern
        self.require_all_success = require_all_success

        if pattern not in ["sequence", "parallel", "race"]:
            raise ValueError(f"Unsupported pattern: {pattern}")

    def execute(self, context: ActionContext, actions: Dict[str, IAction]) -> ActionResult:
        """Execute composite strategy."""
        if self.pattern == "sequence":
            return self._execute_sequence(context, actions)
        elif self.pattern == "parallel":
            return self._execute_parallel(context, actions)
        elif self.pattern == "race":
            return self._execute_race(context, actions)

    def _execute_sequence(self, context: ActionContext, actions: Dict[str, IAction]) -> ActionResult:
        """Execute strategies in sequence."""
        results = []
        for i, strategy in enumerate(self.strategies):
            result = strategy.execute(context, actions)
            results.append(result)

            if not result.success and not self.require_all_success:
                return ActionResult.fail(
                    f"Sequence failed at strategy {i}: {result.message}",
                    strategy_index=i,
                    results=results
                )

        success_count = sum(1 for r in results if r.success)
        return ActionResult.ok(
            f"Sequence completed: {success_count}/{len(results)} successful",
            results=results
        )

    def _execute_parallel(self, context: ActionContext, actions: Dict[str, IAction]) -> ActionResult:
        """Execute strategies in parallel."""
        import concurrent.futures
        import threading

        results = {}
        lock = threading.Lock()

        def execute_strategy(strategy: IPhaseStrategy, index: int):
            result = strategy.execute(context, actions)
            with lock:
                results[index] = result
            return result

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.strategies)) as executor:
            futures = [
                executor.submit(execute_strategy, strategy, i)
                for i, strategy in enumerate(self.strategies)
            ]
            concurrent.futures.wait(futures)

        # Collect results in order
        ordered_results = [results[i] for i in range(len(self.strategies))]

        if self.require_all_success:
            failed_indices = [i for i, r in enumerate(ordered_results) if not r.success]
            if failed_indices:
                return ActionResult.fail(
                    f"Parallel execution failed at strategies: {failed_indices}",
                    failed_indices=failed_indices,
                    results=ordered_results
                )

        success_count = sum(1 for r in ordered_results if r.success)
        return ActionResult.ok(
            f"Parallel execution completed: {success_count}/{len(ordered_results)} successful",
            results=ordered_results
        )

    def _execute_race(self, context: ActionContext, actions: Dict[str, IAction]) -> ActionResult:
        """Execute strategies in race condition."""
        results = {}
        completed = threading.Event()
        lock = threading.Lock()

        def execute_strategy(strategy: IPhaseStrategy, index: int):
            try:
                result = strategy.execute(context, actions)
                with lock:
                    results[index] = result
                    if result.success:
                        completed.set()
            except Exception as e:
                with lock:
                    results[index] = ActionResult.fail(f"Exception: {e}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.strategies)) as executor:
            futures = [
                executor.submit(execute_strategy, strategy, i)
                for i, strategy in enumerate(self.strategies)
            ]
            completed.wait()

        # Find first successful result
        for i in range(len(self.strategies)):
            if i in results and results[i].success:
                return ActionResult.ok(
                    f"Race won by strategy {i}",
                    winning_index=i,
                    winning_result=results[i]
                )

        # All failed
        failed_results = [results.get(i, ActionResult.fail("No result")) for i in range(len(self.strategies))]
        return ActionResult.fail(
            "All strategies in race failed",
            results=failed_results
        )


# Advanced Builders for complex automation workflows

class ConditionalPhaseBuilder(PhaseBuilder):
    """
    Builder for conditional phase execution.

    Allows phases that adapt based on runtime conditions.
    """

    def __init__(self, name: str, condition: Callable[[ActionContext], bool]):
        super().__init__(name)
        self._condition = condition
        self._true_builder = None
        self._false_builder = None

    def on_true(self, true_builder: PhaseBuilder) -> 'ConditionalPhaseBuilder':
        """Set the phase to execute when condition is true."""
        self._true_builder = true_builder
        return self

    def on_false(self, false_builder: Optional[PhaseBuilder] = None) -> 'ConditionalPhaseBuilder':
        """Set the phase to execute when condition is false."""
        self._false_builder = false_builder
        return self

    def build(self) -> 'ConditionalPhase':
        """Build the conditional phase."""
        if not self._true_builder:
            raise ValueError("True condition builder is required")

        true_phase = self._true_builder.build()
        false_phase = self._false_builder.build() if self._false_builder else None

        return ConditionalPhase(self.config, self._condition, true_phase, false_phase)


class ConditionalPhase(Phase):
    """A phase that executes conditionally."""

    def __init__(self, config: PhaseConfiguration, condition: Callable[[ActionContext], bool],
                 true_phase: Phase, false_phase: Optional[Phase] = None):
        super().__init__(config)
        self.condition = condition
        self.true_phase = true_phase
        self.false_phase = false_phase

    def execute(self, context: ActionContext) -> ActionResult:
        """Execute conditional phase."""
        if self.condition(context):
            result = self.true_phase.execute(context)
            return ActionResult.ok(
                f"Condition true: {result.message}",
                condition_result=True,
                phase_result=result
            )
        elif self.false_phase:
            result = self.false_phase.execute(context)
            return ActionResult.ok(
                f"Condition false: {result.message}",
                condition_result=False,
                phase_result=result
            )
        else:
            return ActionResult.ok(
                "Condition false, no alternative phase",
                condition_result=False
            )


class WorkflowBuilder:
    """
    Advanced builder for complex automation workflows.

    Supports composition of multiple phases with advanced patterns.
    """

    def __init__(self, name: str):
        self.name = name
        self.phases = []
        self.dependencies = {}  # phase_name -> list of required phases

    def add_phase(self, phase: Phase, dependencies: Optional[List[str]] = None) -> 'WorkflowBuilder':
        """Add a phase with optional dependencies."""
        self.phases.append(phase)
        if dependencies:
            self.dependencies[phase.name] = dependencies
        return self

    def build_workflow(self) -> 'Workflow':
        """Build a complete workflow."""
        return Workflow(self.name, self.phases, self.dependencies)


class Workflow:
    """
    Complex automation workflow composed of multiple phases.

    Supports dependency management and advanced execution patterns.
    """

    def __init__(self, name: str, phases: List[Phase], dependencies: Dict[str, List[str]]):
        self.name = name
        self.phases = {phase.name: phase for phase in phases}
        self.dependencies = dependencies
        self._validate_workflow()

    def _validate_workflow(self):
        """Validate workflow structure."""
        phase_names = set(self.phases.keys())
        for phase_name, deps in self.dependencies.items():
            if phase_name not in phase_names:
                raise ValueError(f"Unknown phase: {phase_name}")
            for dep in deps:
                if dep not in phase_names:
                    raise ValueError(f"Phase '{phase_name}' depends on unknown phase '{dep}'")

    def execute(self, context: ActionContext, pattern: str = "sequential") -> ActionResult:
        """Execute the workflow."""
        if pattern == "sequential":
            return self._execute_sequential(context)
        elif pattern == "parallel":
            return self._execute_parallel(context)
        else:
            raise ValueError(f"Unsupported execution pattern: {pattern}")

    def _execute_sequential(self, context: ActionContext) -> ActionResult:
        """Execute phases in dependency order."""
        completed = set()
        results = {}

        while len(completed) < len(self.phases):
            # Find ready phases
            ready = [
                name for name in self.phases.keys()
                if name not in completed and
                all(dep in completed for dep in self.dependencies.get(name, []))
            ]

            if not ready:
                remaining = [name for name in self.phases.keys() if name not in completed]
                return ActionResult.fail(
                    f"Circular dependency or unsatisfied dependencies for phases: {remaining}"
                )

            # Execute ready phases
            for phase_name in ready:
                result = self.phases[phase_name].execute(context)
                results[phase_name] = result
                if result.success:
                    completed.add(phase_name)
                else:
                    return ActionResult.fail(
                        f"Phase '{phase_name}' failed: {result.message}",
                        failed_phase=phase_name,
                        partial_results=results
                    )

        return ActionResult.ok(
            f"Workflow '{self.name}' completed successfully",
            phase_results=results
        )

    def _execute_parallel(self, context: ActionContext) -> ActionResult:
        """Execute independent phases in parallel."""
        import concurrent.futures
        import threading

        results = {}
        lock = threading.Lock()
        completed = set()

        def execute_phase(phase_name: str):
            # Wait for dependencies
            while not all(dep in completed for dep in self.dependencies.get(phase_name, [])):
                time.sleep(0.1)

            result = self.phases[phase_name].execute(context)
            with lock:
                results[phase_name] = result
                if result.success:
                    completed.add(phase_name)
            return result

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.phases)) as executor:
            futures = [
                executor.submit(execute_phase, name)
                for name in self.phases.keys()
            ]
            concurrent.futures.wait(futures)

        # Check for failures
        failed_phases = [name for name, result in results.items() if not result.success]
        if failed_phases:
            return ActionResult.fail(
                f"Workflow failed at phases: {failed_phases}",
                failed_phases=failed_phases,
                results=results
            )

        return ActionResult.ok(
            f"Workflow '{self.name}' completed successfully",
            phase_results=results
        )


# Convenience functions for common phase patterns
def create_gathering_phase() -> PhaseBuilder:
    """Create a builder for a resource gathering phase."""
    return (PhaseBuilder("Resource Gathering")
            .description("Gather basic resources for progression")
            .with_default_actions())

def create_combat_phase() -> PhaseBuilder:
    """Create a builder for a combat-focused phase."""
    return (PhaseBuilder("Combat Phase")
            .description("Handle combat and defense")
            .with_default_actions())

def create_construction_phase() -> PhaseBuilder:
    """Create a builder for a construction phase."""
    return (PhaseBuilder("Construction Phase")
            .description("Build structures and infrastructure")
            .with_default_actions())


# Advanced workflow patterns
def create_conditional_phase(name: str, condition: Callable[[ActionContext], bool]) -> ConditionalPhaseBuilder:
    """Create a conditional phase builder."""
    return ConditionalPhaseBuilder(name, condition)


def create_workflow(name: str) -> WorkflowBuilder:
    """Create a workflow builder for complex multi-phase automation."""
    return WorkflowBuilder(name)