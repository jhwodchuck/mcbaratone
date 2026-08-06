"""Value types for bounded work selected between progression objectives."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple


class OpportunityKind(str, Enum):
    """Small recurring work that may run between progression objectives."""

    ANIMAL_FARM = "animal_farm"
    CROP_FARM = "crop_farm"
    WOOD_FARM = "wood_farm"
    IRON_MINE = "iron_mine"
    FOOD_RECOVERY = "food_recovery"
    FOOD_PRODUCTION = "food_production"
    END_SUPPLY = "end_supply"
    NETHER_SUPPLY = "nether_supply"
    ENCHANTING_XP = "enchanting_xp"
    DIMENSION_ENTRY = "dimension_entry"
    ENCHANTING_MATERIAL = "enchanting_material"
    END_FRONTIER = "end_frontier"
    END_CITY_ROUTE = "end_city_route"
    STORAGE_MAINTENANCE = "storage_maintenance"


@dataclass(frozen=True)
class LocalOpportunity:
    """One bounded resource action selected from live signals."""

    kind: OpportunityKind
    score: float
    reason: str
    animal_type: str = ""
    location: Optional[Tuple[int, int, int]] = None
    target_item: str = ""
    assigned_role: str = ""


__all__ = ["LocalOpportunity", "OpportunityKind"]
