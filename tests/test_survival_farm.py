"""Survival recovery grows food at a nearby farm instead of idling unfed."""

from types import SimpleNamespace

import pytest

from baritone_client.automator import objective_survival
from baritone_client.common import survival_farm


CENTER = (100, 64, 100)


def _state():
    return SimpleNamespace(
        custom_data={
            "structures": {
                "food_source": {
                    "type": "starter_crop_farm",
                    "location": list(CENTER),
                    "verified": True,
                }
            }
        }
    )


class _World:
    """Minimal live state, inventory and crop grid for the farm primitives."""

    def __init__(self, *, position=(101, 64, 100), food=15, items=None, crops=None):
        self.position = position
        self.food = food
        self.items = dict(items or {})
        self.crops = dict(crops or {})
        self.transport = SimpleNamespace(dispatch=self.dispatch)

    def dispatch(self, route, _payload):
        assert route == "get_state"
        x, y, z = self.position
        return {
            "health": 6.8,
            "food_level": self.food,
            "is_dead": False,
            "block_position": {"x": x, "y": y, "z": z},
        }


@pytest.fixture
def world(monkeypatch):
    live = _World()
    monkeypatch.setattr(
        "baritone_client.common.combat.scan_for_threats", lambda *_a, **_k: []
    )
    monkeypatch.setattr(
        "baritone_client.common.inventory.count_item",
        lambda _client, item: live.items.get(item, 0),
    )
    monkeypatch.setattr(
        "baritone_client.common.emergency_food.emergency_food_count",
        lambda _client: live.items.get("minecraft:bread", 0),
    )

    def bake(_client, **_kwargs):
        loaves = live.items.get("minecraft:wheat", 0) // 3
        live.items["minecraft:wheat"] = live.items.get("minecraft:wheat", 0) - 3 * loaves
        live.items["minecraft:bread"] = live.items.get("minecraft:bread", 0) + loaves
        return loaves > 0

    monkeypatch.setattr(
        "baritone_client.common.emergency_food.craft_emergency_bread_from_carried_wheat",
        bake,
    )

    def eat(_client, minimum_food=14):
        while live.food < minimum_food and live.items.get("minecraft:bread", 0):
            live.items["minecraft:bread"] -= 1
            live.food = min(20, live.food + 5)
        return live.food >= minimum_food

    monkeypatch.setattr("baritone_client.common.combat.eat_until_hunger", eat)
    monkeypatch.setattr(
        "baritone_client.common.farming._block_data",
        lambda _client, x, y, z: live.crops.get((x, z), {"id": "minecraft:air"}),
    )
    monkeypatch.setattr(
        "baritone_client.common.navigation.goto", lambda *_a, **_k: True
    )
    return live


def test_carried_wheat_is_baked_and_eaten_first(world, monkeypatch):
    world.items = {"minecraft:wheat": 3}
    monkeypatch.setattr(
        "baritone_client.common.farming.harvest_wheat_farm",
        lambda *_a, **_k: pytest.fail("no farm work needed with bread in hand"),
    )

    assert survival_farm.tend_local_farm_for_food(world, _state(), now=0.0)
    assert world.food == 20


def test_mature_wheat_is_harvested_then_baked(world, monkeypatch):
    world.items = {"minecraft:wheat": 2}
    world.crops = {(98, 100): {"id": "minecraft:wheat", "state": {"age": "7"}}}
    harvests = []

    def harvest(_client, x, y, z, range_=8):
        harvests.append(((x, y, z), range_))
        world.items["minecraft:wheat"] += 1
        return True

    monkeypatch.setattr("baritone_client.common.farming.harvest_wheat_farm", harvest)
    monkeypatch.setattr(
        "baritone_client.common.farming._till_and_plant_tile", lambda *_a: False
    )

    assert survival_farm.tend_local_farm_for_food(world, _state(), now=0.0)
    assert harvests == [(CENTER, 3)]
    assert world.food == 20


def test_empty_tiles_are_replanted_without_harvesting_immature_wheat(world, monkeypatch):
    world.items = {"minecraft:wheat_seeds": 3}
    world.crops = {(98, 98): {"id": "minecraft:wheat", "state": {"age": "3"}}}
    monkeypatch.setattr(
        "baritone_client.common.farming.harvest_wheat_farm",
        lambda *_a, **_k: pytest.fail("immature wheat must not be harvested"),
    )
    planted = []

    def plant(_client, x, y, z):
        planted.append((x, y, z))
        world.items["minecraft:wheat_seeds"] -= 1
        return True

    monkeypatch.setattr("baritone_client.common.farming._till_and_plant_tile", plant)

    assert not survival_farm.tend_local_farm_for_food(world, _state(), now=0.0)
    assert len(planted) == 3
    assert (98, 64, 98) not in planted
    assert all(tile[1] == CENTER[1] for tile in planted)


def test_distant_farm_hostiles_and_cooldown_leave_the_farm_alone(world, monkeypatch):
    world.items = {"minecraft:wheat": 3}
    world.position = (140, 64, 100)
    assert not survival_farm.tend_local_farm_for_food(world, _state(), now=0.0)

    world.position = (101, 64, 100)
    monkeypatch.setattr(
        "baritone_client.common.combat.scan_for_threats",
        lambda *_a, **_k: [{"distance": 5.0}],
    )
    monkeypatch.setattr(
        "baritone_client.common.combat._threat_can_reach_player", lambda *_a, **_k: True
    )
    assert not survival_farm.tend_local_farm_for_food(world, _state(), now=100.0)

    monkeypatch.setattr(
        "baritone_client.common.combat.scan_for_threats", lambda *_a, **_k: []
    )
    assert not survival_farm.tend_local_farm_for_food(world, _state(), now=130.0)
    assert survival_farm.tend_local_farm_for_food(world, _state(), now=170.0)


def test_regenerating_player_needs_no_farm_work(world):
    world.items = {"minecraft:wheat": 3}
    world.food = 18

    assert not survival_farm.tend_local_farm_for_food(world, _state(), now=0.0)
    assert world.items["minecraft:wheat"] == 3


def test_survival_gate_tends_the_farm_before_the_blind_food_search(monkeypatch):
    live = {"health": 6.8, "food_level": 15, "is_dead": False}
    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=lambda _route, _payload: dict(live))
    )
    order = []
    monkeypatch.setattr(
        objective_survival, "_has_carried_emergency_bread_materials", lambda _c: False
    )

    def tend(_client, _state):
        order.append("farm")
        live.update(health=12.0, food_level=20)
        return True

    monkeypatch.setattr(objective_survival, "tend_local_farm_for_food", tend)
    monkeypatch.setattr(
        objective_survival,
        "_attempt_survival_recovery_food",
        lambda *_a: pytest.fail("blind search must wait for the local farm"),
    )

    assert objective_survival.recover_survival_before_objective(client, _state())
    assert order == ["farm"]
