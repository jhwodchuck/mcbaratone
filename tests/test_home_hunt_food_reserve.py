"""Full hunger is not packed food for a home-material expedition."""

from types import SimpleNamespace

import pytest

from baritone_client.common import home_respawn


@pytest.mark.parametrize(
    ("inventory", "allowed"),
    [({}, False),
     ({"minecraft:bread": 7}, False),
     ({"minecraft:bread": 8}, True),
     ({"minecraft:cooked_beef": 4, "minecraft:cooked_chicken": 4}, True),
     ({"minecraft:wheat": 64, "minecraft:beef": 64}, False),
     ({"minecraft:bread": -1, "minecraft:cooked_beef": 9}, False),
     ({"minecraft:bread": "8"}, False),
     ({"minecraft:bread": True, "minecraft:cooked_beef": 7}, False),
     (None, False)],
)
def test_sheep_expedition_requires_observed_prepared_reserve(monkeypatch, inventory, allowed):
    live = {"health": 20, "food_level": 20, "world_time": 6000, "is_dead": False}
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a: dict(live)))
    state = SimpleNamespace(custom_data={})
    hunts, returns = [], []
    monkeypatch.setattr(home_respawn, "_missing_wool", lambda _c: 3)

    def read_inventory(_c):
        if inventory is None:
            raise RuntimeError("fresh inventory unavailable")
        return dict(inventory)

    monkeypatch.setattr("baritone_client.common.inventory.get_inventory", read_inventory)
    monkeypatch.setattr("baritone_client.common.combat.hunt_mobs",
                        lambda *_a, **kw: hunts.append(kw) or SimpleNamespace(reason="no targets"))
    monkeypatch.setattr("baritone_client.common.navigation.goto",
                        lambda *_a, **kw: returns.append(kw) or True)
    home_respawn._hunt_sheep_for_wool(client, state, (0, 64, 0))
    assert bool(hunts) is allowed
    assert bool(returns) is allowed
    assert (home_respawn.SHEEP_HUNT_KEY in state.custom_data) is allowed
