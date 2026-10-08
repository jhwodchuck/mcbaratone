from types import SimpleNamespace

import pytest

from baritone_client.automator import mining_storage as storage


@pytest.fixture
def home(monkeypatch):
    anchor = (3, 71, 3)
    blocks = {(5, 70, 1): "minecraft:cobblestone", (6, 71, 1): "minecraft:cobblestone"}
    stock, actions = {}, []
    state = SimpleNamespace(custom_data={"base_location": list(anchor), "structures": {"starter_house": {"origin": [0, 70, 0]}}})
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda route, payload: {"id": blocks.get(tuple(payload[a] for a in ("x", "y", "z")), "minecraft:air")}))
    observed = SimpleNamespace(free=2, safe=True, place=True, bank=True)
    monkeypatch.setattr(storage, "_at_home", lambda *_a: observed.safe)
    monkeypatch.setattr("baritone_client.common.inventory.count_item", lambda _c, item: stock.get(item, 0))
    def craft(_c, item, count):
        actions.append(("craft", item))
        if item == "minecraft:oak_planks" and stock.get("minecraft:oak_log", 0) >= 2:
            stock["minecraft:oak_planks"], stock["minecraft:oak_log"] = 8, 0
        if item == "minecraft:chest" and stock.get("minecraft:oak_planks", 0) >= 8:
            stock["minecraft:chest"], stock["minecraft:oak_planks"] = 1, 0
        return True
    monkeypatch.setattr("baritone_client.common.inventory.craft", craft)
    def gather(*_a):
        stock["minecraft:oak_log"] = 2
        actions.append(("gather",))
    monkeypatch.setattr("baritone_client.automator.charcoal_wood.gather_charcoal_logs", gather)
    def place(_c, x, y, z, item, **kwargs):
        assert kwargs == {"allow_break": False}
        actions.append(("place", (x, y, z)))
        if observed.place:
            blocks[(x, y, z)] = item
        return True
    monkeypatch.setattr("baritone_client.common.harness_ops.place_block_exact", place)
    monkeypatch.setattr("baritone_client.common.storage_catalog.catalog_for", lambda *_a: SimpleNamespace(register_container=lambda *_a, **_k: actions.append(("catalog",))))
    def deposit(*_a, **kwargs):
        actions.append(("deposit", kwargs))
        if observed.bank:
            observed.free = 10
        return 4
    monkeypatch.setattr("baritone_client.common.inventory.deposit_excess_to_chest", deposit)
    monkeypatch.setattr("baritone_client.common.tunnel_miner.free_slots", lambda _c: observed.free)
    return client, state, anchor, blocks, stock, actions, observed


def bank(home):
    c, s, anchor, *_ = home
    return storage._bank.__wrapped__(c, s, anchor, 10, {"minecraft:carrot"}, {"minecraft:carrot": 64})


def test_capacity_chain_verifies_chest_and_space_and_preserves_reserves(home):
    assert bank(home)
    assert home[1].custom_data["mining_storage"]["chest"] == [5, 71, 1]
    actions = home[5]
    assert [a[0] for a in actions] == ["gather", "craft", "craft", "place", "catalog", "deposit"]
    assert actions[-1][1]["retain_counts"] == {"minecraft:carrot": 64}


@pytest.mark.parametrize("failure", ["floor", "wall", "obstruction", "headroom", "landmark", "far_home"])
def test_unsafe_spot_never_gathers_or_places(home, failure):
    blocks, state = home[3], home[1]
    if failure == "floor": blocks[(5, 70, 1)] = "minecraft:water"
    elif failure == "wall": blocks[(6, 71, 1)] = "minecraft:void_air"
    elif failure == "obstruction": blocks[(5, 71, 1)] = "minecraft:oak_door"
    elif failure == "headroom": blocks[(5, 72, 1)] = "minecraft:stone"
    elif failure == "landmark": state.custom_data["structures"]["starter_house"]["bed"] = [5, 71, 1]
    else: state.custom_data["structures"]["starter_house"]["origin"] = [100, 70, 100]
    assert not bank(home)
    assert home[5] == []


def test_place_ack_without_chest_does_not_bank_or_record(home):
    home[6].place = False
    assert not bank(home)
    assert "chest" not in home[1].custom_data["mining_storage"]
    assert not any(a[0] == "deposit" for a in home[5])


def test_deposit_ack_without_room_is_failure(home):
    home[6].bank = False
    assert not bank(home)


def test_existing_overflow_is_reused_without_new_gather_or_build(home):
    home[1].custom_data["mining_storage"] = {"chest": [5, 71, 1]}
    home[3][(5, 71, 1)] = "minecraft:chest"
    assert bank(home)
    assert [a[0] for a in home[5]] == ["deposit"]


def test_failed_home_observation_refuses_all_work(home):
    home[6].safe = False
    assert not storage.bank_mining_overflow(home[0], home[1], home[2], 10, set(), {})
    assert home[5] == []


def test_read_failure_is_closed(home, monkeypatch):
    def fail(*_a): raise RuntimeError("unknown block")
    monkeypatch.setattr(storage, "_block", fail)
    assert not storage.bank_mining_overflow(home[0], home[1], home[2], 10, set(), {})
