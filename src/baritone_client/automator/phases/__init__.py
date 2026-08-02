"""
Phase Handlers - Implementations for each automation phase.
"""

from .bridge_check import BridgeCheckHandler
from .spawn_bootstrap import SpawnBootstrapHandler
from .initial_gathering import InitialGatheringHandler
from .base_construction import BaseConstructionHandler
from .industrial_automation import (
    BootSequenceHandler,
    FoodAndIronHandler,
    EnchantingPipelineHandler,
    NetherAndBlazeHandler,
    VillagerInfraHandler,
    XpEngineHandler,
    IronFarmHandler,
    ToolPerfectionHandler,
    WorldUnlockHandler,
    MegabaseInitHandler,
)
from .terraforming import TerraformingHandler
from .city_building import CityBuildingHandler


__all__ = [
    "BridgeCheckHandler",
    "SpawnBootstrapHandler",
    "InitialGatheringHandler",
    "BaseConstructionHandler",
    "BootSequenceHandler",
    "FoodAndIronHandler",
    "EnchantingPipelineHandler",
    "NetherAndBlazeHandler",
    "VillagerInfraHandler",
    "XpEngineHandler",
    "IronFarmHandler",
    "ToolPerfectionHandler",
    "WorldUnlockHandler",
    "MegabaseInitHandler",
    "TerraformingHandler",
    "CityBuildingHandler",
]
