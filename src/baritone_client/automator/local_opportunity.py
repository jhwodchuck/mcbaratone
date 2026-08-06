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


@dataclass(frozen=True)
class LocalOpportunity:
    """One bounded resource action selected from live signals."""

    kind: OpportunityKind
    score: float
    reason: str
    animal_type: str = ""
    location: Optional[Tuple[int, int, int]] = None


__all__ = ["LocalOpportunity", "OpportunityKind"]
