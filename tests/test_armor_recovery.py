"""Armour comes back from home storage, smelting, and bounded mining."""

from types import SimpleNamespace

import pytest

from baritone_client.automator import armor_recovery, armor_upkeep


HOME = [100, 70, 100]


class _Bot:
    def __init__(self, *, health=20.0, food=20):
        self.health = health
        self.food = food
        self.items = {}
        self.worn = {}
        self.transport = SimpleNamespace(dispatch=self.dispatch)

    def dispatch(self, route, _payload):
        if route == "get_state":
            return {
                "health": self.health,
                "food_level": self.food,
                "is_dead": False,
                "dimension": "minecraft:overworld",
            }
        return {}


def _state():
    return SimpleNamespace(custom_data={"base_location": list(HOME)})


@pytest.fixture
def bot(monkeypatch):
    live = _Bot()
    storage = {"minecraft:iron_boots": 4, "minecraft:iron_helmet": 1}
    withdrawals, mined = [], []

    monkeypatch.setattr(
        "baritone_client.common.inventory.count_item",
        lambda _c, item: live.items.get(item, 0)
        + (1 if item in live.worn.values() else 0),
    )
    monkeypatch.setattr(
        "baritone_client.common.inventory.get_equipped_armor", lambda _c: dict(live.worn)
    )

    def equip(_c):
        for item in list(live.items):
            parts = item.split(":")[1].split("_")
            slot = parts[-1]
            if slot in armor_recovery.SLOTS and live.items[item] > 0 and slot not in live.worn:
                live.worn[slot] = item
                live.items[item] -= 1
        return len(live.worn)

    monkeypatch.setattr("baritone_client.common.inventory.equip_best_armor", equip)

    class _Catalog:
        def find_item(self, item):
            if storage.get(item, 0) <= 0:
                return []
            return [{"dimension": "minecraft:overworld", "x": 104, "y": 71, "z": 97, "count": storage[item]}]

    monkeypatch.setattr(
        "baritone_client.common.storage_catalog.catalog_for", lambda *_a, **_k: _Catalog()
    )

    def withdraw(_client, _state, item, wanted, **kwargs):
        withdrawals.append((item, wanted, kwargs))
        have = live.items.get(item, 0)
        take = min(storage.get(item, 0), max(0, wanted - have))
        storage[item] = storage.get(item, 0) - take
        live.items[item] = have + take

    monkeypatch.setattr(
        "baritone_client.common.home_respawn.withdraw_from_home_containers", withdraw
    )
    monkeypatch.setattr(
        "baritone_client.common.livestock_food._home_furnace", lambda *_a: (101, 70, 101)
    )

    def smelt(_client, _item, target, _pos):
        raw = live.items.get("minecraft:raw_iron", 0)
        live.items["minecraft:raw_iron"] = 0
        live.items["minecraft:iron_ingot"] = live.items.get("minecraft:iron_ingot", 0) + raw
        return True

    monkeypatch.setattr("baritone_client.common.resources._smelt_with_furnace", smelt)

    def gather(_client, ore, count, timeout):
        mined.append((ore, count, timeout))
        live.items["minecraft:raw_iron"] = count
        return True

    monkeypatch.setattr("baritone_client.common.resources.gather_ores", gather)
    return SimpleNamespace(live=live, storage=storage, withdrawals=withdrawals, mined=mined)


def test_storage_armour_near_home_is_fetched_and_worn(bot):
    note = armor_recovery.recover_armor_materials(bot.live, _state(), now=1000.0)

    assert bot.live.worn == {
        "boots": "minecraft:iron_boots",
        "helmet": "minecraft:iron_helmet",
    }
    assert "iron_helmet" in note and "iron_boots" in note
    for _item, _wanted, kwargs in bot.withdrawals:
        assert kwargs["radius"] == armor_recovery.STORAGE_RADIUS
        assert kwargs["max_vertical"] == armor_recovery.STORAGE_MAX_VERTICAL
        assert kwargs["recovery"] is True
    # No armour worn at the start, so mining waits for a later pass.
    assert bot.mined == []


def test_mining_needs_some_armour_health_and_food_and_keeps_ingots(bot):
    bot.storage.clear()
    bot.live.worn = {"boots": "minecraft:iron_boots", "helmet": "minecraft:iron_helmet"}
    bot.live.items["minecraft:iron_ingot"] = 3
    state = _state()

    note = armor_recovery.recover_armor_materials(bot.live, state, now=5000.0)

    # Leggings (7) + chestplate (8) = 15, minus 3 carried ingots.
    assert bot.mined == [("iron", 12, armor_recovery.MINING_TIMEOUT)]
    assert bot.live.items["minecraft:iron_ingot"] == 15
    assert "mined 12 raw iron" in note
    assert state.custom_data[armor_recovery.RECOVERY_KEY]["mining"] == 5000.0

    # The persisted cooldown blocks an immediate second trip.
    bot.live.items["minecraft:iron_ingot"] = 0
    armor_recovery.recover_armor_materials(bot.live, state, now=5100.0)
    assert len(bot.mined) == 1


@pytest.mark.parametrize(
    ("worn", "health", "food"),
    [
        ({}, 20.0, 20),
        ({"boots": "minecraft:iron_boots", "helmet": "minecraft:iron_helmet"}, 12.0, 20),
        ({"boots": "minecraft:iron_boots", "helmet": "minecraft:iron_helmet"}, 20.0, 10),
    ],
)
def test_mining_is_refused_without_armour_health_or_food(bot, worn, health, food):
    bot.storage.clear()
    bot.live.worn = dict(worn)
    bot.live.health, bot.live.food = health, food

    armor_recovery.recover_armor_materials(bot.live, _state(), now=5000.0)

    assert bot.mined == []


def test_carried_raw_iron_is_smelted(bot):
    bot.storage.clear()
    bot.live.items["minecraft:raw_iron"] = 5

    note = armor_recovery.recover_armor_materials(bot.live, _state(), now=1000.0)

    assert bot.live.items["minecraft:iron_ingot"] == 5
    assert "smelted 5 iron" in note


def test_recovery_ready_reflects_storage_and_cooldowns(bot):
    state = _state()
    assert armor_recovery.recovery_ready(bot.live, state, now=1000.0)

    state.custom_data[armor_recovery.RECOVERY_KEY] = {"storage": 900.0}
    assert not armor_recovery.recovery_ready(bot.live, state, now=1000.0)

    bot.live.worn = {slot: f"minecraft:iron_{slot}" for slot in armor_recovery.SLOTS}
    assert not armor_recovery.recovery_ready(bot.live, _state(), now=1000.0)


def test_armour_upkeep_is_offered_when_only_recovery_can_help(bot):
    signals = SimpleNamespace(health=20.0)

    offered = armor_upkeep.select_armor_opportunity(bot.live, signals, True, _state())
    assert offered is not None and "0/4" in offered.reason
    assert armor_upkeep.select_armor_opportunity(bot.live, signals, True) is None


def test_armour_upkeep_recovers_from_zero_through_storage(bot, monkeypatch):
    monkeypatch.setattr(armor_upkeep, "craft", lambda *_a, **_k: False)

    success, detail, before, after = armor_upkeep.run_armor_upkeep(bot.live, _state())

    assert success and (before, after) == (0, 2)
    assert "fetched" in detail
