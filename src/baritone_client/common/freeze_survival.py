"""Shared freeze-state checks and bounded freezing prevention.

Powder snow silently drains health outside any combat or drowning system.
``armor_upkeep.select_armor_opportunity`` already offers a leather-boot swap
as a scheduled opportunity between other tasks, but that only runs at
objective-selection boundaries -- a bot that walks into a freezing biome
mid-task, already carrying leather boots, would not equip them until the
next opportunity cycle, which can be minutes away. Live A1 2026-09-07: a bot
froze to death in a grove biome (``last_damage_source: freeze``) with no
"freezing biome" log line anywhere in the run-up, meaning the opportunity
check never got a turn before it died. ``survival_tick`` is the shared
reflex every long-running wait loop already polls each tick for drowning;
wiring the same swap in here closes the same gap for freezing.
"""

from __future__ import annotations

from typing import Any


def guard_against_freezing(client: Any, state: dict) -> bool:
    """Equip carried leather boots the instant a freezing biome is entered."""
    from ..automator.armor_upkeep import (
        FREEZING_BIOMES,
        equip_freeze_boots,
        wearing_freeze_boots,
    )

    if str(state.get("biome") or "").lower() not in FREEZING_BIOMES:
        return False
    if wearing_freeze_boots(client):
        return False
    return bool(equip_freeze_boots(client))
