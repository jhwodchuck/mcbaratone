"""
Telemetry Module - Performance metrics tracking for automation.

Provides:
- TelemetrySystem for collecting and reporting metrics
- Metric types for various performance indicators
- Automatic logging and reporting
"""

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from enum import Enum, auto

logger = logging.getLogger(__name__)


class MetricType(Enum):
    """Types of metrics that can be tracked."""
    COUNTER = auto()     # Incremental values (blocks_mined, items_crafted)
    GAUGE = auto()       # Point-in-time values (health, food)
    TIMER = auto()       # Duration measurements (task_duration)
    HISTOGRAM = auto()   # Distribution of values


@dataclass
class Metric:
    """A single metric measurement."""
    name: str
    value: float
    metric_type: MetricType
    timestamp: float = field(default_factory=time.time)
    tags: Dict[str, str] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "type": self.metric_type.name,
            "timestamp": self.timestamp,
            "tags": self.tags
        }


@dataclass
class TaskTiming:
    """Timing information for a task."""
    task_name: str
    start_time: float
    end_time: Optional[float] = None
    success: bool = True
    
    @property
    def duration(self) -> float:
        if self.end_time:
            return self.end_time - self.start_time
        return time.time() - self.start_time


class TelemetrySystem:
    """
    Central telemetry system for tracking automation performance.
    
    Tracks:
    - Counters (blocks mined, items crafted, deaths)
    - Timers (phase duration, task duration)
    - Gauges (current health, food level)
    
    Usage:
        telemetry = TelemetrySystem()
        telemetry.increment("blocks_mined", 5, block_type="minecraft:oak_log")
        telemetry.start_timer("mine_task")
        # ... do work ...
        telemetry.stop_timer("mine_task")
        telemetry.write_report()
    """
    
    def __init__(self, output_dir: Optional[str] = None):
        """
        Initialize telemetry system.
        
        Args:
            output_dir: Directory for metrics files (default: cwd).
        """
        self.output_dir = Path(output_dir) if output_dir else Path.cwd()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Counters: name -> value
        self._counters: Dict[str, float] = {}
        
        # Gauges: name -> (value, timestamp)
        self._gauges: Dict[str, tuple] = {}
        
        # Active timers: name -> start_time
        self._active_timers: Dict[str, float] = {}
        
        # Completed task timings
        self._task_timings: List[TaskTiming] = []
        
        # Raw metric history for detailed analysis
        self._history: List[Metric] = []
        
        # Session metadata
        self._session_start = time.time()
        self._session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        logger.info(f"Telemetry session started: {self._session_id}")
    
    def increment(self, name: str, value: float = 1.0, **tags) -> None:
        """
        Increment a counter metric.
        
        Args:
            name: Metric name (e.g., "blocks_mined").
            value: Amount to increment (default 1).
            **tags: Additional context tags.
        """
        if name not in self._counters:
            self._counters[name] = 0.0
        self._counters[name] += value
        
        metric = Metric(name, self._counters[name], MetricType.COUNTER, tags=tags)
        self._history.append(metric)
        logger.debug(f"Counter {name}: {self._counters[name]}")
    
    def gauge(self, name: str, value: float, **tags) -> None:
        """
        Set a gauge metric.
        
        Args:
            name: Metric name (e.g., "health").
            value: Current value.
            **tags: Additional context tags.
        """
        self._gauges[name] = (value, time.time())
        metric = Metric(name, value, MetricType.GAUGE, tags=tags)
        self._history.append(metric)
        logger.debug(f"Gauge {name}: {value}")
    
    def start_timer(self, name: str) -> None:
        """
        Start a timer for duration tracking.
        
        Args:
            name: Timer name (e.g., "phase_iron_age").
        """
        self._active_timers[name] = time.time()
        logger.debug(f"Timer started: {name}")
    
    def stop_timer(self, name: str, success: bool = True) -> float:
        """
        Stop a timer and record duration.
        
        Args:
            name: Timer name.
            success: Whether the task succeeded.
            
        Returns:
            Duration in seconds.
        """
        if name not in self._active_timers:
            logger.warning(f"Timer {name} was not started")
            return 0.0
        
        start = self._active_timers.pop(name)
        duration = time.time() - start
        
        timing = TaskTiming(name, start, time.time(), success)
        self._task_timings.append(timing)
        
        metric = Metric(name, duration, MetricType.TIMER)
        self._history.append(metric)
        
        logger.debug(f"Timer {name}: {duration:.2f}s (success={success})")
        return duration
    
    def get_counter(self, name: str) -> float:
        """Get current counter value."""
        return self._counters.get(name, 0.0)
    
    def get_gauge(self, name: str) -> Optional[float]:
        """Get current gauge value."""
        if name in self._gauges:
            return self._gauges[name][0]
        return None
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all metrics."""
        session_duration = time.time() - self._session_start
        
        return {
            "session_id": self._session_id,
            "session_duration_seconds": round(session_duration, 2),
            "counters": dict(self._counters),
            "gauges": {k: v[0] for k, v in self._gauges.items()},
            "completed_tasks": len(self._task_timings),
            "active_timers": list(self._active_timers.keys()),
            "total_metrics_recorded": len(self._history),
        }
    
    def write_report(self, filename: Optional[str] = None) -> str:
        """
        Write metrics report to file.
        
        Args:
            filename: Output filename (default: metrics_{session_id}.json).
            
        Returns:
            Path to written file.
        """
        if filename is None:
            filename = f"metrics_{self._session_id}.json"
        
        filepath = self.output_dir / filename
        
        report = {
            "session": {
                "id": self._session_id,
                "start_time": self._session_start,
                "end_time": time.time(),
                "duration_seconds": time.time() - self._session_start,
            },
            "counters": dict(self._counters),
            "gauges": {k: {"value": v[0], "timestamp": v[1]} for k, v in self._gauges.items()},
            "task_timings": [
                {
                    "name": t.task_name,
                    "duration": t.duration,
                    "success": t.success,
                }
                for t in self._task_timings
            ],
            "summary": {
                "total_tasks": len(self._task_timings),
                "successful_tasks": sum(1 for t in self._task_timings if t.success),
                "failed_tasks": sum(1 for t in self._task_timings if not t.success),
                "avg_task_duration": (
                    sum(t.duration for t in self._task_timings) / len(self._task_timings)
                    if self._task_timings else 0
                ),
            }
        }
        
        with open(filepath, "w") as f:
            json.dump(report, f, indent=2)
        
        logger.info(f"Metrics report written to {filepath}")
        return str(filepath)
    
    def write_log(self, filename: str = "metrics.log") -> str:
        """
        Write human-readable metrics log.
        
        Args:
            filename: Output filename.
            
        Returns:
            Path to written file.
        """
        filepath = self.output_dir / filename
        session_duration = time.time() - self._session_start
        
        lines = [
            f"=== Telemetry Report ===",
            f"Session: {self._session_id}",
            f"Duration: {session_duration:.1f}s ({session_duration/60:.1f} min)",
            "",
            "--- Counters ---",
        ]
        
        for name, value in sorted(self._counters.items()):
            lines.append(f"  {name}: {value}")
        
        lines.append("")
        lines.append("--- Task Timings ---")
        
        for timing in self._task_timings:
            status = "✓" if timing.success else "✗"
            lines.append(f"  {status} {timing.task_name}: {timing.duration:.2f}s")
        
        lines.append("")
        lines.append(f"Total tasks: {len(self._task_timings)}")
        lines.append(f"Metrics recorded: {len(self._history)}")
        
        with open(filepath, "a") as f:
            f.write("\n".join(lines) + "\n\n")
        
        logger.info(f"Metrics log written to {filepath}")
        return str(filepath)
    
    def reset(self) -> None:
        """Reset all metrics for a new session."""
        self._counters.clear()
        self._gauges.clear()
        self._active_timers.clear()
        self._task_timings.clear()
        self._history.clear()
        self._session_start = time.time()
        self._session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        logger.info(f"Telemetry reset. New session: {self._session_id}")
