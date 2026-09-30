"""Scheduled hunger recovery must not walk away from a usable local farm."""

from types import SimpleNamespace

import pytest

from baritone_client.automator import food_opportunity
from baritone_client.common import survival_farm
from baritone_client.common.tasks import PlayerDeathDetected


def setup_recovery(monkeypatch, food=11):
    live = {"food_level": food, "health": 20}
    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=lambda *_a, **_k: dict(live))
    )
    calls = []
    monkeypatch.setattr(food_opportunity, "recover_food_from_known_sources",
                        lambda *_a: calls.append("storage") or False)
    monkeypatch.setattr(food_opportunity, "acquire_emergency_food",
                        lambda *_a, **_k: calls.append("search") or False)
    return client, SimpleNamespace(custom_data={}), live, calls


@pytest.mark.parametrize("claimed", [True, False])
def test_local_farm_restores_hunger_before_storage_or_search(monkeypatch, claimed):
    client, state, live, calls = setup_recovery(monkeypatch)

    def tend(*_args):
        calls.append("farm")
        live["food_level"] = 16
        return claimed

    monkeypatch.setattr(survival_farm, "tend_local_farm_for_food", tend)
    result = food_opportunity.run_scheduled_food_recovery(client, state)
    assert result[0] and result[2:] == (11, 16)
    assert calls == ["farm"]


@pytest.mark.parametrize("claimed", [True, False])
def test_local_farm_result_without_live_hunger_gain_is_not_success(monkeypatch, claimed):
    client, state, _live, calls = setup_recovery(monkeypatch)
    monkeypatch.setattr(survival_farm, "tend_local_farm_for_food",
                        lambda *_a: calls.append("farm") or claimed)
    result = food_opportunity.run_scheduled_food_recovery(client, state)
    assert not result[0]
    assert calls == ["farm", "storage", "search"]


@pytest.mark.parametrize("error", [RuntimeError("unavailable"), PlayerDeathDetected("dead")])
def test_local_farm_errors_never_start_a_fallback_expedition(monkeypatch, error):
    client, state, _live, calls = setup_recovery(monkeypatch)

    def tend(*_args):
        raise error

    monkeypatch.setattr(survival_farm, "tend_local_farm_for_food", tend)
    with pytest.raises(type(error)):
        food_opportunity.run_scheduled_food_recovery(client, state)
    assert calls == []


def test_healthy_player_does_not_trigger_farm_work(monkeypatch):
    client, state, _live, calls = setup_recovery(monkeypatch, food=20)
    monkeypatch.setattr(survival_farm, "tend_local_farm_for_food",
                        lambda *_a: pytest.fail("healthy player needs no farm recovery"))
    assert food_opportunity.run_scheduled_food_recovery(client, state)[0]
    assert calls == []
