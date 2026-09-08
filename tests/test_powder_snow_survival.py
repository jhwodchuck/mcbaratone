"""Powder snow is a floor that isn't, and only leather boots survive it.

dragon-a froze to death 17 times across 67 deaths in a `minecraft:grove`
whose immediate surroundings measured 58/243 powder snow, while dragon-b --
same build, ordinary biome -- froze zero times.  `powder_snow` appeared
nowhere in src/, so every ground check accepted it as solid footing and the
armour planner had no reason to prefer the one material that helps.
"""

from __future__ import annotations

from types import SimpleNamespace

from baritone_client.automator import armor_upkeep
from baritone_client.common.escape_recovery import destination_safe


def _terrain_client(blocks: dict[tuple[int, int, int], str]):
    def dispatch(route, payload):
        if route != "get_block":
            raise AssertionError(f"unexpected route {route}")
        key = (payload["x"], payload["y"], payload["z"])
        return {"id": blocks.get(key, "minecraft:stone")}

    return SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))


def test_powder_snow_is_not_a_floor_a_flee_endpoint_may_stand_on():
    """The support layer is the gap: feet-level was already rejected."""
    client = _terrain_client(
        {
            (10, 70, 10): "minecraft:air",  # feet
            (10, 71, 10): "minecraft:air",  # head
            (10, 69, 10): "minecraft:powder_snow",  # "floor"
        }
    )
    assert not destination_safe(client, 10, 70, 10)


def test_ordinary_snow_still_supports_a_flee_endpoint():
    """snow_block is genuinely solid; only powder_snow is the trap."""
    client = _terrain_client(
        {
            (10, 70, 10): "minecraft:air",
            (10, 71, 10): "minecraft:air",
            (10, 69, 10): "minecraft:snow_block",
        }
    )
    assert destination_safe(client, 10, 70, 10)


def _armour_client(
    biome: str,
    boots: str | None,
    *,
    leather: int = 0,
):
    armor = [{"id": boots, "count": 1}] if boots else []
    inventory = (
        [{"id": "minecraft:leather", "count": leather}]
        if leather > 0
        else []
    )

    def dispatch(route, _payload):
        if route == "get_state":
            return {"biome": biome, "health": 20.0}
        if route == "get_inventory":
            return {"armor": armor, "inventory": inventory}
        raise AssertionError(f"unexpected route {route}")

    return SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))


def test_a_grove_bot_in_full_iron_still_needs_leather_boots():
    """A complete iron set is zero protection against freezing."""
    client = _armour_client("minecraft:grove", "minecraft:iron_boots")
    assert armor_upkeep.in_freezing_biome(client)
    assert armor_upkeep.needs_freeze_boots(client)


def test_leather_boots_satisfy_the_freezing_requirement():
    client = _armour_client("minecraft:grove", "minecraft:leather_boots")
    assert armor_upkeep.wearing_freeze_boots(client)
    assert not armor_upkeep.needs_freeze_boots(client)


def test_an_ordinary_biome_does_not_ask_for_leather_boots():
    """Elsewhere leather really is a downgrade; do not force it."""
    client = _armour_client("minecraft:plains", "minecraft:iron_boots")
    assert not armor_upkeep.in_freezing_biome(client)
    assert not armor_upkeep.needs_freeze_boots(client)


def test_freezing_outranks_the_already_dressed_exit():
    """4/4 iron normally ends the opportunity; freezing must override that.

    To make a freeze-boot upgrade we need enough leather to craft them.  Pass
    that leather into the fixture so the game logic sees a viable work item.
    """
    client = _armour_client(
        "minecraft:grove",
        "minecraft:iron_boots",
        leather=armor_upkeep.FREEZE_BOOTS_LEATHER,
    )
    signals = SimpleNamespace(health=20.0)

    opportunity = armor_upkeep.select_armor_opportunity(
        client, signals, cooldown_ready=True
    )

    assert opportunity is not None
    assert "freezing biome" in opportunity.reason
