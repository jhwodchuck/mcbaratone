"""A worn sword must be replaced, not counted as provisioned.

`weapon_score` rejects any blade with <=3 durability, so `equip_best_weapon`
(and therefore `_nether_loadout_ready`) refuses it. `_provision_iron_gear`
short-circuits on `count >= 1`, so the worn sword was reported as provisioned
and the gate failed forever. Live A1 spent a day retrying NETHER_AND_BLAZE
with an iron sword at exactly 3 durability of 250.
"""

from __future__ import annotations

from baritone_client.common.combat_loadout import choose_best_weapon, weapon_score


def _sword(remaining: int) -> dict:
    return {
        "id": "minecraft:iron_sword",
        "count": 1,
        "slot": 0,
        "damage": 250 - remaining,
        "max_damage": 250,
    }


def test_a_sword_at_the_rejection_boundary_is_not_usable():
    """A1's sword sat at exactly 3, the boundary weapon_score rejects."""
    assert weapon_score(_sword(3)) == float("-inf")
    assert choose_best_weapon([_sword(3)]) is None


def test_a_sword_just_above_the_boundary_is_still_usable():
    """Guard the other side: 4 must remain equippable, not over-rejected."""
    assert weapon_score(_sword(4)) != float("-inf")
    assert choose_best_weapon([_sword(4)]) is not None


def test_nether_prep_forces_replacement_when_the_carried_sword_is_unusable():
    """Provisioning must ask the gate's question, not just count the item."""
    import inspect

    from baritone_client.automator.phases import nether_prep

    source = inspect.getsource(nether_prep)
    sword_call = source.index('"minecraft:iron_sword", 2')
    window = source[max(0, sword_call - 600) : sword_call + 200]
    assert "force_replacement=replace_sword" in window, (
        "the sword must be replaceable like armor is; counting it is the bug"
    )
    assert "equip_best_weapon(client)" in window, (
        "replacement must be decided with the same predicate the gate uses"
    )
