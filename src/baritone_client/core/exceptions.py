class TransportError(RuntimeError):
    """Raised when the underlying transport fails."""
    
    def __init__(self, message: str, original_error: Exception = None):
        super().__init__(message)
        self.original_error = original_error


class BridgeResponseTimeout(TransportError):
    """Raised when a sent bridge request receives no correlated response."""

    def __init__(
        self,
        route: str,
        *,
        transport_type: str,
        request_sent: bool,
    ) -> None:
        message = (
            "Timeout waiting for bridge response "
            f"(route: {route}, transport: {transport_type})"
        )
        super().__init__(message)
        self.route = route
        self.transport_type = transport_type
        self.request_sent = request_sent


class ValidationError(ValueError):
    """Raised for invalid or incompatible arguments."""
    
    def __init__(self, message: str, field: str = None):
        super().__init__(message)
        self.field = field


class CommandError(RuntimeError):
    """Raised when Baritone returns a command error or invalid state."""
    
    def __init__(self, message: str, command: str = None, error_code: str = None):
        super().__init__(message)
        self.command = command
        self.error_code = error_code


class RouteError(TransportError):
    """Raised when a route is not supported by the transport."""

    def __init__(self, route: str, transport_type: str = None):
        message = f"Route '{route}' is not supported"
        if transport_type:
            message += f" by {transport_type}"
        super().__init__(message)
        self.route = route
        self.transport_type = transport_type


class CircuitBreakerOpenError(CommandError):
    """Raised when the circuit breaker is open and requests are being rejected."""

    def __init__(self, message: str = "System temporarily unavailable, please retry later", circuit_breaker_state: dict = None):
        super().__init__(message, error_code="CIRCUIT_BREAKER_OPEN")
        self.circuit_breaker_state = circuit_breaker_state or {}


class RetryExhaustedError(CommandError):
    """Raised when all retry attempts have been exhausted."""

    def __init__(self, message: str = "Operation failed after maximum retry attempts", retry_status: dict = None):
        super().__init__(message, error_code="RETRY_EXHAUSTED")
        self.retry_status = retry_status or {}
