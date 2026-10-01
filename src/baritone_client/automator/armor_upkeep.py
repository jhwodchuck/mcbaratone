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

from ..common.inventory import craft
from .local_opportunity import LocalOpportunity, OpportunityKind

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

#: The only biomes that generate powder snow, so the only ones that can freeze
#: a player to death.
FREEZING_BIOMES = frozenset(
    {
        "minecraft:grove",
        "minecraft:snowy_slopes",
        "minecraft:jagged_peaks",
        "minecraft:frozen_peaks",
    }
)
#: Leather boots are the *only* boots that stop a player sinking into powder
#: snow and taking freeze damage; iron boots do not help at all. That is why
#: this is separate from ARMOR_PLAN rather than another entry in it -- here the
#: nominally weaker material is the strictly correct one, and it is bought with
#: leather rather than iron.
FREEZE_BOOTS = "minecraft:leather_boots"
#: Vanilla recipe cost, reported in telemetry when the craft cannot proceed.
FREEZE_BOOTS_LEATHER = 4


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
    """Report ingots plus raw iron as the bot's eventual iron reserve."""
    return _count(client, "minecraft:iron_ingot") + _count(client, "minecraft:raw_iron")


def spendable_iron(client: Any) -> int:
    """Return ingots immediately usable by armour recipes.

    Raw iron belongs to the smelting phase.  Treating it as recipe-ready made
    armour upkeep repeatedly call the generic crafter for pieces it could not
    afford, delaying the phase that would actually smelt the ore.
    """
    return _count(client, "minecraft:iron_ingot")


def missing_pieces(client: Any) -> list[Tuple[str, int]]:
    """Pieces the bot neither carries nor wears, so crafting is warranted.

    Affordability is applied later, against the run's iron budget.
    """
    return [
        (piece, cost)
        for piece, cost in ARMOR_PLAN
        if _count(client, piece) < 1
    ]


def worn_out_pieces(client: Any) -> list[Tuple[str, int]]:
    """Equipped pieces below the combat durability floor, worth replacing.

    `missing_pieces` only notices a piece once it has broken and vanished
    outright -- by then the bot already fought at least one encounter with
    that slot empty. Live A1 2026-09-07: a helmet at 153/165 damage and a
    chestplate at 194/240 sat unreplaced through a five-death spiral because
    nothing outside `nether_prep`'s own Nether-readiness gate ever asked "is
    this piece about to break", only "is it already gone". `equip_best_armor`
    already swaps in a fresher same-tier piece once one is crafted -- this
    only has to make sure crafting one is offered in the first place.
    """
    from ..common.inventory import armor_piece_is_durable, get_equipped_armor

    try:
        equipped = get_equipped_armor(client)
    except Exception:
        return []
    return [
        (piece, cost)
        for piece, cost in ARMOR_PLAN
        if (item_id := equipped.get(piece.rsplit("_", 1)[-1]))
        and not armor_piece_is_durable(client, item_id)
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

    A spare of a piece already worn only counts when it is fresher than the
    worn one: live A1 2026-09-28 wore iron boots with 43 uses left and carried
    a second pair with 26, and was offered this work every cooldown with
    nothing it could improve.
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
        and (piece not in worn or _spare_is_fresher(client, piece))
    ]


def _spare_is_fresher(client: Any, piece: str) -> bool:
    """Whether a carried copy of ``piece`` outlasts the one being worn."""
    from ..common.combat_loadout import remaining_durability
    from ..common.inventory import get_equipped_armor_details

    def left(item: Any) -> float:
        value = remaining_durability(item) if isinstance(item, dict) else None
        return float("inf") if value is None else float(value)

    try:
        response = client.transport.dispatch("get_inventory", {})
        data = response.get("data", response)
        carried = [i for i in data.get("inventory", []) if i.get("id") == piece]
        worn = [i for i in get_equipped_armor_details(client).values() if i.get("id") == piece]
    except Exception:
        return False
    if not carried or not worn:
        return bool(carried)
    return max(map(left, carried)) > min(map(left, worn))


def in_freezing_biome(client: Any) -> bool:
    """True where powder snow generates, so freezing is possible."""
    try:
        state = client.transport.dispatch("get_state", {})
    except Exception:
        return False
    if not isinstance(state, dict):
        return False
    return str(state.get("biome") or "").lower() in FREEZING_BIOMES


def wearing_freeze_boots(client: Any) -> bool:
    from ..common.inventory import get_equipped_armor

    try:
        return get_equipped_armor(client).get("boots") == FREEZE_BOOTS
    except Exception:
        return False


def needs_freeze_boots(client: Any) -> bool:
    """True when cold, not mobs, is the thing most likely to kill this bot.

    dragon-a froze to death 17 times across 67 deaths in a grove whose
    immediate surroundings measured ~24% powder snow, while dragon-b -- same
    build, ordinary biome -- froze zero times. `equip_best_armor` ranks armour
    by protection with `minimum_rank=iron`, so it will never put leather on and
    will happily swap iron boots back over them. Freeze protection therefore
    has to be asserted separately from the iron plan, and it applies even to a
    bot already wearing a "complete" 4/4 iron set.
    """
    return in_freezing_biome(client) and not wearing_freeze_boots(client)


def _inventory_slot_of(client: Any, item_id: str) -> Optional[int]:
    """Locate a carried item's raw inventory slot, or None."""
    try:
        response = client.transport.dispatch("get_inventory", {})
    except Exception:
        return None
    data = response.get("data", response) if isinstance(response, dict) else {}
    for item in data.get("inventory", []) or []:
        if item.get("id") != item_id or int(item.get("count", 0) or 0) <= 0:
            continue
        try:
            return int(item["slot"])
        except (KeyError, TypeError, ValueError):
            return None
    return None


def equip_freeze_boots(client: Any) -> bool:
    """Force leather boots on, past equip_best_armor's iron rank floor."""
    import time

    # Import the protocol slot rather than restating 8: a second copy of a
    # wire constant is exactly how two halves drift apart.
    from ..common.inventory import (
        _PLAYER_ARMOR_CONTAINER_SLOTS,
        get_equipped_armor,
    )

    if _inventory_slot_of(client, FREEZE_BOOTS) is None:
        return False
    try:
        if get_equipped_armor(client).get("boots"):
            client.transport.dispatch(
                "inventory_click",
                {
                    "slot": _PLAYER_ARMOR_CONTAINER_SLOTS["boots"],
                    "type": "QUICK_MOVE",
                    "button": 0,
                },
            )
            time.sleep(0.2)
        # Re-read: unequipping the old boots moves everything that follows.
        slot = _inventory_slot_of(client, FREEZE_BOOTS)
        if slot is None:
            return False
        client.transport.dispatch(
            "inventory_click",
            {
                "slot": 36 + slot if 0 <= slot <= 8 else slot,
                "type": "QUICK_MOVE",
                "button": 0,
            },
        )
    except Exception:
        return False
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if wearing_freeze_boots(client):
            return True
        time.sleep(0.1)
    return False


def needs_armor(client: Any) -> bool:
    """True when a cheap, high-value equip or durability replacement is
    available right now.

    A worn-out piece is checked ahead of the "already 4/4" exit below: it is
    still equipped, so that count alone hides exactly the case that needs
    the most urgent replacement.
    """
    if worn_out_pieces(client) and spendable_iron(client) >= MIN_IRON_TO_EQUIP:
        return True
    if equipped_pieces(client) >= TARGET_ARMOR_PIECES:
        return False
    if not missing_pieces(client):
        return False  # carried but unequipped: equipping alone will fix it
    return spendable_iron(client) >= MIN_IRON_TO_EQUIP


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


def select_armor_opportunity(
    client: Any, signals: Any, cooldown_ready: bool, state: Any = None
):
    """Offer one bounded armour upkeep action, if it is worth taking."""

    if not cooldown_ready or not armor_work_allowed(signals):
        return None
    worn = equipped_pieces(client)
    # An unarmed bot cannot win even the fight armour lets it survive, and a
    # stone sword is one craft from cobblestone it almost always carries.
    from .weapon_upkeep import kit_gaps

    if gaps := kit_gaps(client):
        names = ", ".join(g.split(":")[1] for g in gaps)
        return LocalOpportunity(OpportunityKind.ARMOR_UPKEEP, 195, f"no usable weapon/pickaxe; can craft {names}")
    # Iron itself: armour, tools and the Nether all need it, and a bot that has
    # lost its kit has no other way to get it back.
    from .iron_stockpile import LOW_STOCK, iron_stock, supply_due

    if supply_due(client, state, signals):
        return LocalOpportunity(
            OpportunityKind.ARMOR_UPKEEP,
            185,
            f"iron stock {iron_stock(client, state)} is below {LOW_STOCK}; a bounded mining trip can refill it",
        )
    # Freezing outranks the iron plan and ignores the "already dressed" exit:
    # a 4/4 iron set is no protection at all against powder snow, so a fully
    # armoured bot in a grove still needs this.
    if needs_freeze_boots(client):
        # Only offer freeze boot work if we have leather or already carry the boots.
        # Otherwise, leather gathering (signaled by defense.assess_armor_for_environment)
        # should run first. Offering armor_upkeep without leather just burns retries.
        if _count(client, "minecraft:leather") < FREEZE_BOOTS_LEATHER and _count(client, FREEZE_BOOTS) < 1:
            return None
        return LocalOpportunity(
            OpportunityKind.ARMOR_UPKEEP,
            200,
            f"freezing biome without leather boots ({worn}/4 armour worn)",
        )
    # A worn-out piece also outranks the "already dressed" exit below: a
    # helmet at 153/165 damage is still counted in `worn`, so that count
    # alone would hide the one piece about to break outright.
    worn_out = worn_out_pieces(client)
    if worn_out and spendable_iron(client) >= MIN_IRON_TO_EQUIP:
        return LocalOpportunity(
            OpportunityKind.ARMOR_UPKEEP,
            180,
            f"{len(worn_out)} armour piece(s) nearly broken "
            f"with {carried_iron(client)} iron carried",
        )
    if worn >= TARGET_ARMOR_PIECES:
        return None
    # Carrying an unworn piece is worth a tick even with no iron at all:
    # equipping it is free, and grave recovery routinely leaves armour in
    # ordinary inventory slots. Anything else needs iron we can actually spend,
    # otherwise this offers work it has no way to complete.
    if not unworn_carried_pieces(client) and not needs_armor(client):
        # Nothing carried to wear or craft: offer recovery (storage, smelting,
        # bounded mining) when one of its stages has something to try.
        from .armor_recovery import recovery_ready

        if state is None or not recovery_ready(client, state):
            return None
    # An unarmoured bot with iron is the single cheapest survival win
    # available, so it outscores routine farming without displacing recovery.
    return LocalOpportunity(
        OpportunityKind.ARMOR_UPKEEP,
        170 + (TARGET_ARMOR_PIECES - worn) * 10,
        f"{worn}/4 armour worn with {carried_iron(client)} iron carried",
    )


def run_armor_upkeep(client: Any, state: Any) -> Tuple[bool, str, int, int]:
    """Restore a usable weapon and pickaxe, then do the armour work below."""
    from .weapon_upkeep import restore_kit

    made = restore_kit(client)
    from .iron_stockpile import run_supply_trip, supply_due

    if supply_due(client, state):
        success, detail, before, after = run_supply_trip(client, state)
        return success, (f"crafted {', '.join(made)}; {detail}" if made else detail), before, after
    success, detail, before, after = _run_armor(client, state)
    if made:
        return True, f"crafted {', '.join(made)}; {detail}", before, after
    return success, detail, before, after


def _run_armor(client: Any, state: Any) -> Tuple[bool, str, int, int]:
    """Equip what is carried, then craft what one set still needs.

    Returns the scheduler's (success, detail, before, after) contract. Success
    requires the worn count to actually rise: crafting that changes nothing is
    not progress, and must not reset a no-progress streak.
    """
    from ..common.inventory import equip_best_armor
    from ..common.tasks import PlayerDeathDetected, SurvivalRecoveryRequired

    before = equipped_pieces(client)

    # Free first: grave recovery and looting leave armour in ordinary slots.
    try:
        equip_best_armor(client)
    except (PlayerDeathDetected, SurvivalRecoveryRequired):
        raise
    except Exception:
        pass

    # Cold before mobs. This runs *after* equip_best_armor on purpose: that
    # call ranks by protection and will have just put iron boots back on, so
    # the leather has to be re-asserted over the top of its choice. Swapping
    # boots leaves the worn count unchanged, which is why this reports success
    # on its own terms rather than through the before/after delta.
    if needs_freeze_boots(client):
        if (_count(client, FREEZE_BOOTS) < 1
                and _count(client, "minecraft:leather") >= FREEZE_BOOTS_LEATHER):
            try:
                craft(client, FREEZE_BOOTS, 1)
            except (PlayerDeathDetected, SurvivalRecoveryRequired):
                raise
            except Exception:
                pass
        if equip_freeze_boots(client):
            after = equipped_pieces(client)
            return (
                True,
                f"equipped leather boots against freezing ({before}->{after})",
                before,
                after,
            )
        if _count(client, FREEZE_BOOTS) < 1:
            after = equipped_pieces(client)
            return (
                False,
                "freezing biome but no leather boots; "
                f"needs {FREEZE_BOOTS_LEATHER} leather",
                before,
                after,
            )
    worn_out_before = worn_out_pieces(client)
    if equipped_pieces(client) >= TARGET_ARMOR_PIECES and not worn_out_before:
        after = equipped_pieces(client)
        return True, f"equipped carried armour ({before}->{after})", before, after

    # Nothing carried to finish the set: fetch armour or iron from home
    # storage, smelt raw iron, and as a bounded last step mine some.
    from .armor_recovery import recover_armor_materials

    recovered = recover_armor_materials(client, state)
    if recovered:
        try:
            equip_best_armor(client)
        except (PlayerDeathDetected, SurvivalRecoveryRequired):
            raise
        except Exception:
            pass

    # Armour recipes consume ingots, not raw ore.  Leave raw-iron conversion
    # to FOOD_AND_IRON instead of burning a timeout on unaffordable recipes.
    # A worn-out piece is a craft target even at 4/4 equipped -- the count
    # alone hides the one about to break outright, and equip_best_armor
    # already swaps in a fresher same-tier piece once one is crafted.
    budget = min(spendable_iron(client), FULL_SET_IRON)
    targets = list(missing_pieces(client))
    targets += [item for item in worn_out_before if item not in targets]
    crafted = []
    for piece, cost in targets:
        if budget < cost:
            continue
        try:
            success = craft(client, piece, 1)
        except (PlayerDeathDetected, SurvivalRecoveryRequired):
            # These are control flow, not errors: they mean "stop and let the
            # top level recover". Swallowing them left Bot15 crafting while
            # dead -- it walked to an unreachable table at y=70 and tried to
            # build a crafting table, all after the harness logged
            # "Move aborted: player is dead".
            raise
        except Exception:
            continue
        if success:
            # A successful craft may not immediately show up in the
            # inventory count (the item could go straight into an armour
            # slot). We still attempt to equip, then rely on the after
            # count to detect real progress.
            crafted.append(piece.split(":")[1])
            budget -= cost
            equip_best_armor(client)

    after = equipped_pieces(client)
    # A durability swap leaves the equipped count unchanged -- one worn iron
    # helmet out, one fresh iron helmet in -- so the count alone would report
    # this as a no-op and reset a caller's no-progress streak on real work.
    replaced_worn_out = len(worn_out_pieces(client)) < len(worn_out_before)
    note = f"; {recovered}" if recovered else ""
    if after > before or replaced_worn_out:
        made = ", ".join(crafted) if crafted else "carried pieces"
        return True, f"armour {before}->{after} ({made}){note}", before, after
    return (
        False,
        f"armour unchanged at {before}/4 with {carried_iron(client)} iron carried{note}",
        before,
        after,
    )


__all__ = [
    "ARMOR_MIN_HEALTH",
    "ARMOR_PLAN",
    "FREEZE_BOOTS",
    "FREEZE_BOOTS_LEATHER",
    "FREEZING_BIOMES",
    "FULL_SET_IRON",
    "MIN_IRON_TO_EQUIP",
    "TARGET_ARMOR_PIECES",
    "OpportunityKind",
    "armor_work_allowed",
    "carried_iron",
    "equip_freeze_boots",
    "equipped_pieces",
    "in_freezing_biome",
    "needs_freeze_boots",
    "wearing_freeze_boots",
    "missing_pieces",
    "needs_armor",
    "spendable_iron",
    "unworn_carried_pieces",
    "run_armor_upkeep",
    "select_armor_opportunity",
]
