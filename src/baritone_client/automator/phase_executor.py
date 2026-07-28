"""
Phase Executor - Executes phase-specific automation logic.
"""

import time
from abc import ABC, abstractmethod
from pprint import pformat
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from .state_manager import Phase, StateManager
from ..common.tasks import (
    PlayerDeathDetected,
    SurvivalRecoveryRequired,
    TaskResult,
    normalize_task_result,
)
from .resource_manager import ResourceManager
from .coordination_hub import CoordinationHub, SystemEvent, EventType
import logging

from ..observability import begin_operation, end_operation

if TYPE_CHECKING:
    from .phase_verifier import PhaseVerifier

logger = logging.getLogger(__name__)


class PhaseHandler(ABC):
    """Abstract base class for phase handlers."""
    
    @abstractmethod
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        """
        Execute this phase's automation.
        
        Args:
            client: Baritone client
            resources: Resource manager
            state: State manager
            
        Returns:
            TaskResult describing success/failure and contextual data
        """
        pass
    
    @abstractmethod
    def get_name(self) -> str:
        """Get human-readable phase name."""
        pass
    
    def on_enter(self, client, resources: ResourceManager, state: StateManager) -> None:
        """Called when entering this phase."""
        pass
    
    def on_exit(self, client, resources: ResourceManager, state: StateManager) -> None:
        """Called when exiting this phase."""
        pass


class PhaseExecutor:
    """
    Executes phase handlers with error handling and retry logic.
    
    Provides:
    - Phase handler registration
    - Execution with configurable retries
    - Progress callbacks
    - Error recovery hooks
    """
    
    def __init__(
        self,
        client,
        resources: ResourceManager,
        state: StateManager,
        coordination_hub: Optional["CoordinationHub"] = None,
        max_retries: int = 3,
        retry_delay: float = 5.0,
        screenshot_enabled: bool = True,
        verifier: Optional["PhaseVerifier"] = None,
    ):
        """
        Initialize phase executor.
        
        Args:
            client: Baritone client
            resources: Resource manager
            state: State manager
            max_retries: Maximum retry attempts per phase
            retry_delay: Seconds to wait between retries
            screenshot_enabled: Whether to capture screenshots on phase transitions/errors
        """
        self.client = client
        self.resources = resources
        self.state = state
        # Shared gathering helpers need access to checkpointed home storage
        # when inventory pressure occurs between phase boundaries.
        setattr(self.client, "_automation_state", state)
        self.coordination = coordination_hub
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        
        self.handlers: Dict[Phase, PhaseHandler] = {}
        self.progress_callback: Optional[Callable[[Phase, float, str], None]] = None
        self.error_callback: Optional[Callable[[Phase, Exception], bool]] = None
        self.screenshot_enabled = screenshot_enabled
        self.verifier = verifier
        self.interruption_reason: Optional[str] = None
    
    def register_handler(self, phase: Phase, handler: PhaseHandler) -> None:
        """
        Register a handler for a phase.
        
        Args:
            phase: Phase to handle
            handler: Handler instance
        """
        self.handlers[phase] = handler
    
    def set_progress_callback(self, callback: Callable[[Phase, float, str], None]) -> None:
        """
        Set callback for progress updates.
        
        Callback receives: (phase, progress 0-1, status message)
        """
        self.progress_callback = callback
    
    def set_error_callback(self, callback: Callable[[Phase, Exception], bool]) -> None:
        """
        Set callback for error handling.
        
        Callback receives: (phase, exception)
        Returns: True to retry, False to abort
        """
        self.error_callback = callback
    
    def _report_progress(self, phase: Phase, progress: float, message: str) -> None:
        """Report progress via callback if set."""
        if self.progress_callback:
            self.progress_callback(phase, progress, message)
        self.state.update_progress(progress, phase=phase)
    
    def _take_screenshot(self, reason: str, phase: Optional[Phase] = None) -> None:
        """Take a screenshot if enabled.
        
        Args:
            reason: Reason for screenshot (e.g., 'phase_start', 'phase_complete', 'error')
            phase: Optional phase for filename context
        """
        if not self.screenshot_enabled:
            return
        
        try:
            phase_str = phase.name if phase else "unknown"
            filename = f"{phase_str}_{reason}"
            result = self.client.command.screenshot(filename=filename, reason=reason)
            logger.info(f"Screenshot captured: {result.get('path', 'unknown')}")
        except Exception as e:
            logger.warning(f"Failed to capture screenshot: {e}")
    
    def _coerce_result(self, result: Any) -> TaskResult:
        """Normalize handler return values to TaskResult."""
        return normalize_task_result(result)
    
    def execute_phase(self, phase: Phase) -> bool:
        """
        Execute a specific phase with retry logic.
        
        Args:
            phase: Phase to execute
            
        Returns:
            True if phase completed successfully
        """
        self.interruption_reason = None
        handler = self.handlers.get(phase)
        if handler is None:
            print(f"Warning: No handler registered for {phase.name}")
            return False
        
        print(f"\n{'='*50}")
        print(f"Starting Phase: {handler.get_name()}")
        print(f"{'='*50}")
        
        # Enter phase
        if self.coordination:
            self.coordination.broadcast(SystemEvent(
                EventType.PHASE_CHANGE,
                "phase_executor",
                {"phase": phase.name, "status": "started"}
            ))
        
        handler.on_enter(self.client, self.resources, self.state)
        
        # Screenshot on phase start
        self._take_screenshot("phase_start", phase)
        
        retries = 0
        while retries <= self.max_retries:
            operation = begin_operation(
                "phase_attempt",
                handler.get_name(),
                phase=phase.name,
                attempt=retries + 1,
                max_attempts=self.max_retries + 1,
            )
            try:
                # Refresh inventory before phase
                self.resources.refresh_inventory()
                
                # Execute phase
                self._report_progress(phase, 0.0, f"Starting {handler.get_name()}")
                result = self._coerce_result(handler.execute(self.client, self.resources, self.state))
                if result.success and self.verifier is not None:
                    verification = self.verifier.verify(phase, result)
                    if not verification.success:
                        result = TaskResult.fail(
                            f"Phase postcondition verification failed: {verification.reason}",
                            verification_gate_ids=list(verification.gate_ids),
                            handler_result=result.data,
                        )
                self._log_result_details(phase, result)
                end_operation(
                    operation,
                    "success" if result.success else "failure",
                    reason=result.reason,
                    attempt=retries + 1,
                )
                
                if result.success:
                    self._report_progress(phase, 1.0, f"Completed {handler.get_name()}")
                    handler.on_exit(self.client, self.resources, self.state)
                    self.state.record_phase_payload(phase, result.data)
                    
                    if self.coordination:
                        self.coordination.broadcast(SystemEvent(
                            EventType.TASK_COMPLETED,
                            "phase_executor",
                            {"phase": phase.name, "data": result.data}
                        ))
                    
                    # Screenshot on phase success
                    self._take_screenshot("phase_complete", phase)
                    
                    return True
                else:
                    print(f"Phase {phase.name} reported failure: {result.reason}")
                    self.state.record_phase_payload(phase, result.data)
                    
            except PlayerDeathDetected as exc:
                end_operation(
                    operation,
                    "interrupted",
                    reason=str(exc),
                    interruption="player_death",
                )
                self.interruption_reason = "player_death"
                print(f"Phase {phase.name} interrupted for death recovery: {exc}")
                handler.on_exit(self.client, self.resources, self.state)
                return False
            except SurvivalRecoveryRequired as exc:
                end_operation(
                    operation,
                    "interrupted",
                    reason=str(exc),
                    interruption="survival_recovery",
                )
                self.interruption_reason = "survival_recovery"
                print(f"Phase {phase.name} yielded for survival recovery: {exc}")
                handler.on_exit(self.client, self.resources, self.state)
                # This used to just sleep(2.0) and return, re-entering the
                # phase from the top on the next tick. ensure_supplies raises
                # this specifically when eat_until_hunger fails because
                # nothing is CARRIED -- it never hunts. With no active
                # recovery here, food never rises, so the phase replayed the
                # identical sequence (walk to furnace, smelt, hit the food
                # gate, raise) forever. Confirmed live: Bot08 looped this for
                # 700+ attempts at a fixed food=8 while sitting at full
                # health, never once leaving its base to find food.
                # acquire_emergency_food is the same bounded, threat-checked
                # hunt already used elsewhere for this; give food a real
                # chance to recover before the next attempt instead of a
                # bare pause.
                try:
                    from ..common.combat import acquire_emergency_food

                    acquire_emergency_food(self.client, minimum_food=14, timeout=120.0)
                except PlayerDeathDetected:
                    raise
                except Exception as food_exc:
                    print(
                        f"Phase {phase.name} survival-recovery food attempt "
                        f"failed non-fatally: {food_exc}"
                    )
                time.sleep(2.0)
                return False
            except Exception as e:
                end_operation(
                    operation,
                    "error",
                    reason=str(e),
                    error_type=type(e).__name__,
                    attempt=retries + 1,
                )
                print(f"Error in phase {phase.name}: {e}")
                
                # Screenshot on error
                self._take_screenshot("error", phase)
                
                # Check error callback
                if self.error_callback:
                    should_retry = self.error_callback(phase, e)
                    if not should_retry:
                        handler.on_exit(self.client, self.resources, self.state)
                        return False
            
            retries += 1
            if retries <= self.max_retries:
                print(f"Retry {retries}/{self.max_retries} in {self.retry_delay}s...")
                time.sleep(self.retry_delay)
        
        print(f"Phase {phase.name} failed after {self.max_retries} retries")
        
        # Screenshot on final failure
        self._take_screenshot("phase_failed", phase)
        
        handler.on_exit(self.client, self.resources, self.state)
        return False

    def _log_result_details(self, phase: Phase, result: TaskResult) -> None:
        """Pretty-print TaskResult data for easier debugging."""
        if not result.data:
            return
        print(f"  {phase.name} context:")
        for line in pformat(result.data, compact=True).splitlines():
            print(f"    {line}")
    
    def execute_current_phase(self) -> bool:
        """Execute the current phase from state manager."""
        return self.execute_phase(self.state.get_current_phase())
    
    def has_handler(self, phase: Phase) -> bool:
        """Check if a handler is registered for a phase."""
        return phase in self.handlers
    
    def get_handler(self, phase: Phase) -> Optional[PhaseHandler]:
        """Get handler for a phase."""
        return self.handlers.get(phase)
