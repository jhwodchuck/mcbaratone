"""Core tests for Phase A functionality without circular imports."""

import pytest

# Direct imports to avoid circular imports
from baritone_client.transport.command_dispatcher import CommandResult


class TestCommandResultCore:
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

    def test_backward_compatibility_no_metadata(self):
        """Test that results work without metadata (backward compatibility)."""
        result = CommandResult.success({"data": "test"})

        assert result.is_success()
        assert result.get_data() == {"data": "test"}
        assert result.get_retry_status() is None
        assert result.get_circuit_breaker_state() is None
        assert result.get_priority_level() is None

        dict_result = result.to_dict()
        assert dict_result["status"] == "ok"
        assert dict_result["data"] == {"data": "test"}
        assert "retry_status" not in dict_result
        assert "circuit_breaker_state" not in dict_result
        assert "priority_level" not in dict_result


if __name__ == "__main__":
    pytest.main([__file__])