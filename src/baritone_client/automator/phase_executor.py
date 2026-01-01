"""
Phase Executor - Executes phase-specific automation logic.
"""

import time
from abc import ABC, abstractmethod
from pprint import pformat
from typing import Any, Callable, Dict, Optional

from .state_manager import Phase, StateManager
from ..common.tasks import TaskResult
from .resource_manager import ResourceManager


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
        max_retries: int = 3,
        retry_delay: float = 5.0,
    ):
        """
        Initialize phase executor.
        
        Args:
            client: Baritone client
            resources: Resource manager
            state: State manager
            max_retries: Maximum retry attempts per phase
            retry_delay: Seconds to wait between retries
        """
        self.client = client
        self.resources = resources
        self.state = state
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        
        self.handlers: Dict[Phase, PhaseHandler] = {}
        self.progress_callback: Optional[Callable[[Phase, float, str], None]] = None
        self.error_callback: Optional[Callable[[Phase, Exception], bool]] = None
    
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
        self.state.update_progress(progress)
    
    def _coerce_result(self, result: Any) -> TaskResult:
        """Normalize handler return values to TaskResult."""
        if isinstance(result, TaskResult):
            return result
        if isinstance(result, bool):
            return TaskResult.ok() if result else TaskResult.fail("Phase handler returned False")
        return TaskResult.ok(data={"result": result})
    
    def execute_phase(self, phase: Phase) -> bool:
        """
        Execute a specific phase with retry logic.
        
        Args:
            phase: Phase to execute
            
        Returns:
            True if phase completed successfully
        """
        handler = self.handlers.get(phase)
        if handler is None:
            print(f"Warning: No handler registered for {phase.name}")
            return False
        
        print(f"\n{'='*50}")
        print(f"Starting Phase: {handler.get_name()}")
        print(f"{'='*50}")
        
        # Enter phase
        handler.on_enter(self.client, self.resources, self.state)
        
        retries = 0
        while retries <= self.max_retries:
            try:
                # Refresh inventory before phase
                self.resources.refresh_inventory()
                
                # Execute phase
                self._report_progress(phase, 0.0, f"Starting {handler.get_name()}")
                result = self._coerce_result(handler.execute(self.client, self.resources, self.state))
                self._log_result_details(phase, result)
                
                if result.success:
                    self._report_progress(phase, 1.0, f"Completed {handler.get_name()}")
                    handler.on_exit(self.client, self.resources, self.state)
                    self.state.record_phase_payload(phase, result.data)
                    return True
                else:
                    print(f"Phase {phase.name} reported failure: {result.reason}")
                    self.state.record_phase_payload(phase, result.data)
                    
            except Exception as e:
                print(f"Error in phase {phase.name}: {e}")
                
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
