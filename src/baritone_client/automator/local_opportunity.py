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
    CLEAR_HOSTILES_AID = "clear_hostiles_aid"


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
    aid_request_id: str = ""


def local_work_blockers(signals: object) -> Tuple[str, ...]:
    """Name every condition preventing local side work, with its value.

    A bare boolean made a hold unexplainable: the fleet reported "waiting for
    recurring food production" for hours while a worker stood at 20 health and
    19 food, and finding the cause took a session of live RCON probing. Report
    which condition failed so the log answers the question by itself.
    """
    blockers = []
    if not getattr(signals, "observed", False):
        blockers.append("no state snapshot")
    if not getattr(signals, "entities_observed", False):
        blockers.append("no entity snapshot")
    dimension = getattr(signals, "dimension", "") or ""
    if "overworld" not in dimension:
        blockers.append(f"dimension={dimension or 'unknown'}")
    health = float(getattr(signals, "health", 0.0) or 0.0)
    if health < 16.0:
        blockers.append(f"health {health:.1f}<16")
    food = int(getattr(signals, "food", 0) or 0)
    if food < 14:
        blockers.append(f"food {food}<14")
    hostiles = int(getattr(signals, "nearby_hostiles", 0) or 0)
    if hostiles:
        blockers.append(f"{hostiles} hostile(s) near")
    world_time = int(getattr(signals, "world_time", 0) or 0)
    if world_time % 24000 >= 12000:
        blockers.append(f"night (t={world_time % 24000})")
    return tuple(blockers)


def local_work_hold_reason(signals: object) -> str:
    """Return one log-ready phrase explaining a hold, blocked or not.

    The two cases need distinguishing: a safety gate refused, or the gate
    passed and opportunity selection simply produced nothing. They point at
    different code, and a hold that says neither costs hours to diagnose.
    """
    blockers = local_work_blockers(signals)
    if blockers:
        return f"local work blocked: {', '.join(blockers)}"
    return "local work permitted but no opportunity selected"


__all__ = [
    "LocalOpportunity",
    "OpportunityKind",
    "local_work_blockers",
    "local_work_hold_reason",
]
