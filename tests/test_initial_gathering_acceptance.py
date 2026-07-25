from types import SimpleNamespace

import pytest

from baritone_client.automator.phases import initial_gathering
from baritone_client.automator.phases.initial_gathering import InitialGatheringHandler
from baritone_client.common.tasks import SurvivalRecoveryRequired


def test_bulk_wood_targets_full_t1202_buffer(monkeypatch):
    requested = []
    monkeypatch.setattr(
        initial_gathering,
        "gather_wood",
        lambda _client, count: requested.append(count) or True,
    )

    assert InitialGatheringHandler()._gather_bulk_wood(SimpleNamespace())
    assert requested == [16]


def test_low_hunger_yields_when_bounded_recovery_fails(monkeypatch):
    transport = SimpleNamespace(
        dispatch=lambda route, _payload: {"health": 20, "food_level": 3}
    )
    monkeypatch.setattr(initial_gathering, "eat_until_hunger", lambda *_a, **_k: False)
    monkeypatch.setattr(
        initial_gathering, "acquire_emergency_food", lambda *_a, **_k: False
    )

    with pytest.raises(SurvivalRecoveryRequired):
        InitialGatheringHandler()._stabilize_gathering_hunger(
            SimpleNamespace(transport=transport)
        )
