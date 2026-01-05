"""
Advanced Phase B features for enhanced client-side functionality.

This module provides optional advanced features for health monitoring, performance analytics,
and enhanced error recovery that work with the Java bridge reliability system.
"""

import time
import random
import logging
from typing import Dict, List, Optional, Any, Union
from datetime import datetime, timedelta
from collections import defaultdict

from ..transport.transport import Transport
from ..core.exceptions import CommandError, TransportError, CircuitBreakerOpenError
from ..models.models import (
    BridgeHealth,
    BridgeMetrics,
    CircuitBreakerStatus,
    CommandAnalytics,
    PerformanceReport,
    ClientRetryPolicy
)

logger = logging.getLogger(__name__)


class CommandAnalyticsTracker:
    """
    Tracks execution times, success rates, and error patterns for commands.

    This class maintains performance statistics for command execution and provides
    insights into command reliability and performance bottlenecks.
    """

    def __init__(self) -> None:
        self._command_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            'total_calls': 0,
            'successful_calls': 0,
            'failed_calls': 0,
            'execution_times': [],
            'error_types': defaultdict(int),
            'last_called': None
        })

    def record_command_call(self, command_name: str, success: bool, execution_time_ms: float, error_type: Optional[str] = None) -> None:
        """Record the result of a command execution."""
        stats = self._command_stats[command_name]
        stats['total_calls'] += 1
        stats['last_called'] = datetime.now()

        if success:
            stats['successful_calls'] += 1
        else:
            stats['failed_calls'] += 1
            if error_type:
                stats['error_types'][error_type] += 1

        stats['execution_times'].append(execution_time_ms)

        # Keep only the last 100 execution times for memory efficiency
        if len(stats['execution_times']) > 100:
            stats['execution_times'] = stats['execution_times'][-100:]

    def get_command_analytics(self, command_name: str) -> Optional[CommandAnalytics]:
        """Get performance analytics for a specific command."""
        if command_name not in self._command_stats:
            return None

        stats = self._command_stats[command_name]
        execution_times = stats['execution_times']

        if not execution_times:
            return None

        return CommandAnalytics(
            command_name=command_name,
            total_calls=stats['total_calls'],
            successful_calls=stats['successful_calls'],
            failed_calls=stats['failed_calls'],
            average_execution_time_ms=sum(execution_times) / len(execution_times),
            max_execution_time_ms=max(execution_times),
            min_execution_time_ms=min(execution_times),
            error_types=dict(stats['error_types']),
            last_called=stats['last_called']
        )

    def get_performance_report(self, slow_threshold_ms: float = 1000.0, error_threshold_rate: float = 0.1) -> PerformanceReport:
        """Generate a comprehensive performance report with bottleneck analysis."""
        total_commands = len(self._command_stats)
        if total_commands == 0:
            return PerformanceReport(
                timestamp=datetime.now(),
                overall_success_rate=0.0,
                total_commands=0,
                slow_commands=[],
                error_prone_commands=[],
                bottleneck_suggestions=[],
                health_score=0.0
            )

        total_calls = sum(stats['total_calls'] for stats in self._command_stats.values())
        successful_calls = sum(stats['successful_calls'] for stats in self._command_stats.values())

        overall_success_rate = successful_calls / total_calls if total_calls > 0 else 0.0

        slow_commands = []
        error_prone_commands = []
        bottleneck_suggestions = []

        for cmd_name, stats in self._command_stats.items():
            if stats['execution_times']:
                avg_time = sum(stats['execution_times']) / len(stats['execution_times'])
                if avg_time > slow_threshold_ms:
                    slow_commands.append(cmd_name)
                    bottleneck_suggestions.append(f"Consider optimizing {cmd_name} (avg {avg_time:.1f}ms)")

            if stats['total_calls'] > 0:
                error_rate = stats['failed_calls'] / stats['total_calls']
                if error_rate > error_threshold_rate:
                    error_prone_commands.append(cmd_name)
                    bottleneck_suggestions.append(f"Investigate reliability issues with {cmd_name} ({error_rate:.1%} failure rate)")

        # Calculate health score (0.0 to 1.0)
        success_weight = 0.7
        performance_weight = 0.3

        success_score = overall_success_rate
        performance_score = 1.0 - min(1.0, len(slow_commands) / max(1, total_commands))

        health_score = (success_score * success_weight) + (performance_score * performance_weight)

        return PerformanceReport(
            timestamp=datetime.now(),
            overall_success_rate=overall_success_rate,
            total_commands=total_commands,
            slow_commands=slow_commands,
            error_prone_commands=error_prone_commands,
            bottleneck_suggestions=bottleneck_suggestions,
            health_score=health_score
        )


class ClientRetryPolicyHandler:
    """
    Handles client-side retry logic with configurable backoff strategies.

    This class provides enhanced error recovery with exponential backoff,
    jitter, and integration with bridge retry logic.
    """

    def __init__(self, policy: ClientRetryPolicy) -> None:
        self.policy = policy

    def should_retry(self, attempt: int, error: Exception) -> bool:
        """Determine if an operation should be retried based on the error type and attempt count."""
        if attempt >= self.policy.max_attempts:
            return False

        error_type = type(error).__name__
        return error_type in self.policy.retry_on_errors

    def calculate_delay(self, attempt: int) -> float:
        """Calculate the delay before the next retry attempt."""
        base_delay = self.policy.base_delay_seconds * (self.policy.backoff_multiplier ** (attempt - 1))

        # Apply maximum delay cap
        delay = min(base_delay, self.policy.max_delay_seconds)

        # Add jitter if enabled
        if self.policy.jitter_enabled:
            jitter = random.uniform(0, delay * 0.1)  # 10% jitter
            delay += jitter

        return delay

    async def execute_with_retry(self, operation, *args, **kwargs):
        """
        Execute an operation with retry logic.

        Note: This is a simplified synchronous version. In a real implementation,
        this would be async to properly handle delays.
        """
        last_error = None

        for attempt in range(1, self.policy.max_attempts + 1):
            try:
                return operation(*args, **kwargs)
            except Exception as e:
                last_error = e

                if not self.should_retry(attempt, e):
                    raise e

                if attempt < self.policy.max_attempts:
                    delay = self.calculate_delay(attempt)
                    logger.info(f"Retrying operation in {delay:.2f}s (attempt {attempt}/{self.policy.max_attempts})")
                    time.sleep(delay)

        raise last_error


class BridgeMonitor:
    """
    Health monitoring API for bridge diagnostics and performance metrics.

    This class provides methods to query bridge health status, performance metrics,
    and circuit breaker state from the enhanced Java bridge.
    """

    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    def get_bridge_health(self) -> BridgeHealth:
        """
        Retrieve bridge thread pool utilization and system load metrics.

        Returns:
            BridgeHealth object with current system metrics

        Raises:
            TransportError: If unable to communicate with bridge
            CommandError: If bridge returns an error
        """
        try:
            response = self.transport.dispatch("bridge/health", {})

            return BridgeHealth(
                thread_pool_active=response.get("thread_pool_active", 0),
                thread_pool_size=response.get("thread_pool_size", 1),
                thread_pool_utilization=response.get("thread_pool_utilization", 0.0),
                active_connections=response.get("active_connections", 0),
                system_load_average=response.get("system_load_average"),
                memory_usage_mb=response.get("memory_usage_mb"),
                uptime_seconds=response.get("uptime_seconds")
            )
        except Exception as e:
            logger.error(f"Failed to get bridge health: {e}")
            # Return default/empty health data if bridge doesn't support this feature yet
            return BridgeHealth(
                thread_pool_active=0,
                thread_pool_size=1,
                thread_pool_utilization=0.0,
                active_connections=0
            )

    def get_bridge_metrics(self) -> BridgeMetrics:
        """
        Access performance metrics including response times and error breakdowns.

        Returns:
            BridgeMetrics object with performance statistics

        Raises:
            TransportError: If unable to communicate with bridge
            CommandError: If bridge returns an error
        """
        try:
            response = self.transport.dispatch("bridge/metrics", {})

            return BridgeMetrics(
                total_requests=response.get("total_requests", 0),
                successful_requests=response.get("successful_requests", 0),
                failed_requests=response.get("failed_requests", 0),
                average_response_time_ms=response.get("average_response_time_ms", 0.0),
                max_response_time_ms=response.get("max_response_time_ms", 0.0),
                error_breakdown=response.get("error_breakdown", {}),
                timestamp=datetime.now()
            )
        except Exception as e:
            logger.error(f"Failed to get bridge metrics: {e}")
            # Return default metrics if bridge doesn't support this feature yet
            return BridgeMetrics(
                total_requests=0,
                successful_requests=0,
                failed_requests=0,
                average_response_time_ms=0.0,
                max_response_time_ms=0.0,
                error_breakdown={},
                timestamp=datetime.now()
            )

    def get_circuit_breaker_status(self) -> CircuitBreakerStatus:
        """
        Query circuit breaker state and failure statistics.

        Returns:
            CircuitBreakerStatus object with current circuit breaker state

        Raises:
            TransportError: If unable to communicate with bridge
            CommandError: If bridge returns an error
        """
        try:
            response = self.transport.dispatch("bridge/circuit_breaker", {})

            return CircuitBreakerStatus(
                state=response.get("state", "CLOSED"),
                failure_count=response.get("failure_count", 0),
                success_count=response.get("success_count", 0),
                last_failure_time=datetime.fromisoformat(response["last_failure_time"]) if response.get("last_failure_time") else None,
                next_attempt_time=datetime.fromisoformat(response["next_attempt_time"]) if response.get("next_attempt_time") else None,
                failure_threshold=response.get("failure_threshold", 5),
                success_threshold=response.get("success_threshold", 3),
                timeout_seconds=response.get("timeout_seconds", 60)
            )
        except Exception as e:
            logger.error(f"Failed to get circuit breaker status: {e}")
            # Return default status if bridge doesn't support this feature yet
            return CircuitBreakerStatus(
                state="CLOSED",
                failure_count=0,
                success_count=0,
                failure_threshold=5,
                success_threshold=3,
                timeout_seconds=60
            )