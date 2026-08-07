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

Self-equipping is capped at one set, because a bot's carried iron is notionally
the fleet's supply for tools and rails and a single worker must not consume the
reserve.

The safety gate is deliberately *not* the scheduler's `safe_for_local_work` --
see `armor_work_allowed` for why that choice made the first version of this
module a no-op on every bot it was written for.
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
#: Health floor for armour work. Above the emergency threshold, so a dying bot
#: still flees and eats first -- but deliberately far below the comfort gate's
#: 16, because a bot under fire is exactly the one that needs armour.
ARMOR_MIN_HEALTH = 8.0


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
    """Pieces the bot neither carries nor wears, so crafting is warranted.

    Affordability is applied later, against the run's iron budget.
    """
    return [
        (piece, cost)
        for piece, cost in ARMOR_PLAN
        if _count(client, piece) < 1
    ]


def unworn_carried_pieces(client: Any) -> list[str]:
    """Armour sitting in ordinary slots that is not already being worn.

    `get_inventory` folds the bridge's `armor` section into its counts, so
    `count_item("minecraft:iron_helmet")` returns 1 for a helmet that is *on
    the bot's head*. Testing `count > 0` therefore means "carried or worn",
    which is why Bot16 offered this opportunity every cooldown for an hour
    while holding 2 iron and having nothing it could do -- seven identical
    "armour unchanged at 1/4 with 2 iron carried" runs. A piece only counts as
    unworn when there is one more of it than the bot is wearing.
    """
    from ..common.inventory import get_equipped_armor

    try:
        worn = set(get_equipped_armor(client).values())
    except Exception:
        worn = set()
    return [
        piece
        for piece, _cost in ARMOR_PLAN
        if _count(client, piece) > (1 if piece in worn else 0)
    ]


def needs_armor(client: Any) -> bool:
    """True when a cheap, high-value equip is available right now."""
    if equipped_pieces(client) >= TARGET_ARMOR_PIECES:
        return False
    if not missing_pieces(client):
        return False  # carried but unequipped: equipping alone will fix it
    return carried_iron(client) >= MIN_IRON_TO_EQUIP


def armor_work_allowed(signals: Any) -> bool:
    """Armour uses its own gate, deliberately not `safe_for_local_work`.

    The comfort gate demands health >= 16, food >= 14, daylight and *zero*
    nearby hostiles. Measured across the fleet, the blocker is almost always
    "1-2 hostile(s) near", sometimes with "health 14.0<16" -- which are the
    symptoms of having no armour. Gating armour on that is circular: a bot
    must be safe before it may become safe, so at night the opportunity never
    fires at all. That circularity is what the first version of this module
    shipped with, and it made the fix a no-op.

    A bot under fire is taking those hits either way. Spending a short crafting
    detour to cut all future damage is right even mid-fight; waiting for calm
    is what produced 143 zombie deaths in a day. So the only gate is a health
    floor above the emergency threshold: below it, fleeing and eating come
    first and armour can wait.
    """
    return float(getattr(signals, "health", 0.0) or 0.0) >= ARMOR_MIN_HEALTH


def select_armor_opportunity(client: Any, signals: Any, cooldown_ready: bool):
    """Offer one bounded armour upkeep action, if it is worth taking."""
    from .local_opportunity import LocalOpportunity, OpportunityKind

    if not cooldown_ready or not armor_work_allowed(signals):
        return None
    worn = equipped_pieces(client)
    if worn >= TARGET_ARMOR_PIECES:
        return None
    # Carrying an unworn piece is worth a tick even with no iron at all:
    # equipping it is free, and grave recovery routinely leaves armour in
    # ordinary inventory slots. Anything else needs iron we can actually spend,
    # otherwise this offers work it has no way to complete.
    if not unworn_carried_pieces(client) and not needs_armor(client):
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
    from ..common.tasks import PlayerDeathDetected, SurvivalRecoveryRequired

    before = equipped_pieces(client)

    # Free first: grave recovery and looting leave armour in ordinary slots.
    try:
        equip_best_armor(client)
    except (PlayerDeathDetected, SurvivalRecoveryRequired):
        raise
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
        except (PlayerDeathDetected, SurvivalRecoveryRequired):
            # These are control flow, not errors: they mean "stop and let the
            # top level recover". Swallowing them left Bot15 crafting while
            # dead -- it walked to an unreachable table at y=70 and tried to
            # build a crafting table, all after the harness logged
            # "Move aborted: player is dead".
            raise
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
    "ARMOR_MIN_HEALTH",
    "ARMOR_PLAN",
    "FULL_SET_IRON",
    "MIN_IRON_TO_EQUIP",
    "TARGET_ARMOR_PIECES",
    "armor_work_allowed",
    "carried_iron",
    "equipped_pieces",
    "missing_pieces",
    "needs_armor",
    "unworn_carried_pieces",
    "run_armor_upkeep",
    "select_armor_opportunity",
]
