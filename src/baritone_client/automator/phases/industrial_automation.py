"""
Industrial Automation Phases - Detailed implementations for 10-phase industrialization.

REFACTORED: Implementation moved to individual files in `phases/`.
"""

from .boot_sequence import BootSequenceHandler
from .iron_age import FoodAndIronHandler
from .enchanting import EnchantingPipelineHandler
from .nether_prep import NetherAndBlazeHandler
from .villager import VillagerInfraHandler
from .xp_engine import XpEngineHandler
from .iron_farm import IronFarmHandler
from .trading import ToolPerfectionHandler
from .end_game import WorldUnlockHandler
from .megabase import MegabaseInitHandler

# Re-export for compatibility
__all__ = [
    "BootSequenceHandler",
    "FoodAndIronHandler", 
    "EnchantingPipelineHandler",
    "NetherAndBlazeHandler",
    "VillagerInfraHandler",
    "XpEngineHandler",
    "IronFarmHandler",
    "ToolPerfectionHandler",
    "WorldUnlockHandler",
    "MegabaseInitHandler"
]
