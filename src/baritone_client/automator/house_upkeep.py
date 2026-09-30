"""Finish or repair the starter house any time it is left incomplete.

BASE_CONSTRUCTION's own repair loop only runs while that phase is active --
`execute()` short-circuits the instant its objective is satisfied, so a house
accepted in a degraded state (or damaged later) is never revisited. Confirmed
live on A1 2026-08-14: the starter house sat at 68/70 walls, 39/49 roof, and
no door at all for days after BASE_CONSTRUCTION completed, with nothing left
checking on it. `build_good_house` is already idempotent -- it surveys the
world and only places what is missing -- so this just needs to call it again
periodically, the same way `armor_upkeep` re-checks armour in every phase.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Optional, Tuple

#: Health floor for structural work. Below the comfort gate's 16 (this is not
#: combat-adjacent survival work like armour), but well above the emergency
#: threshold: a bot should not detour to place cobblestone while critical.
HOUSE_UPKEEP_MIN_HEALTH = 12.0
HOUSE_STRUCTURE_RECHECK_INTERVAL = 1800.0


def _house_origin(state: Any) -> Optional[Tuple[int, int, int]]:
    """Read the starter house's origin recorded by BASE_CONSTRUCTION."""
    custom = getattr(state, "custom_data", {}) or {}
    structures = custom.get("structures", {})
    if not isinstance(structures, Mapping):
        return None
    house = structures.get("starter_house", {})
    if not isinstance(house, Mapping):
        return None
    origin = house.get("origin")
    if not isinstance(origin, (list, tuple)) or len(origin) != 3:
        return None
    try:
        return (int(origin[0]), int(origin[1]), int(origin[2]))
    except (TypeError, ValueError):
        return None


def _house_repaired(state: Any) -> bool:
    """Trust completion briefly; later damage must receive a fresh survey."""
    custom = getattr(state, "custom_data", {}) or {}
    structures = custom.get("structures", {})
    if not isinstance(structures, Mapping):
        return False
    house = structures.get("starter_house", {})
    if not isinstance(house, Mapping) or not house.get("repaired"):
        return False
    try:
        age = time.time() - float(house["structure_checked_at"])
    except (KeyError, TypeError, ValueError):
        return False
    return 0 <= age < HOUSE_STRUCTURE_RECHECK_INTERVAL


def _mark_house_repaired(state: Any) -> None:
    structures = state.custom_data.setdefault("structures", {})
    house = structures.setdefault("starter_house", {})
    house["repaired"] = True
    house["structure_checked_at"] = time.time()


def house_upkeep_allowed(signals: Any) -> bool:
    """Structural work needs a genuinely calm moment, unlike armour upkeep.

    Placing shell/roof blocks takes the player outside the house's own
    perimeter defence for multiple slow bridge round-trips; a nearby hostile
    turns that into free damage. Armour upkeep intentionally ignores hostiles
    because it is the fix for taking hits -- house repair is not.
    """
    return (
        float(getattr(signals, "health", 0.0) or 0.0) >= HOUSE_UPKEEP_MIN_HEALTH
        and int(getattr(signals, "nearby_hostiles", 0) or 0) == 0
    )


def select_house_upkeep_opportunity(
    state: Any, signals: Any, cooldown_ready: bool
):
    """Offer one bounded house-repair pass, if the starter house needs one."""
    from .local_opportunity import LocalOpportunity, OpportunityKind

    from .base_lighting import lighting_allowed, lighting_due

    if not cooldown_ready:
        return None
    origin = _house_origin(state)
    if (_house_repaired(state) or origin is None) or not house_upkeep_allowed(signals):
        # The house stands (or cannot be repaired now): keep the base lit.
        # Lighting is the fix for hostiles, so it has its own gentler gate.
        if lighting_allowed(signals) and lighting_due(state):
            return LocalOpportunity(
                OpportunityKind.HOUSE_UPKEEP,
                150,
                "the base has dark spots where monsters can spawn",
                location=origin,
            )
        return None
    return LocalOpportunity(
        OpportunityKind.HOUSE_UPKEEP,
        110,
        "the starter house has a persisted origin and may need repair",
        location=origin,
    )


def run_house_upkeep(client: Any, state: Any) -> Tuple[bool, str, int, int]:
    """Survey the starter house, repair what's missing, and record success.

    Returns the scheduler's (success, detail, before, after) contract, where
    before/after are the world-verified block+door count out of 169
    (168 planned blocks plus the door).
    """
    from ..common import base as house_utils
    from ..common.navigation import goto

    origin = _house_origin(state)
    if origin is None or _house_repaired(state):
        from .base_lighting import LIGHTING_KEY, light_base, lighting_zone

        if lighting_zone(state) is None:
            return False, "no persisted house origin", 0, 0

        record = state.custom_data.get(LIGHTING_KEY, {}) if isinstance(state.custom_data, dict) else {}
        before = int(record.get("placed_total", 0) or 0) if isinstance(record, dict) else 0
        placed, _remaining, detail = light_base(client, state)
        return placed > 0, detail, before, before + placed
    x, y, z = origin

    def _survey() -> Tuple[int, dict]:
        progress = house_utils.summarize_house_progress(client, x, y, z)
        total = (
            progress["floor"] + progress["shell"] + progress["roof"]
            + int(progress["door_present"])
        )
        return total, progress

    remote_before, _ = _survey()
    if not goto(client, x + 3, y + 1, z - 2, timeout=60, tolerance=4.0):
        return (
            False,
            "could not reach the starter house to repair it",
            remote_before,
            remote_before,
        )

    # Re-survey now that the chunk is certainly loaded. Measured from across
    # the map every block reads void_air, so the remote survey scored 0 of 169
    # and the closing `after > before` test then reported a successful repair
    # for a house that was already standing and had not been touched.
    before, _ = _survey()
    complete = house_utils.build_good_house(client, x, y, z)
    after, progress = _survey()
    if complete:
        _mark_house_repaired(state)

    detail = (
        f"starter house floor={progress['floor']}/{progress['floor_total']} "
        f"walls={progress['shell']}/{progress['shell_total']} "
        f"roof={progress['roof']}/{progress['roof_total']} "
        f"door={'yes' if progress['door_present'] else 'no'}"
    )
    if after > before:
        return True, detail, before, after
    return False, detail, before, after


__all__ = [
    "HOUSE_UPKEEP_MIN_HEALTH",
    "house_upkeep_allowed",
    "run_house_upkeep",
    "select_house_upkeep_opportunity",
]
