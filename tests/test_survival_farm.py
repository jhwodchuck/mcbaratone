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


def test_each_tile_is_planted_from_beside_it_and_refusals_do_not_abort(world, monkeypatch):
    world.items = {"minecraft:wheat_seeds": 3}
    monkeypatch.setattr(
        "baritone_client.common.farming.harvest_wheat_farm",
        lambda *_a, **_k: pytest.fail("nothing is mature"),
    )
    stands = []
    monkeypatch.setattr(
        "baritone_client.common.navigation.goto",
        lambda _client, x, y, z, **kwargs: stands.append(((x, y, z), kwargs.get("radius"))) or True,
    )
    attempts = []

    def plant(_client, x, y, z):
        attempts.append((x, y, z))
        if len(attempts) == 1:
            raise RuntimeError("Target is not visible on a real block ray")
        world.items["minecraft:wheat_seeds"] -= 1
        return True

    monkeypatch.setattr("baritone_client.common.farming._till_and_plant_tile", plant)

    assert not survival_farm.tend_local_farm_for_food(world, _state(), now=0.0)
    assert len(attempts) == 4
    assert [stand for stand, _radius in stands] == [(x, y + 1, z) for x, y, z in attempts]
    assert all(radius == 1 for _stand, radius in stands)


def test_a_death_while_tending_is_not_swallowed(world, monkeypatch):
    from baritone_client.common.tasks import PlayerDeathDetected

    world.items = {"minecraft:wheat_seeds": 3}

    def die(*_a, **_k):
        raise PlayerDeathDetected("died")

    monkeypatch.setattr("baritone_client.common.farming._till_and_plant_tile", die)

    with pytest.raises(PlayerDeathDetected):
        survival_farm.tend_local_farm_for_food(world, _state(), now=0.0)


def test_farm_tending_failure_falls_through_to_the_food_search(monkeypatch):
    live = {"health": 6.8, "food_level": 15, "is_dead": False}
    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=lambda _route, _payload: dict(live))
    )
    order = []
    monkeypatch.setattr(
        objective_survival, "_has_carried_emergency_bread_materials", lambda _c: False
    )
    monkeypatch.setattr(
        objective_survival,
        "tend_local_farm_for_food",
        lambda *_a: (_ for _ in ()).throw(RuntimeError("interaction refused")),
    )

    def search(*_args):
        order.append("search")
        live.update(health=12.0, food_level=20)
        return True

    monkeypatch.setattr(objective_survival, "_attempt_survival_recovery_food", search)

    assert objective_survival.recover_survival_before_objective(client, _state())
    assert order == ["search"]


def test_farm_movement_is_marked_as_recovery_navigation(world, monkeypatch):
    # At critical health the defense loop cancels ordinary navigation every
    # tick; only movement marked as recovery may proceed with no threat near.
    world.items = {"minecraft:wheat": 2}
    world.crops = {(98, 100): {"id": "minecraft:wheat", "state": {"age": "7"}}}
    depths = []

    def harvest(client, *_a, **_k):
        depths.append(getattr(client, "_safe_recovery_navigation_depth", 0))
        return False

    monkeypatch.setattr("baritone_client.common.farming.harvest_wheat_farm", harvest)
    monkeypatch.setattr(
        "baritone_client.common.farming._till_and_plant_tile", lambda *_a: False
    )

    survival_farm.tend_local_farm_for_food(world, _state(), now=0.0)
    assert depths == [1]
    assert getattr(world, "_safe_recovery_navigation_depth", 0) == 0


def test_farm_below_or_above_player_is_not_a_local_recovery_route(world):
    world.items = {"minecraft:wheat": 3}
    world.position = (100, 54, 100)
    assert not survival_farm.tend_local_farm_for_food(world, _state(), now=0)
    assert world.food == 15


def test_failed_farm_movement_never_plants_from_the_wrong_position(world, monkeypatch):
    world.items = {"minecraft:wheat_seeds": 3}
    monkeypatch.setattr("baritone_client.common.navigation.goto", lambda *_a, **_k: False)
    monkeypatch.setattr("baritone_client.common.farming._till_and_plant_tile",
                        lambda *_a: pytest.fail("failed movement cannot authorize planting"))
    assert not survival_farm.tend_local_farm_for_food(world, _state(), now=0)


def test_partial_meal_refreshes_state_and_waits_for_regeneration(monkeypatch):
    live = {"health": 7, "food_level": 15, "is_dead": False}
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a: dict(live)))
    monkeypatch.setattr(objective_survival, "_secure_home_respawn", lambda *_a: None)
    monkeypatch.setattr(objective_survival, "_has_carried_emergency_bread_materials", lambda *_a: False)

    def partial_meal(*_a):
        live["food_level"] = 18
        return False  # not enough food to reach the helper's target of 20

    monkeypatch.setattr(objective_survival, "tend_local_farm_for_food", partial_meal)
    monkeypatch.setattr(objective_survival, "local_farm_wait_reason",
                        lambda *_a: "recovering health in a verified enclosure")
    monkeypatch.setattr(objective_survival, "_attempt_survival_recovery_food",
                        lambda *_a: pytest.fail("do not leave to search after a partial meal"))
    assert not objective_survival.recover_survival_before_objective(client, _state())


def test_growing_sheltered_farm_holds_without_claiming_food_success(monkeypatch):
    live = {"health": 7, "food_level": 16, "is_dead": False}
    calls = []
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda route, _p: calls.append(route) or dict(live)))
    reasons = []
    strategy = SimpleNamespace(suspend_for_survival=reasons.append)
    monkeypatch.setattr(objective_survival, "_secure_home_respawn", lambda *_a: None)
    monkeypatch.setattr(objective_survival, "_has_carried_emergency_bread_materials", lambda *_a: False)
    monkeypatch.setattr(objective_survival, "tend_local_farm_for_food", lambda *_a: False)
    monkeypatch.setattr(objective_survival, "local_farm_wait_reason", lambda *_a: "waiting for growing wheat")
    monkeypatch.setattr(objective_survival, "_attempt_survival_recovery_food",
                        lambda *_a: pytest.fail("must hold before storage or blind search"))
    assert not objective_survival.recover_survival_before_objective(client, _state(), strategy)
    assert reasons == ["waiting for growing wheat"]
    assert "cancel" in calls


def test_wait_requires_live_crops_water_same_height_and_shelter(world, monkeypatch):
    monkeypatch.setattr(survival_farm, "_wait_enclosure", lambda *_a: True)
    crops = {(100, 64, 100): {"id": "minecraft:water", "state": {"level": "0"}},
             (98, 65, 98): {"id": "minecraft:wheat", "state": {"age": "3"}},
             (98, 64, 98): {"id": "minecraft:farmland", "state": {"moisture": "7"}}}
    monkeypatch.setattr("baritone_client.common.farming._block_data",
                        lambda _c, x, y, z: crops.get((x, y, z), {"id": "minecraft:air"}))
    assert survival_farm.local_farm_wait_reason(world, _state(), world.dispatch("get_state", {}))
    world.position = (100, 54, 100)
    assert survival_farm.local_farm_wait_reason(world, _state(), world.dispatch("get_state", {})) is None
    world.position = (100, 64, 100)
    crops[(100, 64, 100)] = {"id": "minecraft:air"}
    assert survival_farm.local_farm_wait_reason(world, _state(), world.dispatch("get_state", {})) is None


def test_water_may_be_offset_from_the_recorded_plot_center(world, monkeypatch):
    monkeypatch.setattr(survival_farm, "_wait_enclosure", lambda *_a: True)
    crops = {(100, 64, 101): {"id": "minecraft:water", "state": {"level": "0"}},
             (98, 65, 98): {"id": "minecraft:wheat", "state": {"age": "3"}},
             (98, 64, 98): {"id": "minecraft:farmland", "state": {"moisture": "7"}}}
    monkeypatch.setattr("baritone_client.common.farming._block_data",
                        lambda _c, x, y, z: crops.get((x, y, z), {"id": "minecraft:air"}))
    assert survival_farm.local_farm_wait_reason(world, _state(), world.dispatch("get_state", {}))
    crops[(100, 64, 101)]["state"]["level"] = "2"
    assert survival_farm.local_farm_wait_reason(world, _state(), world.dispatch("get_state", {})) is None
    crops[(100, 64, 101)]["state"]["level"] = "0"
    crops[(98, 64, 98)]["state"]["moisture"] = "0"
    assert survival_farm.local_farm_wait_reason(world, _state(), world.dispatch("get_state", {})) is None
    crops[(98, 64, 98)]["state"]["moisture"] = "7"
    crops[(100, 64, 101)]["state"].pop("level")
    assert survival_farm.local_farm_wait_reason(world, _state(), world.dispatch("get_state", {})) is None


def test_wait_enclosure_rejects_open_door_and_requires_all_four_walls(world, monkeypatch):
    blocks = {}
    for x in range(99, 104):
        for z in range(98, 103):
            for y in (63, 67):
                blocks[(x, y, z)] = {"id": "minecraft:cobblestone"}
            if x in (99, 103) or z in (98, 102):
                for y in (64, 65):
                    blocks[(x, y, z)] = {"id": "minecraft:cobblestone"}
    monkeypatch.setattr("baritone_client.common.farming._block_data",
                        lambda _c, x, y, z: blocks.get((x, y, z), {"id": "minecraft:air"}))
    live = world.dispatch("get_state", {})
    assert survival_farm._wait_enclosure(world, live)
    blocks[(103, 64, 100)] = {"id": "minecraft:oak_door", "state": {"open": "true"}}
    assert not survival_farm._wait_enclosure(world, live)
    blocks[(103, 64, 100)]["state"]["open"] = "false"
    assert survival_farm._wait_enclosure(world, live)
    blocks[(101, 67, 100)] = {"id": "minecraft:air"}
    assert not survival_farm._wait_enclosure(world, live)


def test_four_posts_are_not_a_safe_room(world, monkeypatch):
    def block(_c, x, y, z):
        if (x, z) == (101, 100) and y == 67:
            return {"id": "minecraft:cobblestone"}
        if (x, z) in ((103, 100), (99, 100), (101, 102), (101, 98)) and y in (64, 65):
            return {"id": "minecraft:cobblestone"}
        return {"id": "minecraft:air"}
    monkeypatch.setattr("baritone_client.common.farming._block_data", block)
    assert not survival_farm._wait_enclosure(world, world.dispatch("get_state", {}))


def test_reachable_hostile_or_unknown_blocks_never_claim_safe_wait(world, monkeypatch):
    monkeypatch.setattr(survival_farm, "_hostile_close", lambda *_a: True)
    monkeypatch.setattr(survival_farm, "_wait_enclosure", lambda *_a: pytest.fail("threat must win"))
    assert survival_farm.local_farm_wait_reason(world, _state(), world.dispatch("get_state", {})) is None
    monkeypatch.setattr(survival_farm, "_hostile_close", lambda *_a: False)
    monkeypatch.setattr(survival_farm, "_wait_enclosure", lambda *_a: (_ for _ in ()).throw(RuntimeError("unknown")))
    assert survival_farm.local_farm_wait_reason(world, _state(), world.dispatch("get_state", {})) is None
