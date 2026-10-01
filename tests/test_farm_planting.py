"""Planting failures remain bounded and do not strand a whole crop plot."""

import pytest

from baritone_client.common import farming
from baritone_client.common.farm_planting import plant_farm_tiles
from baritone_client.common.tasks import PlayerDeathDetected, SurvivalRecoveryRequired


def setup(monkeypatch, traveler, planter):
    monkeypatch.setattr(farming, "count_item", lambda *_a: 12)
    monkeypatch.setattr(farming, "goto", traveler)
    monkeypatch.setattr(farming, "_till_and_plant_tile", planter)


def test_planting_approaches_crop_height_with_a_close_bounded_goal(monkeypatch):
    moves = []
    setup(monkeypatch, lambda _c, *p, **kw: moves.append((p, kw)) or True,
          lambda *_a: True)
    assert plant_farm_tiles(object(), [(1, 64, 2)]) == 1
    assert moves == [((1, 65, 2), {"timeout": 15, "tolerance": 1.5, "radius": 1})]


def test_unreachable_tile_is_not_planted_and_later_tiles_continue(monkeypatch):
    planted = []
    setup(monkeypatch, lambda _c, x, *_a, **_k: x != 1,
          lambda _c, *p: planted.append(p) or True)
    assert plant_farm_tiles(object(), [(1, 64, 0), (2, 64, 0)]) == 1
    assert planted == [(2, 64, 0)]


def test_visibility_failure_does_not_abort_the_remaining_plot(monkeypatch):
    attempts = []

    def plant(_c, x, y, z):
        attempts.append((x, y, z))
        if x == 1:
            raise RuntimeError("Target is not visible on a real block ray")
        return True

    setup(monkeypatch, lambda *_a, **_k: True, plant)
    assert plant_farm_tiles(object(), [(1, 64, 0), (2, 64, 0)]) == 1
    assert len(attempts) == 2


@pytest.mark.parametrize("error", [PlayerDeathDetected, SurvivalRecoveryRequired])
def test_survival_abort_propagates_without_planting_more_tiles(monkeypatch, error):
    attempts = []

    def plant(*_a):
        attempts.append(1)
        raise error("survival abort")

    setup(monkeypatch, lambda *_a, **_k: True, plant)
    with pytest.raises(error):
        plant_farm_tiles(object(), [(1, 64, 0), (2, 64, 0)])
    assert attempts == [1]


def test_seed_exhaustion_stops_without_another_trip(monkeypatch):
    setup(monkeypatch, lambda *_a, **_k: pytest.fail("no seed, no trip"),
          lambda *_a: pytest.fail("no seed, no planting"))
    monkeypatch.setattr(farming, "count_item", lambda *_a: 0)
    assert plant_farm_tiles(object(), [(1, 64, 0)]) == 0


def test_rejected_planting_does_not_credit_output(monkeypatch):
    setup(monkeypatch, lambda *_a, **_k: True, lambda *_a: False)
    assert plant_farm_tiles(object(), [(1, 64, 0)]) == 0
