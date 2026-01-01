"""
Phase Handlers - Implementations for each automation phase.
"""

from .spawn_bootstrap import SpawnBootstrapHandler
from .initial_gathering import InitialGatheringHandler
from .base_construction import BaseConstructionHandler
from .iron_age import IronAgeHandler
from .diamond_mining import DiamondMiningHandler
from .enchanting import EnchantingHandler
from .nether_prep import NetherPrepHandler
from .nether_travel import NetherTravelHandler
from .ender_pearls import EnderPearlHandler
from .stronghold import StrongholdHandler
from .end_portal import EndPortalHandler
from .dragon_fight import DragonFightHandler
from .bridge_check import BridgeCheckHandler

__all__ = [
    "SpawnBootstrapHandler",
    "InitialGatheringHandler",
    "BaseConstructionHandler",
    "IronAgeHandler",
    "DiamondMiningHandler",
    "EnchantingHandler",
    "NetherPrepHandler",
    "NetherTravelHandler",
    "EnderPearlHandler",
    "StrongholdHandler",
    "EndPortalHandler",
    "DragonFightHandler",
    "BridgeCheckHandler",
]
