"""
EndGame Automator - Automation framework for Minecraft end-game progression.
"""

from .state_manager import StateManager, Phase
from .resource_manager import ResourceManager
from .phase_executor import PhaseExecutor
from .automator import EndGameAutomator
from .actions import (
    Action, MineAction, CraftAction, GotoAction,
    ActionOptimizer, PhaseCondition, PhaseReadinessEvaluator
)
from .telemetry import TelemetrySystem

__all__ = [
    "StateManager",
    "Phase",
    "ResourceManager",
    "PhaseExecutor",
    "EndGameAutomator",
    "Action",
    "MineAction",
    "CraftAction",
    "GotoAction",
    "ActionOptimizer",
    "PhaseCondition",
    "PhaseReadinessEvaluator",
    "TelemetrySystem",
]

