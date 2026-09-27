"""Food recovery covers the band where a wounded bot can neither heal nor work."""

from types import SimpleNamespace

import pytest

from baritone_client.automator import food_opportunity
from baritone_client.automator.local_opportunity import OpportunityKind


@pytest.mark.parametrize(
    ("food", "health", "offered"),
    [
        (17, 15.4, True),   # live A1: cannot regenerate, cannot work
        (17, 16.0, False),  # healthy enough for ordinary local work
        (18, 12.0, False),  # regenerating on its own
        (10, 20.0, True),   # ordinary hunger recovery still applies
    ],
)
def test_recovery_is_offered_in_the_regeneration_gap(food, health, offered):
    opportunity = food_opportunity.select_food_recovery_opportunity(food, True, health)
    assert (opportunity is not None) is offered
    if offered:
        assert opportunity.kind is OpportunityKind.FOOD_RECOVERY


def test_existing_two_argument_callers_are_unchanged():
    assert food_opportunity.select_food_recovery_opportunity(17, True) is None
    assert food_opportunity.select_food_recovery_opportunity(10, True) is not None


def _client(food, health):
    live = {"food_level": food, "health": health, "block_position": {"x": 0, "y": 64, "z": 0}}
    return SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a, **_k: dict(live))), live


def test_recovery_run_targets_the_regen_floor_in_the_gap(monkeypatch):
    client, live = _client(17, 15.4)
    targets = []
    monkeypatch.setattr(food_opportunity, "recover_food_from_known_sources", lambda *_a: False)

    def emergency(_client, **kwargs):
        targets.append(kwargs["minimum_food"])
        live["food_level"] = 20
        return True

    monkeypatch.setattr(food_opportunity, "acquire_emergency_food", emergency)

    success, detail, before, after = food_opportunity.run_scheduled_food_recovery(
        client, SimpleNamespace(custom_data={})
    )

    assert targets == [18]
    assert success and (before, after) == (17, 20)


def test_recovery_run_still_returns_early_when_healthy(monkeypatch):
    client, _live = _client(17, 20.0)
    monkeypatch.setattr(
        food_opportunity,
        "acquire_emergency_food",
        lambda *_a, **_k: pytest.fail("no search needed while regenerating is not required"),
    )

    success, detail, _b, _a = food_opportunity.run_scheduled_food_recovery(
        client, SimpleNamespace(custom_data={})
    )

    assert success and "already meets" in detail
