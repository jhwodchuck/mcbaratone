"""Wear the armour you are already carrying, in any phase.

Armour provisioning was built as a Nether-entry prerequisite, so it only ran
in `NETHER_AND_BLAZE`, and later `WORLD_UNLOCK` and `ENCHANTING_PIPELINE`. No
path in `BOOT_SEQUENCE` or `VILLAGER_INFRA` ever says "you have iron, put some
on". The result on 2026-08-06: Bot15 stood in `BOOT_SEQUENCE` carrying 94 iron
ingots and 19 raw iron -- four full sets' worth -- wearing nothing, while the
fleet took 271 deaths in a day, 143 of them to ordinary zombies.

That is not a combat-code failure. The defence system evaluates threats, tries
to flee, and fights when cornered; it simply cannot save a bot taking full
damage. Armour is a survival concern, not an expedition prerequisite.

Two bounds matter. Self-equipping is capped at one set, because a bot's carried
iron is notionally the fleet's supply for tools and rails and a single worker
must not consume the reserve. And it only runs when local work is already
considered safe -- crafting needs a table, and a bot at 1.5 health in the
middle of a siege should be escaping, not tailoring.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence, Tuple

#: Iron cost of each piece, cheapest first. Boots and helmet come first on
#: purpose: three cheap pieces beat one expensive one for survival, and the
#: defence runtime already treats armour count as its threshold.
ARMOR_PLAN: Tuple[Tuple[str, int], ...] = (
    ("minecraft:iron_boots", 4),
    ("minecraft:iron_helmet", 5),
    ("minecraft:iron_leggings", 7),
    ("minecraft:iron_chestplate", 8),
)
#: One full set. A worker may dress itself; it may not drain the fleet.
FULL_SET_IRON = sum(cost for _piece, cost in ARMOR_PLAN)
#: Enough to be worth a detour. Below this, mining is the better use of a tick.
MIN_IRON_TO_EQUIP = 4
#: The defence runtime's own comfort threshold.
TARGET_ARMOR_PIECES = 4


def _count(client: Any, item_id: str) -> int:
    from ..common.inventory import count_item

    try:
        return max(0, int(count_item(client, item_id) or 0))
    except Exception:
        return 0


def equipped_pieces(client: Any) -> int:
    """How many armour slots are filled right now."""
    from ..common.inventory import get_equipped_armor

    try:
        return len(get_equipped_armor(client))
    except Exception:
        return TARGET_ARMOR_PIECES  # unknown: assume dressed, never loop on it


def carried_iron(client: Any) -> int:
    """Ingots plus raw iron, since raw smelts into ingots."""
    return _count(client, "minecraft:iron_ingot") + _count(client, "minecraft:raw_iron")


def missing_pieces(client: Any) -> list[Tuple[str, int]]:
    """Armour this bot can afford and is not already carrying."""
    return [
        (piece, cost)
        for piece, cost in ARMOR_PLAN
        if _count(client, piece) < 1
    ]


def needs_armor(client: Any) -> bool:
    """True when a cheap, high-value equip is available right now."""
    if equipped_pieces(client) >= TARGET_ARMOR_PIECES:
        return False
    if not missing_pieces(client):
        return False  # carried but unequipped: equipping alone will fix it
    return carried_iron(client) >= MIN_IRON_TO_EQUIP


def select_armor_opportunity(client: Any, signals: Any, cooldown_ready: bool):
    """Offer one bounded armour upkeep action, if it is worth taking."""
    from .local_opportunity import LocalOpportunity, OpportunityKind

    if not cooldown_ready or not getattr(signals, "safe_for_local_work", False):
        return None
    worn = equipped_pieces(client)
    if worn >= TARGET_ARMOR_PIECES:
        return None
    # Carrying an unworn piece is worth a tick even with no iron at all:
    # equipping it is free, and grave recovery routinely leaves armour in
    # ordinary inventory slots.
    carrying_unworn = any(_count(client, piece) > 0 for piece, _cost in ARMOR_PLAN)
    if not carrying_unworn and not needs_armor(client):
        return None
    # An unarmoured bot with iron is the single cheapest survival win
    # available, so it outscores routine farming without displacing recovery.
    return LocalOpportunity(
        OpportunityKind.ARMOR_UPKEEP,
        170 + (TARGET_ARMOR_PIECES - worn) * 10,
        f"{worn}/4 armour worn with {carried_iron(client)} iron carried",
    )


def run_armor_upkeep(client: Any, state: Any) -> Tuple[bool, str, int, int]:
    """Equip what is carried, then craft what one set still needs.

    Returns the scheduler's (success, detail, before, after) contract. Success
    requires the worn count to actually rise: crafting that changes nothing is
    not progress, and must not reset a no-progress streak.
    """
    from ..common.inventory import equip_best_armor
    from ..common.resources import ensure_supplies

    before = equipped_pieces(client)

    # Free first: grave recovery and looting leave armour in ordinary slots.
    try:
        equip_best_armor(client)
    except Exception:
        pass
    if equipped_pieces(client) >= TARGET_ARMOR_PIECES:
        after = equipped_pieces(client)
        return True, f"equipped carried armour ({before}->{after})", before, after

    budget = min(carried_iron(client), FULL_SET_IRON)
    crafted = []
    for piece, cost in missing_pieces(client):
        if budget < cost:
            continue
        try:
            if ensure_supplies(client, {piece: 1}, timeout=120).success:
                crafted.append(piece.split(":")[1])
                budget -= cost
                equip_best_armor(client)
        except Exception:
            continue

    after = equipped_pieces(client)
    if after > before:
        made = ", ".join(crafted) if crafted else "carried pieces"
        return True, f"armour {before}->{after} ({made})", before, after
    return (
        False,
        f"armour unchanged at {before}/4 with {carried_iron(client)} iron carried",
        before,
        after,
    )


__all__ = [
    "ARMOR_PLAN",
    "FULL_SET_IRON",
    "MIN_IRON_TO_EQUIP",
    "TARGET_ARMOR_PIECES",
    "carried_iron",
    "equipped_pieces",
    "missing_pieces",
    "needs_armor",
    "run_armor_upkeep",
    "select_armor_opportunity",
]
