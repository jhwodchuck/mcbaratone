class TransportError(RuntimeError):
    """Raised when the underlying transport fails."""
    
    def __init__(self, message: str, original_error: Exception = None):
        super().__init__(message)
        self.original_error = original_error


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
