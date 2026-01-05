"""
Base action implementation.
"""

from baritone_client.core.interfaces import IAction, ActionContext, ActionResult
from baritone_client.core.exceptions import CommandError

class BaseAction(IAction):
    """Base class for actions with common utility methods."""
    
    def run_command(self, context: ActionContext, command: str, params: dict) -> dict:
        """Helper to run a client command safely."""
        try:
            return context.client.transport.dispatch(command, params)
        except Exception as e:
            # Wrap transport errors
            raise CommandError(f"Action command failed: {command}") from e

    def execute(self, context: ActionContext) -> ActionResult:
        raise NotImplementedError("Actions must implement execute()")
