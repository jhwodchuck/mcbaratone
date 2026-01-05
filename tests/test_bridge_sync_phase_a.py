"""Tests for Phase A of Python Bridge Sync: Minimal Backward-Compatible Updates."""

import pytest

from baritone_client.core.exceptions import CircuitBreakerOpenError, RetryExhaustedError, ValidationError
from baritone_client.transport.command_dispatcher import CommandResult, CommandDispatcher


class MockTransport:
    """Mock transport that returns predefined responses."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.dispatched = []
        self.events = MagicMock()

    def dispatch(self, route, payload, **kwargs):
        self.dispatched.append((route, payload))
        return self.responses.get(route, {"status": "ok", "data": {}})

    def subscribe(self, event, callback):
        self.events.subscribe(event, callback)

    def emit(self, event, payload):
        self.events.dispatch(event, payload)

    def shutdown(self):
        pass


class TestCommandResult:
    """Test the enhanced CommandResult class."""

    def test_success_result_with_metadata(self):
        """Test successful result includes reliability metadata."""
        retry_status = {"attempts": 2, "final_success": True}
        circuit_breaker_state = {"state": "closed", "failure_count": 0}
        priority_level = "high"

        result = CommandResult.success(
            {"inventory": []},
            retry_status=retry_status,
            circuit_breaker_state=circuit_breaker_state,
            priority_level=priority_level
        )

        assert result.is_success()
        assert result.get_data() == {"inventory": []}
        assert result.get_retry_status() == retry_status
        assert result.get_circuit_breaker_state() == circuit_breaker_state
        assert result.get_priority_level() == priority_level

    def test_error_result_with_metadata(self):
        """Test error result includes reliability metadata."""
        retry_status = {"attempts": 3, "final_success": False}
        circuit_breaker_state = {"state": "half_open", "failure_count": 5}

        result = CommandResult.error(
            "Operation failed",
            retry_status=retry_status,
            circuit_breaker_state=circuit_breaker_state,
            priority_level="normal"
        )

        assert not result.is_success()
        assert result.get_error_message() == "Operation failed"
        assert result.get_retry_status() == retry_status
        assert result.get_circuit_breaker_state() == circuit_breaker_state
        assert result.get_priority_level() == "normal"

    def test_to_dict_includes_metadata(self):
        """Test that to_dict includes reliability metadata."""
        result = CommandResult.success(
            {"data": "test"},
            retry_status={"attempts": 1},
            circuit_breaker_state={"state": "closed"},
            priority_level="low"
        )

        dict_result = result.to_dict()
        assert dict_result["status"] == "ok"
        assert dict_result["data"] == {"data": "test"}
        assert dict_result["retry_status"] == {"attempts": 1}
        assert dict_result["circuit_breaker_state"] == {"state": "closed"}
        assert dict_result["priority_level"] == "low"


class TestCommandDispatcher:
    """Test the enhanced CommandDispatcher."""

    def test_circuit_breaker_error_raises_exception(self):
        """Test that circuit breaker errors raise CircuitBreakerOpenError."""
        transport = MockTransport({
            "command": {
                "status": "error",
                "error": "System temporarily unavailable, please retry later",
                "error_code": "CIRCUIT_BREAKER_OPEN",
                "circuit_breaker_state": {"state": "open", "failure_count": 10}
            }
        })

        dispatcher = CommandDispatcher(transport)
        with pytest.raises(CircuitBreakerOpenError) as exc_info:
            dispatcher.dispatch("test_command", {})

        assert "System temporarily unavailable" in str(exc_info.value)
        assert exc_info.value.circuit_breaker_state == {"state": "open", "failure_count": 10}

    def test_retry_exhausted_error_raises_exception(self):
        """Test that retry exhausted errors raise RetryExhaustedError."""
        transport = MockTransport({
            "command": {
                "status": "error",
                "error": "Operation failed after maximum retry attempts",
                "error_code": "RETRY_EXHAUSTED",
                "retry_status": {"attempts": 5, "max_attempts": 5}
            }
        })

        dispatcher = CommandDispatcher(transport)
        with pytest.raises(RetryExhaustedError) as exc_info:
            dispatcher.dispatch("test_command", {})

        assert "maximum retry attempts" in str(exc_info.value)
        assert exc_info.value.retry_status == {"attempts": 5, "max_attempts": 5}

    def test_message_based_error_detection(self):
        """Test that errors are detected from message content when error_code is missing."""
        transport = MockTransport({
            "command": {
                "status": "error",
                "error": "Circuit breaker is open, please wait",
                "circuit_breaker_state": {"state": "open"}
            }
        })

        dispatcher = CommandDispatcher(transport)
        with pytest.raises(CircuitBreakerOpenError):
            dispatcher.dispatch("test_command", {})

    def test_backward_compatibility_error_handling(self):
        """Test that regular errors still return CommandResult for backward compatibility."""
        transport = MockTransport({
            "command": {
                "status": "error",
                "error": "Regular command error",
                "retry_status": {"attempts": 1}
            }
        })

        dispatcher = CommandDispatcher(transport)
        result = dispatcher.dispatch("test_command", {})

        assert isinstance(result, CommandResult)
        assert not result.is_success()
        assert result.get_error_message() == "Regular command error"
        assert result.get_retry_status() == {"attempts": 1}





class TestResponseMetadataHandling:
    """Test that response metadata is properly handled."""

    def test_response_with_full_metadata(self):
        """Test handling responses with all reliability metadata."""
        transport = MockTransport({
            "command": {
                "status": "ok",
                "data": {"result": "success"},
                "retry_status": {"attempts": 2, "final_success": True},
                "circuit_breaker_state": {"state": "closed", "failure_count": 0},
                "priority_level": "high"
            }
        })

        dispatcher = CommandDispatcher(transport)
        result = dispatcher.dispatch("test_command", {})

        assert result.is_success()
        assert result.get_data() == {"result": "success"}
        assert result.get_retry_status() == {"attempts": 2, "final_success": True}
        assert result.get_circuit_breaker_state() == {"state": "closed", "failure_count": 0}
        assert result.get_priority_level() == "high"

    def test_response_with_partial_metadata(self):
        """Test handling responses with only some metadata."""
        transport = MockTransport({
            "command": {
                "status": "ok",
                "data": {"result": "success"},
                "retry_status": {"attempts": 1}
                # Missing circuit_breaker_state and priority_level
            }
        })

        dispatcher = CommandDispatcher(transport)
        result = dispatcher.dispatch("test_command", {})

        assert result.is_success()
        assert result.get_retry_status() == {"attempts": 1}
        assert result.get_circuit_breaker_state() is None
        assert result.get_priority_level() is None

    def test_response_without_metadata(self):
        """Test handling legacy responses without metadata."""
        transport = MockTransport({
            "command": {"status": "ok", "data": {"result": "success"}}
        })

        dispatcher = CommandDispatcher(transport)
        result = dispatcher.dispatch("test_command", {})

        assert result.is_success()
        assert result.get_data() == {"result": "success"}
        assert result.get_retry_status() is None
        assert result.get_circuit_breaker_state() is None
        assert result.get_priority_level() is None


if __name__ == "__main__":
    pytest.main([__file__])