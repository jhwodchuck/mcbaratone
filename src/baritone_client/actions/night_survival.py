"""
Night survival actions for initial gathering phase.
"""

import time
from .base import BaseAction
from ..core.interfaces import ActionContext, ActionResult
from ..common.base import build_emergency_shelter, sleep_through_night


class NightSurvivalAction(BaseAction):
    """Action for handling night time survival - sleeping or building shelter."""

    def execute(self, context: ActionContext) -> ActionResult:
        """
        Ensure survival through the night by sleeping in bed or building emergency shelter.
        """
        print("Action: Checking night survival...")

        # Check if it's night time
        state = context.client.transport.dispatch("get_state", {})
        world_time = state.get("world_time", 0)
        is_night = (world_time % 24000) >= 13000  # Night starts at 13000

        if not is_night:
            print("  It is not night time, no action needed")
            return ActionResult.ok("Not night time")

        print("  Night detected, ensuring survival...")

        # Try to sleep through the night first
        if sleep_through_night(context.client):
            print("  Successfully slept through night")
            return ActionResult.ok("Slept through night")
        else:
            print("  Sleep failed or no bed available, building emergency shelter...")

            # Build emergency shelter
            if build_emergency_shelter(context.client):
                print("  Emergency shelter built successfully")

                # Wait for morning after building shelter
                self._wait_for_morning(context)
                return ActionResult.ok("Emergency shelter built and survived night")
            else:
                print("  Failed to build emergency shelter")
                # Still try to wait for morning
                self._wait_for_morning(context)
                return ActionResult.fail("Failed to build shelter, but waited for morning")

    def _wait_for_morning(self, context: ActionContext, max_wait_seconds: int = 300) -> None:
        """Wait for morning to break."""
        print("  Waiting for morning...")
        waited = 0
        while waited < max_wait_seconds:
            state = context.client.transport.dispatch("get_state", {})
            world_time = state.get("world_time", 0)
            if (world_time % 24000) < 1000:  # Morning starts at 0-1000
                print("  Morning has broken!")
                break
            time.sleep(10)
            waited += 10
        else:
            print("  Timed out waiting for morning")