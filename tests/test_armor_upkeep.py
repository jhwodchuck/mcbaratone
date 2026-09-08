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


@pytest.mark.parametrize("leather,boots", [(0, 0), (3, 0), (4, 0), (0, 1)])
def test_freeze_upkeep_only_crafts_affordable_boots(monkeypatch, leather, boots):
    import baritone_client.common.inventory as inv

    crafts = []
    client = _Client(worn=4)
    monkeypatch.setattr(inv, "equip_best_armor", lambda _c: None)
    monkeypatch.setattr(armor_upkeep, "needs_freeze_boots", lambda _c: True)
    monkeypatch.setattr(armor_upkeep, "_count", lambda _c, item:
                        leather if item == "minecraft:leather" else boots + len(crafts))
    monkeypatch.setattr(armor_upkeep, "craft", lambda _c, item, qty:
                        crafts.append((item, qty)) or True)
    monkeypatch.setattr(armor_upkeep, "equip_freeze_boots", lambda _c: bool(boots or crafts))
    ok, detail, before, after = armor_upkeep.run_armor_upkeep(client, SimpleNamespace())
    assert crafts == ([("minecraft:leather_boots", 1)] if leather >= 4 and not boots else [])
    assert ok is bool(boots or leather >= 4)
    assert (before, after) == (4, 4)
    if not ok:
        assert "needs 4 leather" in detail


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
        armor_upkeep, "craft",
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
        armor_upkeep, "craft", lambda *_a, **_k: False
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

def test_freezing_biome_requires_leather_boots(monkeypatch):
    """Regression: freezing biome needs leather boots, not just iron."""
    from baritone_client.common import defense

    client = _Client(worn=0)
    # Mock inventory to have no leather boots
    monkeypatch.setattr(
        client.transport, "dispatch",
        lambda cmd, params: {"armor": [], "inventory": []}
    )

    assessment = defense.assess_armor_for_environment(client, "freezing")
    assert assessment.action == "gather_leather"
    assert "no leather boots" in assessment.reason


def test_select_armor_opportunity_declines_when_freezing_without_leather(monkeypatch):
    """select_armor_opportunity must not offer armor_upkeep in a freezing biome
    when leather is unavailable -- that just burns retries with no progress.
    Leather gathering (signaled by defense.assess_armor_for_environment) should
    run first.
    """
    from baritone_client.automator import armor_upkeep

    # In freezing biome, no leather, no leather boots carried
    client = _Client(worn=0)
    monkeypatch.setattr(armor_upkeep, "_count", lambda _c, item: 0)
    monkeypatch.setattr(armor_upkeep, "in_freezing_biome", lambda _c: True)
    monkeypatch.setattr(armor_upkeep, "wearing_freeze_boots", lambda _c: False)

    signals = _signals()
    opportunity = armor_upkeep.select_armor_opportunity(client, signals, True)
    assert opportunity is None, "should not offer armor_upkeep when leather is missing"


def test_select_armor_opportunity_offers_when_freezing_with_leather(monkeypatch):
    """select_armor_opportunity should offer armor_upkeep in a freezing biome
    when leather is available (>=4) or leather boots are already carried.
    """
    from baritone_client.automator import armor_upkeep

    # In freezing biome, has 4 leather
    client = _Client(worn=0)
    monkeypatch.setattr(armor_upkeep, "_count", lambda _c, item: 4 if item == "minecraft:leather" else 0)
    monkeypatch.setattr(armor_upkeep, "in_freezing_biome", lambda _c: True)
    monkeypatch.setattr(armor_upkeep, "wearing_freeze_boots", lambda _c: False)

    signals = _signals()
    opportunity = armor_upkeep.select_armor_opportunity(client, signals, True)
    assert opportunity is not None
    assert opportunity.kind == armor_upkeep.OpportunityKind.ARMOR_UPKEEP


def test_select_armor_opportunity_offers_when_freezing_with_leather_boots_carried(monkeypatch):
    """select_armor_opportunity should offer armor_upkeep in a freezing biome
    when leather boots are already in inventory (just need equipping).
    """
    from baritone_client.automator import armor_upkeep

    # In freezing biome, has leather boots in inventory
    client = _Client(worn=0)
    monkeypatch.setattr(armor_upkeep, "_count", lambda _c, item: 1 if item == "minecraft:leather_boots" else 0)
    monkeypatch.setattr(armor_upkeep, "in_freezing_biome", lambda _c: True)
    monkeypatch.setattr(armor_upkeep, "wearing_freeze_boots", lambda _c: False)

    signals = _signals()
    opportunity = armor_upkeep.select_armor_opportunity(client, signals, True)
    assert opportunity is not None
    assert opportunity.kind == armor_upkeep.OpportunityKind.ARMOR_UPKEEP


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
    monkeypatch.setattr(armor_upkeep, "craft", die)

    with pytest.raises(PlayerDeathDetected):
        armor_upkeep.run_armor_upkeep(_Client(iron=94, worn=0), SimpleNamespace())


def test_survival_recovery_signal_is_not_swallowed_either(monkeypatch):
    import baritone_client.common.inventory as inv
    import baritone_client.common.resources as res
    from baritone_client.common.tasks import SurvivalRecoveryRequired

    def bail(_c):
        raise SurvivalRecoveryRequired("needs recovery")

    monkeypatch.setattr(inv, "equip_best_armor", bail)
    monkeypatch.setattr(armor_upkeep, "craft", lambda *_a, **_k: None)

    with pytest.raises(SurvivalRecoveryRequired):
        armor_upkeep.run_armor_upkeep(_Client(iron=94, worn=0), SimpleNamespace())


def test_ordinary_crafting_failures_are_still_tolerated(monkeypatch):
    """Only control-flow signals escape; a broken recipe must not crash a tick."""
    import baritone_client.common.inventory as inv
    import baritone_client.common.resources as res

    monkeypatch.setattr(inv, "equip_best_armor", lambda _c: None)
    monkeypatch.setattr(
        armor_upkeep, "craft",
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
        armor_upkeep,
        "craft",
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


def test_enough_iron_uses_craft_not_ensure_supplies(monkeypatch):
    import baritone_client.common.inventory as inv
    import baritone_client.common.resources as res
    client = _Client(iron=14, worn=1)
    calls = []
    monkeypatch.setattr(inv, "equip_best_armor", lambda c: c.worn)
    monkeypatch.setattr(
        res, "ensure_supplies",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("ensure_supplies should not be called")),
    )
    monkeypatch.setattr(
        armor_upkeep, "craft",
        lambda c, item, count=1: calls.append(item) or True,
    )
    armor_upkeep.run_armor_upkeep(client, SimpleNamespace())
    assert calls, "should have attempted to craft at least one piece"


def test_successful_craft_attempts_equip_even_when_inventory_count_does_not_increase(monkeypatch):
    """Successful craft must still try to equip the piece.

    Regression for the saturated A1 counter:
    armour unchanged at 1/4 with 14 iron carried
    """
    import baritone_client.common.inventory as inv

    client = _Client(iron=14, worn=1)

    # Force every armour count to 0 so the bot tries to craft all four pieces.
    monkeypatch.setattr(
        inv, "count_item",
        lambda c, item: c.iron if item == "minecraft:iron_ingot" else 0,
    )
    monkeypatch.setattr(
        inv, "get_equipped_armor",
        lambda c: {"feet": "minecraft:iron_boots"} if c.worn else {},
    )

    equip_calls = []
    monkeypatch.setattr(
        inv, "equip_best_armor",
        lambda c: equip_calls.append(1),
    )

    def fake_craft(_c, item, count=1):
        return True

    monkeypatch.setattr(armor_upkeep, "craft", fake_craft)

    armor_upkeep.run_armor_upkeep(client, SimpleNamespace())

    assert len(equip_calls) >= 2, (
        "expected at least two equip attempts: initial plus after a crafted piece"
    )


_FULL_IRON_SET = {
    "helmet": "minecraft:iron_helmet",
    "chestplate": "minecraft:iron_chestplate",
    "leggings": "minecraft:iron_leggings",
    "boots": "minecraft:iron_boots",
}


def test_worn_out_helmet_survives_the_already_dressed_exit(monkeypatch):
    """THE A1Bot 2026-09-07 bug: a helmet at 153/165 damage sat unreplaced
    through a five-death spiral because a 4/4 equipped count alone satisfied
    the "already dressed" exit before anything asked about durability.
    """
    import baritone_client.common.inventory as inv

    monkeypatch.setattr(inv, "get_equipped_armor", lambda _c: dict(_FULL_IRON_SET))
    monkeypatch.setattr(
        inv, "armor_piece_is_durable",
        lambda _c, item_id: item_id != "minecraft:iron_helmet",
    )

    opportunity = armor_upkeep.select_armor_opportunity(
        _Client(iron=20, worn=4), _signals(), True
    )

    assert opportunity is not None
    assert "nearly broken" in opportunity.reason


def test_a_durable_full_set_is_never_offered_the_work(monkeypatch):
    """No false positives: nothing worn out means the exit still applies."""
    import baritone_client.common.inventory as inv

    monkeypatch.setattr(inv, "get_equipped_armor", lambda _c: dict(_FULL_IRON_SET))
    monkeypatch.setattr(inv, "armor_piece_is_durable", lambda *_a: True)

    assert armor_upkeep.select_armor_opportunity(
        _Client(iron=20, worn=4), _signals(), True
    ) is None


def test_worn_out_piece_is_never_offered_without_spendable_iron(monkeypatch):
    """A replacement nobody can afford is not an opportunity."""
    import baritone_client.common.inventory as inv

    monkeypatch.setattr(inv, "get_equipped_armor", lambda _c: dict(_FULL_IRON_SET))
    monkeypatch.setattr(
        inv, "armor_piece_is_durable",
        lambda _c, item_id: item_id != "minecraft:iron_helmet",
    )

    assert armor_upkeep.select_armor_opportunity(
        _Client(iron=0, worn=4), _signals(), True
    ) is None


def test_run_armor_upkeep_crafts_a_replacement_for_a_worn_out_piece(monkeypatch):
    """End to end: a worn helmet gets a fresh replacement crafted and worn,
    and this must count as success even though the equipped count (4) never
    changes -- one worn iron helmet out, one fresh iron helmet in.
    """
    import baritone_client.common.inventory as inv

    still_worn = {"value": True}
    monkeypatch.setattr(inv, "get_equipped_armor", lambda _c: dict(_FULL_IRON_SET))
    monkeypatch.setattr(
        inv, "armor_piece_is_durable",
        lambda _c, item_id: not (
            item_id == "minecraft:iron_helmet" and still_worn["value"]
        ),
    )
    monkeypatch.setattr(inv, "equip_best_armor", lambda _c: 4)
    crafted_calls = []

    def fake_craft(_c, item_id, count=1):
        crafted_calls.append(item_id)
        still_worn["value"] = False
        return True

    monkeypatch.setattr(armor_upkeep, "craft", fake_craft)

    # carried must list all four pieces: this fixture's count_item stub has
    # no notion that "worn" implies "carried" the way the real bridge's
    # merged inventory+armor counts do, so without this missing_pieces would
    # (wrongly, for this fixture only) also treat every piece as absent.
    client = _Client(
        iron=20,
        worn=4,
        carried=(
            "minecraft:iron_helmet",
            "minecraft:iron_boots",
            "minecraft:iron_leggings",
            "minecraft:iron_chestplate",
        ),
    )
    ok, detail, before, after = armor_upkeep.run_armor_upkeep(
        client, SimpleNamespace()
    )

    assert ok is True
    assert crafted_calls == ["minecraft:iron_helmet"]
    assert before == after == 4
