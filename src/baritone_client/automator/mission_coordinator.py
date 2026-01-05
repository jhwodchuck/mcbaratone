"""
Mission Coordination Framework

REFACTORED: Implementation moved to `src/baritone_client/automator/mission/`
"""

from .mission import (
    MissionContext,
    MissionInstanceHandler,
    MissionCoordinator
)

__all__ = [
    "MissionContext",
    "MissionInstanceHandler",
    "MissionCoordinator"
]