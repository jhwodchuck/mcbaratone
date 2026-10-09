"""Mining preparation verifies inventory effects before leaving home."""

from types import SimpleNamespace

import pytest

from baritone_client.automator import iron_preparation as prep


@pytest.fixture
def setup(monkeypatch):
    observed = SimpleNamespace(free=1, tool=False, deposits=[], withdrawals=[])
    monkeypatch.setattr("baritone_client.automator.armor_recovery._home", lambda _s: (0, 70, 0))
    monkeypatch.setattr("baritone_client.common.inventory.resolve_storage_location", lambda *_a, **_k: (2, 70, 2))
    monkeypatch.setattr("baritone_client.common.tunnel_miner.free_slots", lambda _c: observed.free)
    monkeypatch.setattr("baritone_client.common.inventory.get_inventory", lambda _c: {})
    monkeypatch.setattr(prep, "has_mining_pickaxe", lambda _c: observed.tool)
    monkeypatch.setattr(prep, "restore_kit", lambda _c: [])
    monkeypatch.setattr("baritone_client.common.inventory.deposit_excess_to_chest", lambda *_a, **k: observed.deposits.append(k) or 4)
    monkeypatch.setattr("baritone_client.common.home_respawn.withdraw_from_home_containers", lambda *_a, **k: observed.withdrawals.append(k))
    monkeypatch.setattr("baritone_client.automator.mining_storage.bank_mining_overflow", lambda *_a: False)
    return observed


def test_successful_report_without_space_or_tool_does_not_start_mining(setup):
    assert prep.prepare_iron_inventory(object(), object()) == "no durable stone-or-better pickaxe for iron"
    assert setup.deposits[0]["retain_counts"] == {
        "minecraft:carrot": 64, "minecraft:cobblestone": 64,
        "minecraft:wheat_seeds": 16,
    }
    assert "minecraft:cobblestone" in setup.deposits[0]["deposit_items"]
    assert "minecraft:enchanted_book" in setup.deposits[0]["deposit_items"]
    assert "minecraft:bread" not in setup.deposits[0]["deposit_items"]
    assert setup.withdrawals[0]["max_vertical"] == 8


def test_inventory_full_still_blocks_a_usable_tool(setup):
    setup.tool = True
    assert "inventory space" in prep.prepare_iron_inventory(object(), object())
    assert setup.withdrawals == []


@pytest.mark.parametrize("chest", [(200, 70, 0), (0, 30, 0)])
def test_preparation_never_banks_at_remote_or_deep_storage(setup, monkeypatch, chest):
    setup.tool = True
    monkeypatch.setattr("baritone_client.common.inventory.resolve_storage_location", lambda *_a, **_k: chest)
    assert "inventory space" in prep.prepare_iron_inventory(object(), object())
    assert setup.deposits == []


def test_preparation_rechecks_real_space_and_pickaxe(setup, monkeypatch):
    def bank(*_a, **_k):
        setup.free = 10
        return 3

    def craft(_c):
        setup.tool = True
        return ["stone_pickaxe"]

    monkeypatch.setattr("baritone_client.common.inventory.deposit_excess_to_chest", bank)
    monkeypatch.setattr(prep, "restore_kit", craft)
    assert prep.prepare_iron_inventory(object(), object()) == ""


def test_rubble_and_valuable_but_non_trip_items_are_banked_with_food_and_seed_reserves(setup):
    setup.tool = True
    prep.prepare_iron_inventory(object(), object())

    bank = setup.deposits[0]
    assert {"minecraft:cobblestone", "minecraft:deepslate"} <= bank["deposit_items"]
    assert {"minecraft:lead", "minecraft:bell", "minecraft:leaf_litter"} <= bank["deposit_items"]
    assert bank["retain_counts"] == {
        "minecraft:carrot": 64, "minecraft:cobblestone": 64,
        "minecraft:wheat_seeds": 16,
    }


def test_preparation_read_error_propagates_without_authorizing_trip(setup, monkeypatch):
    def failed(_c):
        raise RuntimeError("unavailable inventory")

    monkeypatch.setattr("baritone_client.common.tunnel_miner.free_slots", failed)
    with pytest.raises(RuntimeError):
        prep.prepare_iron_inventory(object(), object())


def test_minimum_mid_trip_room_is_not_enough_to_start_a_long_trip(setup):
    setup.tool, setup.free = True, 3
    assert "inventory space" in prep.prepare_iron_inventory(object(), object())


def test_overflow_acknowledgement_without_free_slots_cannot_admit_trip(setup, monkeypatch):
    setup.tool = True
    monkeypatch.setattr("baritone_client.automator.mining_storage.bank_mining_overflow", lambda *_a: True)
    assert "inventory space" in prep.prepare_iron_inventory(object(), object())


def test_verified_overflow_space_admits_trip(setup, monkeypatch):
    setup.tool = True
    def bank(*_a):
        setup.free = 10
        return False  # observed inventory, not the helper's return, is authoritative
    monkeypatch.setattr("baritone_client.automator.mining_storage.bank_mining_overflow", bank)
    assert prep.prepare_iron_inventory(object(), object()) == ""


@pytest.mark.parametrize("transferred", [False, True])
def test_copper_is_stored_to_recover_the_last_required_slot(setup, monkeypatch, transferred):
    setup.tool, setup.free = True, 9
    copper = {"carried": 4, "stored": 0}

    def overflow(_client, _state, _anchor, _required, items, _retains):
        assert "minecraft:raw_copper" in items
        assert not {"minecraft:raw_iron", "minecraft:iron_ingot", "minecraft:bread",
                    "minecraft:stone_pickaxe", "minecraft:torch"} & items
        if transferred:
            copper["stored"], copper["carried"] = copper["carried"], 0
            setup.free += 1
        return True  # Acknowledgement alone must never admit the expedition.

    monkeypatch.setattr("baritone_client.automator.mining_storage.bank_mining_overflow", overflow)
    result = prep.prepare_iron_inventory(object(), object())
    assert (result == "") is transferred
    assert copper == ({"carried": 0, "stored": 4} if transferred else {"carried": 4, "stored": 0})


@pytest.mark.parametrize("verified_room", [False, True])
def test_rejected_original_chest_can_reach_guarded_overflow(setup, monkeypatch, verified_room):
    from baritone_client.core.exceptions import CommandError

    setup.tool = True
    attempted = []
    def rejected(*_a, **_k):
        raise CommandError("Target is not visible on a real block ray")
    def overflow(*_a):
        attempted.append(True)
        if verified_room:
            setup.free = 10
        return True  # Only the independently observed slot count admits mining.
    monkeypatch.setattr("baritone_client.common.inventory.deposit_excess_to_chest", rejected)
    monkeypatch.setattr("baritone_client.automator.mining_storage.bank_mining_overflow", overflow)
    result = prep.prepare_iron_inventory(object(), object())
    assert attempted == [True]
    assert (result == "") is verified_room


@pytest.mark.parametrize("transferred", [False, True])
@pytest.mark.parametrize("plank_reserve", [2, 4])
def test_building_leftovers_free_room_without_banking_trip_supplies(setup, monkeypatch, transferred, plank_reserve):
    setup.tool, setup.free = True, 7
    carried = {
        "minecraft:oak_planks": plank_reserve, "minecraft:birch_planks": 2,
        "minecraft:oak_door": 2, "minecraft:white_wool": 2,
        "minecraft:oak_log": 2, "minecraft:stick": 2,
        "minecraft:bread": 8, "minecraft:torch": 12,
        "minecraft:stone_pickaxe": 1, "minecraft:iron_sword": 1,
        "minecraft:iron_helmet": 1, "minecraft:raw_iron": 3,
    }
    original = dict(carried)
    monkeypatch.setattr("baritone_client.common.inventory.get_inventory", lambda _c: dict(carried))

    def bank(*_a, deposit_items, retain_counts, **_k):
        if transferred:
            for item in list(carried):
                if item in deposit_items and carried[item] > retain_counts.get(item, 0):
                    carried[item] = retain_counts.get(item, 0)
                    if carried[item] == 0:
                        del carried[item]
                        setup.free += 1
        return 3  # A transfer claim alone cannot admit mining.

    monkeypatch.setattr("baritone_client.common.inventory.deposit_excess_to_chest", bank)
    result = prep.prepare_iron_inventory(object(), object())
    assert (result == "") is transferred
    banked = {"minecraft:birch_planks", "minecraft:oak_door", "minecraft:white_wool"}
    assert carried == ({k: v for k, v in original.items() if k not in banked} if transferred else original)
    assert setup.free == (10 if transferred else 7)


def test_unknown_building_inventory_cannot_authorize_preparation(setup, monkeypatch):
    setup.tool = True
    def failed(_c):
        raise RuntimeError("inventory unavailable")
    monkeypatch.setattr("baritone_client.common.inventory.get_inventory", failed)
    with pytest.raises(RuntimeError, match="inventory unavailable"):
        prep.prepare_iron_inventory(object(), object())
    assert setup.deposits == []
