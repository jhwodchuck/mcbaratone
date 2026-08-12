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


def _signals(health=20.0, hostiles=0):
    """Armour uses a health floor only -- see armor_work_allowed."""
    return SimpleNamespace(
        health=health, nearby_hostiles=hostiles, food=20,
        safe_for_local_work=(health >= 16 and not hostiles),
    )


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


def test_a_dying_bot_does_not_stop_to_get_dressed():
    """Below the health floor, fleeing and eating come first."""
    assert armor_upkeep.select_armor_opportunity(
        _Client(iron=94, worn=0), _signals(health=1.5), True
    ) is None


def test_the_live_deadlock_condition_now_produces_work():
    """THE regression test for this module.

    The first version gated armour on `safe_for_local_work`, which demands
    health >= 16 and zero nearby hostiles. Measured across the live fleet, the
    standing blocker was "health 14.0<16, 2 hostile(s) near" -- the symptoms of
    having no armour. So the fix could never fire on the bots that needed it.
    A bot at 14 health with zombies on it and 94 iron in its pack is the single
    clearest case for putting boots on.
    """
    signals = _signals(health=14.0, hostiles=2)
    assert signals.safe_for_local_work is False, "precondition: comfort gate shut"

    assert armor_upkeep.select_armor_opportunity(
        _Client(iron=94, raw=19, worn=0), signals, True
    ) is not None


def test_hostiles_alone_never_block_armour():
    """Night with mobs about is when armour matters most."""
    assert armor_upkeep.select_armor_opportunity(
        _Client(iron=94, worn=0), _signals(health=20.0, hostiles=5), True
    ) is not None


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


def test_death_during_crafting_is_not_swallowed(monkeypatch):
    """PlayerDeathDetected is control flow, not an error.

    Its docstring says "phase execution must yield to top-level death
    recovery". The first version caught it in a bare `except Exception:
    continue`, and Bot15 went on crafting while dead -- the harness logged
    "Move aborted: player is dead", then walked to an unreachable table at
    y=70 and tried to build a crafting table from planks it did not have.
    """
    import baritone_client.common.inventory as inv
    import baritone_client.common.resources as res
    from baritone_client.common.tasks import PlayerDeathDetected

    def die(*_a, **_k):
        raise PlayerDeathDetected("Player died during combat or health recovery")

    monkeypatch.setattr(inv, "equip_best_armor", lambda _c: None)
    monkeypatch.setattr(res, "ensure_supplies", die)

    with pytest.raises(PlayerDeathDetected):
        armor_upkeep.run_armor_upkeep(_Client(iron=94, worn=0), SimpleNamespace())


def test_survival_recovery_signal_is_not_swallowed_either(monkeypatch):
    import baritone_client.common.inventory as inv
    import baritone_client.common.resources as res
    from baritone_client.common.tasks import SurvivalRecoveryRequired

    def bail(_c):
        raise SurvivalRecoveryRequired("needs recovery")

    monkeypatch.setattr(inv, "equip_best_armor", bail)
    monkeypatch.setattr(res, "ensure_supplies", lambda *_a, **_k: None)

    with pytest.raises(SurvivalRecoveryRequired):
        armor_upkeep.run_armor_upkeep(_Client(iron=94, worn=0), SimpleNamespace())


def test_ordinary_crafting_failures_are_still_tolerated(monkeypatch):
    """Only control-flow signals escape; a broken recipe must not crash a tick."""
    import baritone_client.common.inventory as inv
    import baritone_client.common.resources as res

    monkeypatch.setattr(inv, "equip_best_armor", lambda _c: None)
    monkeypatch.setattr(
        res, "ensure_supplies",
        lambda *_a, **_k: (_ for _ in ()).throw(ValueError("no recipe")),
    )

    ok, detail, _before, _after = armor_upkeep.run_armor_upkeep(
        _Client(iron=94, worn=0), SimpleNamespace()
    )

    assert ok is False and "unchanged" in detail


def test_worn_armour_is_not_mistaken_for_a_spare(monkeypatch):
    """THE Bot16 livelock.

    `get_inventory` folds the bridge's `armor` section into its counts, so
    count_item() reports 1 for a helmet the bot is wearing. Treating count > 0
    as "carrying a spare" meant any bot wearing one piece was offered this
    work every cooldown forever. Bot16 logged seven identical runs of
    "armour unchanged at 1/4 with 2 iron carried" -- 2 iron cannot buy the
    cheapest piece, so there was never anything it could do.
    """
    import baritone_client.common.inventory as inv

    client = _Client(iron=2, raw=0, worn=1, carried=["minecraft:iron_helmet"])
    monkeypatch.setattr(
        inv, "get_equipped_armor", lambda _c: {"head": "minecraft:iron_helmet"}
    )

    assert armor_upkeep.unworn_carried_pieces(client) == []
    assert armor_upkeep.select_armor_opportunity(client, _signals(), True) is None


def test_a_genuine_spare_is_still_detected(monkeypatch):
    """Wearing one helmet while carrying a second is real, equippable work."""
    import baritone_client.common.inventory as inv

    client = _Client(iron=0, worn=1)
    monkeypatch.setattr(inv, "count_item", lambda _c, item: 2 if "helmet" in item else 0)
    monkeypatch.setattr(
        inv, "get_equipped_armor", lambda _c: {"head": "minecraft:iron_helmet"}
    )

    assert armor_upkeep.unworn_carried_pieces(client) == ["minecraft:iron_helmet"]
    assert armor_upkeep.select_armor_opportunity(client, _signals(), True) is not None


def test_no_iron_and_no_spare_is_never_offered(monkeypatch):
    """The general form of the livelock: never offer unaffordable work."""
    import baritone_client.common.inventory as inv

    monkeypatch.setattr(inv, "get_equipped_armor", lambda c: {"head": "x"} if c.worn else {})

    assert armor_upkeep.select_armor_opportunity(
        _Client(iron=3, raw=0, worn=1), _signals(), True
    ) is None, "3 iron cannot buy the cheapest piece (boots cost 4)"


def test_raw_iron_yields_to_smelting_instead_of_armor_crafting(monkeypatch):
    """Live A1 regression: raw ore is not spendable in an armour recipe."""
    import baritone_client.common.inventory as inv
    import baritone_client.common.resources as res

    client = _Client(iron=3, raw=11, worn=1)
    monkeypatch.setattr(
        inv, "get_equipped_armor", lambda _c: {"feet": "minecraft:iron_boots"}
    )
    monkeypatch.setattr(inv, "equip_best_armor", lambda _c: None)
    monkeypatch.setattr(
        res,
        "ensure_supplies",
        lambda *_a, **_k: pytest.fail("raw iron was treated as recipe-ready"),
    )

    assert armor_upkeep.carried_iron(client) == 14
    assert armor_upkeep.spendable_iron(client) == 3
    assert armor_upkeep.select_armor_opportunity(client, _signals(), True) is None

    ok, detail, before, after = armor_upkeep.run_armor_upkeep(
        client, SimpleNamespace()
    )
    assert ok is False and before == after == 1
    assert "unchanged" in detail
