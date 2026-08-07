"""A bot carrying iron must not stand around wearing nothing.

Armour provisioning was built as a Nether-entry prerequisite, so it only ran in
NETHER_AND_BLAZE, WORLD_UNLOCK and ENCHANTING_PIPELINE. Nothing in
BOOT_SEQUENCE or VILLAGER_INFRA ever said "you have iron, put some on".

On 2026-08-06 Bot15 stood in BOOT_SEQUENCE carrying 94 iron ingots and 19 raw
iron -- four full sets' worth -- wearing nothing, while the fleet took 271
deaths in a day, 143 of them to ordinary zombies. The defence system was
working; it simply cannot save a bot taking full damage.
"""

from types import SimpleNamespace

import pytest

from baritone_client.automator import armor_upkeep


class _Client:
    def __init__(self, iron=0, raw=0, worn=0, carried=()):
        self.iron, self.raw, self.worn = iron, raw, worn
        self.carried = set(carried)
        self.transport = SimpleNamespace(dispatch=lambda *_a, **_k: {})


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    import baritone_client.common.inventory as inv

    monkeypatch.setattr(
        inv, "count_item",
        lambda c, item: (
            c.iron if item == "minecraft:iron_ingot"
            else c.raw if item == "minecraft:raw_iron"
            else (1 if item in c.carried else 0)
        ),
    )
    monkeypatch.setattr(
        inv, "get_equipped_armor", lambda c: {f"slot{i}": "x" for i in range(c.worn)}
    )


def _signals(safe=True):
    return SimpleNamespace(safe_for_local_work=safe)


def test_bot15s_exact_state_produces_an_opportunity():
    """94 ingots, 19 raw iron, nothing worn -- the fleet's cheapest win."""
    opportunity = armor_upkeep.select_armor_opportunity(
        _Client(iron=94, raw=19, worn=0), _signals(), True
    )

    assert opportunity is not None
    assert "0/4" in opportunity.reason and "113 iron" in opportunity.reason


def test_a_fully_armoured_bot_is_never_offered_the_work():
    assert armor_upkeep.select_armor_opportunity(
        _Client(iron=94, worn=4), _signals(), True
    ) is None


def test_an_unsafe_bot_does_not_stop_to_get_dressed():
    """At 1.5 health mid-siege the right move is escaping, not tailoring."""
    assert armor_upkeep.select_armor_opportunity(
        _Client(iron=94, worn=0), _signals(safe=False), True
    ) is None


def test_a_bot_without_iron_is_not_offered_the_work():
    assert armor_upkeep.select_armor_opportunity(
        _Client(iron=0, raw=0, worn=0), _signals(), True
    ) is None


def test_carrying_an_unworn_piece_is_work_even_with_no_iron():
    """Grave recovery returns armour to ordinary slots; wearing it costs nothing."""
    opportunity = armor_upkeep.select_armor_opportunity(
        _Client(iron=0, raw=0, worn=0, carried=["minecraft:iron_chestplate"]),
        _signals(), True,
    )

    assert opportunity is not None


def test_armour_never_outranks_eating():
    """A starving bot must go for food first; armour does not help at food 0."""
    from baritone_client.automator.food_opportunity import (
        select_food_recovery_opportunity,
    )

    hungry = select_food_recovery_opportunity(0, True)
    naked = armor_upkeep.select_armor_opportunity(
        _Client(iron=94, worn=0), _signals(), True
    )

    assert naked.score < hungry.score, (naked.score, hungry.score)


def test_self_equipping_is_capped_at_one_set():
    """Carried iron is notionally the fleet's supply; one worker must not
    drain it dressing itself repeatedly."""
    assert armor_upkeep.FULL_SET_IRON == 24
    assert sum(cost for _piece, cost in armor_upkeep.ARMOR_PLAN) == 24


def test_cheap_pieces_come_first():
    """Three cheap pieces beat one expensive one: the defence runtime keys on
    the armour COUNT, not on total protection."""
    costs = [cost for _piece, cost in armor_upkeep.ARMOR_PLAN]
    assert costs == sorted(costs), armor_upkeep.ARMOR_PLAN


def test_equipping_carried_armour_is_tried_before_crafting(monkeypatch):
    """Grave recovery leaves armour in ordinary slots; wearing it is free."""
    import baritone_client.common.inventory as inv
    import baritone_client.common.resources as res

    client = _Client(iron=94, worn=0)

    def equip(c):
        c.worn = 4  # the carried pieces go on

    monkeypatch.setattr(inv, "equip_best_armor", equip)
    monkeypatch.setattr(
        res, "ensure_supplies",
        lambda *_a, **_k: pytest.fail("crafted despite carrying armour"),
    )

    ok, detail, before, after = armor_upkeep.run_armor_upkeep(client, SimpleNamespace())

    assert ok is True and before == 0 and after == 4
    assert "equipped carried armour" in detail


def test_crafting_that_changes_nothing_is_not_success(monkeypatch):
    """Otherwise a bot that cannot craft resets its no-progress streak forever."""
    import baritone_client.common.inventory as inv
    import baritone_client.common.resources as res

    monkeypatch.setattr(inv, "equip_best_armor", lambda _c: None)
    monkeypatch.setattr(
        res, "ensure_supplies", lambda *_a, **_k: SimpleNamespace(success=False)
    )

    ok, detail, before, after = armor_upkeep.run_armor_upkeep(
        _Client(iron=94, worn=0), SimpleNamespace()
    )

    assert ok is False
    assert before == after == 0
    assert "unchanged" in detail


def test_unreadable_equipment_never_loops_the_scheduler(monkeypatch):
    """Unknown armour state must read as dressed, not as endless work."""
    import baritone_client.common.inventory as inv

    def boom(_c):
        raise RuntimeError("bridge down")

    monkeypatch.setattr(inv, "get_equipped_armor", boom)

    assert armor_upkeep.equipped_pieces(_Client()) == armor_upkeep.TARGET_ARMOR_PIECES
    assert armor_upkeep.select_armor_opportunity(
        _Client(iron=94), _signals(), True
    ) is None
