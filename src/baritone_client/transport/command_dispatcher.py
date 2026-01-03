"""CommandDispatcher for orchestrating command execution using handler pattern."""

import logging
import time
from typing import Any, Dict, Optional

from ..core.exceptions import CommandError, TransportError
from .transport import Transport

logger = logging.getLogger(__name__)


class CommandResult:
    """Represents the standardized response from command handlers.

    Provides a consistent interface for success/error responses with optional data payload.
    """

    def __init__(self, success: bool, data: Optional[Dict[str, Any]] = None, error_message: Optional[str] = None):
        """
        Initialize a CommandResult.

        Args:
            success: Whether the command succeeded
            data: Response data payload (optional)
            error_message: Error message if failed (optional)
        """
        self.success = success
        self.data = data or {}
        self.error_message = error_message if not success else None

    @classmethod
    def success(cls, data: Optional[Dict[str, Any]] = None) -> "CommandResult":
        """Create a successful result with optional data."""
        return cls(success=True, data=data)

    @classmethod
    def error(cls, error_message: str) -> "CommandResult":
        """Create an error result with message."""
        return cls(success=False, error_message=error_message)

    def is_success(self) -> bool:
        """Check if the command result indicates success."""
        return self.success

    def get_data(self) -> Dict[str, Any]:
        """Get the response data payload."""
        return self.data

    def get_error_message(self) -> Optional[str]:
        """Get the error message if command failed."""
        return self.error_message

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary suitable for network transmission."""
        result = {"status": "ok" if self.success else "error", "data": self.data}
        if not self.success and self.error_message:
            result["error"] = self.error_message
        return result


class CommandDispatcher:
    """Orchestrates command execution using handler pattern with rate limiting and timeout management.

    This class serves as the main entry point for command processing, replacing the monolithic
    handleCommand logic by dispatching to appropriate handlers on the bridge side.
    """

    def __init__(
        self,
        transport: Transport,
        rate_limit_requests: int = 500,
        rate_limit_window_ms: int = 10000,
        default_timeout: Optional[float] = None,
    ):
        """
        Initialize the CommandDispatcher.

        Args:
            transport: Transport instance for communication with bridge
            rate_limit_requests: Maximum requests per rate limit window
            rate_limit_window_ms: Rate limit window in milliseconds
            default_timeout: Default timeout for commands in seconds
        """
        self.transport = transport
        self.rate_limit_requests = rate_limit_requests
        self.rate_limit_window_ms = rate_limit_window_ms
        self.default_timeout = default_timeout

        # Rate limiting state
        self._last_request_times: Dict[str, float] = {}
        self._request_counts: Dict[str, int] = {}

        # For rate limiting, we'll use a simple client identifier
        # In a real implementation, this could be per-client or per-IP
        self._client_id = "default"

    def dispatch(
        self,
        command: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> CommandResult:
        """
        Dispatch a command for execution.

        Uses CommandHandlerFactory on bridge side to get appropriate handlers,
        falls back to legacy processing for commands not yet extracted.

        Args:
            command: Command name to execute
            params: Command parameters
            timeout: Override default timeout in seconds

        Returns:
            CommandResult with execution outcome

        Raises:
            CommandError: If command execution fails
            TransportError: If transport fails
        """
        # Apply rate limiting
        if not self._check_rate_limit():
            return CommandResult.error("Rate limit exceeded. Please try again later.")

        effective_timeout = timeout or self.default_timeout

        try:
            # Dispatch to bridge - bridge will use CommandHandlerFactory if available,
            # otherwise fallback to legacy handleCommand logic
            payload = {"command": command}
            if params:
                payload["params"] = params

            logger.info(f"Dispatching command '{command}' with params {params}")
            # Use transport dispatch with command route
            response = self.transport.dispatch("command", payload, timeout=effective_timeout)

            # Bridge returns status "ok" or "error"
            if response.get("status") == "ok":
                return CommandResult.success(response.get("data", {}))
            else:
                error_msg = response.get("error", "Unknown error")
                return CommandResult.error(error_msg)

        except TransportError as e:
            logger.error(f"Transport error dispatching command {command}: {e}")
            return CommandResult.error(f"Transport error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error dispatching command {command}: {e}")
            return CommandResult.error(f"Unexpected error: {e}")

    def _check_rate_limit(self) -> bool:
        """Check if request is within rate limits."""
        now = time.time() * 1000  # milliseconds
        last_time = self._last_request_times.get(self._client_id, 0)

        if now - last_time > self.rate_limit_window_ms:
            # New window
            self._last_request_times[self._client_id] = now
            self._request_counts[self._client_id] = 1
            return True

        count = self._request_counts.get(self._client_id, 0) + 1
        self._request_counts[self._client_id] = count

        if count > self.rate_limit_requests:
            return False

        return True

    def get_rate_limit_info(self) -> Dict[str, Any]:
        """Get current rate limiting information."""
        now = time.time() * 1000
        last_time = self._last_request_times.get(self._client_id, 0)
        count = self._request_counts.get(self._client_id, 0)

        return {
            "current_count": count,
            "limit": self.rate_limit_requests,
            "window_ms": self.rate_limit_window_ms,
            "time_until_reset_ms": max(0, self.rate_limit_window_ms - (now - last_time)),
        }