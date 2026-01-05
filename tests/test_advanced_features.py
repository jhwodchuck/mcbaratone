"""
Unit tests for Phase B advanced features.

Tests health monitoring, client-side analytics, and enhanced error recovery.
"""

import pytest
import time
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock

from baritone_client import (
    Client, TcpTransport, ClientRetryPolicy,
    BridgeHealth, BridgeMetrics, CircuitBreakerStatus,
    CommandAnalytics, PerformanceReport
)
from baritone_client.core.exceptions import TransportError, CommandError


class TestBridgeMonitor:
    """Test bridge monitoring functionality."""

    def test_get_bridge_health_success(self):
        """Test successful bridge health retrieval."""
        mock_transport = Mock()
        mock_transport.dispatch.return_value = {
            "thread_pool_active": 2,
            "thread_pool_size": 10,
            "thread_pool_utilization": 0.2,
            "active_connections": 1,
            "system_load_average": 0.8,
            "memory_usage_mb": 512,
            "uptime_seconds": 3600
        }

        from baritone_client.core.advanced import BridgeMonitor
        monitor = BridgeMonitor(mock_transport)

        health = monitor.get_bridge_health()

        assert isinstance(health, BridgeHealth)
        assert health.thread_pool_active == 2
        assert health.thread_pool_size == 10
        assert health.thread_pool_utilization == 0.2
        assert health.active_connections == 1
        assert health.system_load_average == 0.8
        assert health.memory_usage_mb == 512
        assert health.uptime_seconds == 3600

    def test_get_bridge_health_fallback(self):
        """Test fallback behavior when bridge doesn't support health monitoring."""
        mock_transport = Mock()
        mock_transport.dispatch.side_effect = TransportError("Route not found")

        from baritone_client.core.advanced import BridgeMonitor
        monitor = BridgeMonitor(mock_transport)

        health = monitor.get_bridge_health()

        # Should return default values
        assert isinstance(health, BridgeHealth)
        assert health.thread_pool_active == 0
        assert health.thread_pool_size == 1
        assert health.thread_pool_utilization == 0.0
        assert health.active_connections == 0

    def test_get_bridge_metrics_success(self):
        """Test successful bridge metrics retrieval."""
        mock_transport = Mock()
        mock_transport.dispatch.return_value = {
            "total_requests": 100,
            "successful_requests": 95,
            "failed_requests": 5,
            "average_response_time_ms": 150.5,
            "max_response_time_ms": 500.0,
            "error_breakdown": {"timeout": 3, "command_error": 2}
        }

        from baritone_client.core.advanced import BridgeMonitor
        monitor = BridgeMonitor(mock_transport)

        metrics = monitor.get_bridge_metrics()

        assert isinstance(metrics, BridgeMetrics)
        assert metrics.total_requests == 100
        assert metrics.successful_requests == 95
        assert metrics.failed_requests == 5
        assert metrics.average_response_time_ms == 150.5
        assert metrics.max_response_time_ms == 500.0
        assert metrics.error_breakdown == {"timeout": 3, "command_error": 2}

    def test_get_circuit_breaker_status_success(self):
        """Test successful circuit breaker status retrieval."""
        mock_transport = Mock()
        mock_transport.dispatch.return_value = {
            "state": "HALF_OPEN",
            "failure_count": 3,
            "success_count": 2,
            "last_failure_time": "2024-01-01T12:00:00",
            "next_attempt_time": "2024-01-01T12:01:00",
            "failure_threshold": 5,
            "success_threshold": 3,
            "timeout_seconds": 60
        }

        from baritone_client.core.advanced import BridgeMonitor
        monitor = BridgeMonitor(mock_transport)

        status = monitor.get_circuit_breaker_status()

        assert isinstance(status, CircuitBreakerStatus)
        assert status.state == "HALF_OPEN"
        assert status.failure_count == 3
        assert status.success_count == 2
        assert status.failure_threshold == 5
        assert status.success_threshold == 3
        assert status.timeout_seconds == 60


class TestCommandAnalyticsTracker:
    """Test command analytics tracking."""

    def test_record_and_retrieve_command_analytics(self):
        """Test recording command calls and retrieving analytics."""
        from baritone_client.core.advanced import CommandAnalyticsTracker

        tracker = CommandAnalyticsTracker()

        # Record some command calls
        tracker.record_command_call("mine", True, 500.0)
        tracker.record_command_call("mine", True, 600.0)
        tracker.record_command_call("mine", False, 300.0, "TransportError")
        tracker.record_command_call("goto", True, 1000.0)

        # Get analytics for mine command
        analytics = tracker.get_command_analytics("mine")
        assert analytics is not None
        assert analytics.command_name == "mine"
        assert analytics.total_calls == 3
        assert analytics.successful_calls == 2
        assert analytics.failed_calls == 1
        assert analytics.average_execution_time_ms == 466.6666666666667  # (500+600+300)/3
        assert analytics.max_execution_time_ms == 600.0
        assert analytics.min_execution_time_ms == 300.0
        assert analytics.error_types == {"TransportError": 1}

        # Get analytics for non-existent command
        assert tracker.get_command_analytics("nonexistent") is None

    def test_performance_report_generation(self):
        """Test generation of comprehensive performance reports."""
        from baritone_client.core.advanced import CommandAnalyticsTracker

        tracker = CommandAnalyticsTracker()

        # Add some test data
        tracker.record_command_call("fast_command", True, 100.0)
        tracker.record_command_call("fast_command", True, 150.0)

        tracker.record_command_call("slow_command", True, 1500.0)  # Over 1 second
        tracker.record_command_call("slow_command", True, 2000.0)

        tracker.record_command_call("error_command", False, 500.0, "CommandError")
        tracker.record_command_call("error_command", False, 600.0, "TransportError")
        tracker.record_command_call("error_command", True, 700.0)  # 1 success out of 3 = 33% failure rate

        report = tracker.get_performance_report()

        assert isinstance(report, PerformanceReport)
        assert report.total_commands == 3
        assert "slow_command" in report.slow_commands
        assert "error_command" in report.error_prone_commands
        assert len(report.bottleneck_suggestions) > 0
        assert 0.0 <= report.health_score <= 1.0


class TestClientRetryPolicy:
    """Test client-side retry policy functionality."""

    def test_retry_policy_creation(self):
        """Test creation and configuration of retry policies."""
        from baritone_client.core.advanced import ClientRetryPolicyHandler

        policy = ClientRetryPolicy(
            max_attempts=5,
            base_delay_seconds=2.0,
            retry_on_errors=["TransportError", "CommandError"]
        )

        handler = ClientRetryPolicyHandler(policy)

        assert handler.policy.max_attempts == 5
        assert handler.policy.base_delay_seconds == 2.0
        assert handler.policy.retry_on_errors == ["TransportError", "CommandError"]

    def test_should_retry_logic(self):
        """Test retry decision logic."""
        from baritone_client.core.advanced import ClientRetryPolicyHandler

        policy = ClientRetryPolicy(retry_on_errors=["TransportError"])
        handler = ClientRetryPolicyHandler(policy)

        # Should retry on specified error
        assert handler.should_retry(1, TransportError("test")) == True

        # Should not retry on unspecified error
        assert handler.should_retry(1, ValueError("test")) == False

        # Should not retry when max attempts reached
        assert handler.should_retry(3, TransportError("test")) == False

    def test_delay_calculation(self):
        """Test exponential backoff delay calculation."""
        from baritone_client.core.advanced import ClientRetryPolicyHandler

        policy = ClientRetryPolicy(
            base_delay_seconds=1.0,
            max_delay_seconds=10.0,
            backoff_multiplier=2.0,
            jitter_enabled=False
        )
        handler = ClientRetryPolicyHandler(policy)

        # First retry: 1.0 * (2^0) = 1.0
        assert handler.calculate_delay(1) == 1.0

        # Second retry: 1.0 * (2^1) = 2.0
        assert handler.calculate_delay(2) == 2.0

        # Third retry: 1.0 * (2^2) = 4.0
        assert handler.calculate_delay(3) == 4.0

    def test_delay_calculation_with_max(self):
        """Test that delays are capped at max_delay_seconds."""
        from baritone_client.core.advanced import ClientRetryPolicyHandler

        policy = ClientRetryPolicy(
            base_delay_seconds=1.0,
            max_delay_seconds=3.0,
            backoff_multiplier=2.0,
            jitter_enabled=False
        )
        handler = ClientRetryPolicyHandler(policy)

        # Should be capped at 3.0
        assert handler.calculate_delay(10) == 3.0


class TestClientIntegration:
    """Test integration of advanced features with Client class."""

    def test_client_advanced_features_initialization(self):
        """Test that advanced features are properly initialized in Client."""
        mock_transport = Mock()
        client = Client(mock_transport)

        # Check that advanced features are initialized
        assert hasattr(client, '_analytics')
        assert hasattr(client, '_bridge_monitor')
        assert client._retry_policy is None  # Initially None until set

    def test_set_retry_policy(self):
        """Test setting retry policy on client."""
        mock_transport = Mock()
        client = Client(mock_transport)

        policy = ClientRetryPolicy(max_attempts=3)
        client.set_retry_policy(policy)

        assert client._retry_policy is not None
        assert client._retry_policy.policy.max_attempts == 3

    def test_bridge_health_method(self):
        """Test bridge health method delegation."""
        mock_transport = Mock()
        mock_transport.dispatch.return_value = {
            "thread_pool_active": 1,
            "thread_pool_size": 5,
            "thread_pool_utilization": 0.2,
            "active_connections": 2
        }

        client = Client(mock_transport)
        health = client.get_bridge_health()

        assert isinstance(health, BridgeHealth)
        assert health.thread_pool_active == 1
        assert health.active_connections == 2

    def test_bridge_metrics_method(self):
        """Test bridge metrics method delegation."""
        mock_transport = Mock()
        mock_transport.dispatch.return_value = {
            "total_requests": 50,
            "successful_requests": 48,
            "failed_requests": 2,
            "average_response_time_ms": 200.0,
            "max_response_time_ms": 1000.0,
            "error_breakdown": {"timeout": 2}
        }

        client = Client(mock_transport)
        metrics = client.get_bridge_metrics()

        assert isinstance(metrics, BridgeMetrics)
        assert metrics.total_requests == 50
        assert metrics.successful_requests == 48

    def test_circuit_breaker_status_method(self):
        """Test circuit breaker status method delegation."""
        mock_transport = Mock()
        mock_transport.dispatch.return_value = {
            "state": "CLOSED",
            "failure_count": 0,
            "success_count": 10,
            "failure_threshold": 5,
            "success_threshold": 3,
            "timeout_seconds": 60
        }

        client = Client(mock_transport)
        status = client.get_circuit_breaker_status()

        assert isinstance(status, CircuitBreakerStatus)
        assert status.state == "CLOSED"
        assert status.failure_count == 0

    def test_command_analytics_methods(self):
        """Test command analytics methods."""
        mock_transport = Mock()
        client = Client(mock_transport)

        # Initially no analytics
        assert client.get_command_analytics("test") is None

        # Simulate some command tracking (would normally happen during actual command execution)
        client._analytics.record_command_call("test", True, 100.0)
        client._analytics.record_command_call("test", True, 200.0)

        analytics = client.get_command_analytics("test")
        assert analytics is not None
        assert analytics.total_calls == 2
        assert analytics.average_execution_time_ms == 150.0

    def test_performance_report_method(self):
        """Test performance report generation."""
        mock_transport = Mock()
        client = Client(mock_transport)

        # Add some test data
        client._analytics.record_command_call("good_cmd", True, 100.0)
        client._analytics.record_command_call("bad_cmd", False, 500.0, "error")

        report = client.get_performance_report()

        assert isinstance(report, PerformanceReport)
        assert report.total_commands == 2
        assert isinstance(report.health_score, float)