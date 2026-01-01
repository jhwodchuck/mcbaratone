"""
EndGame Automator - Automation framework for Minecraft end-game progression.
"""

from .state_manager import StateManager, Phase
from .resource_manager import ResourceManager
from .phase_executor import PhaseExecutor
from .automator import EndGameAutomator

__all__ = [
    "StateManager",
    "Phase",
    "ResourceManager",
    "PhaseExecutor",
    "EndGameAutomator",
]
