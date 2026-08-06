"""A food cycle must not confuse "never arrived" with "no crops".

Bot18 reported `harvested 0 wheat, established 0 plot, crafted 0 bread,
banked 0 prepared food` on six consecutive attempts. The message reads as an
empty farm. It was not: the bot was **461 blocks** from its own plot at
(-183,104,-404) and 467 from its base anchor. ``harvest_wheat_farm`` travels
first and returns whether it arrived, but the cycle discarded that answer, so
every failed journey was recorded as a barren harvest.

The cycle then "solved" the empty harvest by establishing another plot near
the same distant anchor -- repeating the trip that had just failed. Six
attempts produced six rejected sites and zero food while the fleet had ten
edible items in the entire world.
"""

from types import SimpleNamespace

import pytest

from baritone_client.common import food_supply


class _Client:
    def __init__(self, position=(-10, 78, 23)):
        self.position = position
        self.transport = SimpleNamespace(dispatch=self._dispatch)

    def _dispatch(self, route, payload=None, **_kwargs):
        if route == "get_state":
            x, y, z = self.position
            return {
                "block_position": {"x": x, "y": y, "z": z},
                "dimension": "minecraft:overworld",
                "health": 20.0,
                "is_dead": False,
            }
        return {}


def _state(plot=(-183, 104, -404)):
    return SimpleNamespace(
        custom_data={
            "food_worker": {"farm_plots": [{"origin": list(plot)}]},
            "base_location": [-166, 105, -417],
        }
    )


@pytest.fixture(autouse=True)
def _stub_environment(monkeypatch):
    monkeypatch.setattr(food_supply, "get_inventory", lambda _c: {})
    monkeypatch.setattr(food_supply, "eat_until_hunger", lambda *_a, **_k: True)
    monkeypatch.setattr(food_supply, "resolve_storage_location", lambda *_a, **_k: None)
    monkeypatch.setattr(food_supply, "craft", lambda *_a, **_k: True)


def test_an_unreachable_farm_is_reported_as_unreachable(monkeypatch):
    """The exact Bot18 case: the trip fails, and the detail must say so."""
    monkeypatch.setattr(food_supply, "harvest_wheat_farm", lambda *_a, **_k: False)
    established = []
    monkeypatch.setattr(
        food_supply, "establish_wheat_farm",
        lambda *a, **k: established.append(a) or None,
    )

    result = food_supply.run_food_cycle(_Client(), _state())

    assert result.success is False
    assert "could not reach" in result.detail, result.detail
    assert "461" in result.detail or "blocks away" in result.detail, result.detail


def test_an_unreachable_farm_does_not_trigger_building_another_one(monkeypatch):
    """Establishing a plot near the same distant anchor repeats the failed trip."""
    monkeypatch.setattr(food_supply, "harvest_wheat_farm", lambda *_a, **_k: False)
    established = []
    monkeypatch.setattr(
        food_supply, "establish_wheat_farm",
        lambda *a, **k: established.append(a) or None,
    )

    food_supply.run_food_cycle(_Client(), _state())

    assert not established, "tried to build a farm it also could not reach"


def test_a_reachable_but_barren_farm_still_tries_to_expand(monkeypatch):
    """Arriving and finding nothing IS a reason to establish another plot."""
    client = _Client()

    def arrive(_c, x, y, z, **_k):
        client.position = (x, y, z)  # a real harvest trip ends at the plot
        return True

    monkeypatch.setattr(food_supply, "harvest_wheat_farm", arrive)
    established = []
    monkeypatch.setattr(
        food_supply, "establish_wheat_farm",
        lambda *a, **k: established.append(a) or None,
    )

    result = food_supply.run_food_cycle(client, _state())

    assert established, "a reached-but-empty farm must still expand"
    assert "could not reach" not in result.detail


def test_the_unreachable_plot_is_recorded_for_an_operator():
    """Silent failure is what made this take a day to find."""
    state = _state()
    client = _Client()

    import baritone_client.common.food_supply as module
    original = module.harvest_wheat_farm
    module.harvest_wheat_farm = lambda *_a, **_k: False
    try:
        module.run_food_cycle(client, state)
    finally:
        module.harvest_wheat_farm = original

    worker = state.custom_data["food_worker"]
    assert worker.get("unreachable_plots") == [[-183, 104, -404]]


def test_new_plots_are_sited_within_walking_distance_of_base():
    """Siting used to march outward with every rejection.

    Bot18's six attempts landed at 20, 26, 48, 51, 64 and 72 blocks from base
    and the grid fallback grows without limit, so the worker eventually owned
    farms it never visited again.
    """
    from math import dist
    anchor = (-166, 105, -417)
    for cursor in range(24):
        candidate = food_supply._candidate(
            anchor, [(-183, 104, -404)], [], 32, cursor, 3
        )
        if candidate is None:
            continue
        assert dist(anchor, candidate) <= food_supply.MAX_ANCHOR_RADIUS + 1, (
            cursor, candidate, dist(anchor, candidate)
        )


def test_siting_reports_nothing_rather_than_wandering_when_base_is_boxed_in():
    """If every nearby site is rejected, say so instead of walking away.

    Returning None is the honest answer; an operator can then clear ground.
    Silently siting a farm 500 blocks out looks like progress and is not.
    """
    anchor = (0, 64, 0)
    rejected = [
        food_supply._candidate(anchor, [], [], 32, cursor, 3) for cursor in range(40)
    ]
    rejected = [site for site in rejected if site]
    assert food_supply._candidate(anchor, [], rejected, 32, 99, 3) is None


def test_the_radius_still_permits_more_than_one_plot():
    """A cap that only ever allows a single farm would starve the fleet."""
    from math import dist
    anchor = (0, 64, 0)
    found, rejected = [], []
    for cursor in range(40):
        candidate = food_supply._candidate(anchor, found, rejected, 32, cursor, 3)
        if candidate and candidate not in found:
            found.append(candidate)
        elif candidate:
            rejected.append(candidate)
    assert len(found) >= 2, found
    assert all(dist(anchor, site) <= food_supply.MAX_ANCHOR_RADIUS + 1 for site in found)
