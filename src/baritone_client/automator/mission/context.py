"""
Mission Context Module
"""

import threading
from typing import Optional, Set, Callable
from dataclasses import dataclass, field

from ...models.models import MissionInstance, MissionEvent


@dataclass
class MissionContext:
    """Execution context for a running mission."""
    mission: MissionInstance
    thread: Optional[threading.Thread] = None
    start_time: Optional[float] = None
    allocated_resources: Set[str] = field(default_factory=set)
    event_callback: Optional[Callable[[MissionEvent], None]] = None

    def is_running(self) -> bool:
        """Check if mission context is actively running."""
        return self.thread is not None and self.thread.is_alive()

    def cleanup(self) -> None:
        """Clean up mission context resources."""
        if self.thread and self.thread.is_alive():
            # Note: In production, implement proper thread termination
            pass
        self.allocated_resources.clear()
