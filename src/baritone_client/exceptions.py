class TransportError(RuntimeError):
    """Raised when the underlying transport fails."""


class ValidationError(ValueError):
    """Raised for invalid or incompatible arguments."""


class CommandError(RuntimeError):
    """Raised when Baritone returns a command error or invalid state."""
