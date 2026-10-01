from dataclasses import replace

import pytest

from baritone_client.common import sheltered_alert, survival_farm
from baritone_client.common.defense import AttackStyle, DefenseDecision, DefenseMode, ThreatAssessment
from baritone_client.common.tasks import PlayerDeathDetected


@pytest.fixture
def alert(monkeypatch):
    entity = {"can_see_player": False, "is_aggressive": True, "target_id": 7,
              "position": {"x": 20, "y": 65, "z": 0},
              "velocity": {"x": 0, "y": 0, "z": 0}}
    threat = ThreatAssessment(entity, "skeleton", 15, 0, 80, AttackStyle.RANGED, False)
    decision = DefenseDecision(DefenseMode.EVADE,
                              "threat stalled in alert range too long; forcing evasion", threat)
    state = {"health": 20, "is_dead": False, "on_fire": False, "position": {"x": 5, "y": 65, "z": 0},
             "velocity": {"x": 0, "y": 0, "z": 0}, "entity_id": 7}
    monkeypatch.setattr(survival_farm, "_enclosure_bounds", lambda *_a, **_k: (0, 8, 65, -3, 3))
    return decision, state


def test_aggressive_occluded_distant_alert_keeps_verified_shelter(alert):
    decision, state = alert
    held = sheltered_alert.hold_sheltered_alert(object(), decision, state)
    assert held.mode is DefenseMode.ALERT
    assert held.primary is decision.primary


@pytest.mark.parametrize("changes", [
    {"distance": 9}, {"closing_speed": .2}, {"style": AttackStyle.EXPLOSIVE},
    {"style": AttackStyle.BOSS}, {"entity_type": "vex"}, {"always_evade": True},
    {"distance": float("nan")},
])
def test_actionable_or_unknown_threat_keeps_evasion(alert, changes):
    decision, state = alert
    decision = replace(decision, primary=replace(decision.primary, **changes))
    assert sheltered_alert.hold_sheltered_alert(object(), decision, state) is decision


@pytest.mark.parametrize("visible", [True, None])
def test_visible_or_unknown_sightline_keeps_evasion(alert, visible):
    decision, state = alert
    decision.primary.entity["can_see_player"] = visible
    assert sheltered_alert.hold_sheltered_alert(object(), decision, state) is decision


def test_missing_velocity_keeps_evasion(alert):
    decision, state = alert
    decision.primary.entity.pop("velocity")
    assert sheltered_alert.hold_sheltered_alert(object(), decision, state) is decision


@pytest.mark.parametrize("changes", [
    {"health": float("nan")}, {"health": None}, {"health": 11},
    {"is_dead": None}, {"on_fire": None}, {"on_fire": True},
])
def test_unknown_or_unsafe_player_keeps_evasion(alert, changes):
    decision, state = alert
    state.update(changes)
    assert sheltered_alert.hold_sheltered_alert(object(), decision, state) is decision


def test_multiple_threats_keep_evasion(alert):
    decision, state = alert
    projectile = replace(decision.primary, entity_type="arrow", always_evade=True)
    assert sheltered_alert.hold_sheltered_alert(
        object(), decision, state, [decision.primary, projectile],
    ) is decision


def test_open_or_unknown_enclosure_keeps_evasion(alert, monkeypatch):
    decision, state = alert
    monkeypatch.setattr(survival_farm, "_enclosure_bounds", lambda *_a, **_k: False)
    assert sheltered_alert.hold_sheltered_alert(object(), decision, state) is decision


def test_other_evasion_reason_is_untouched(alert):
    decision, state = alert
    decision = replace(decision, reason="critical health with nearby threat")
    assert sheltered_alert.hold_sheltered_alert(object(), decision, state) is decision


def test_enclosure_query_error_keeps_evasion(alert, monkeypatch):
    decision, state = alert
    monkeypatch.setattr(survival_farm, "_enclosure_bounds", lambda *_a, **_k:
                        (_ for _ in ()).throw(RuntimeError("unknown blocks")))
    assert sheltered_alert.hold_sheltered_alert(object(), decision, state) is decision


def test_enclosure_death_propagates(alert, monkeypatch):
    decision, state = alert
    monkeypatch.setattr(survival_farm, "_enclosure_bounds", lambda *_a, **_k:
                        (_ for _ in ()).throw(PlayerDeathDetected("dead")))
    with pytest.raises(PlayerDeathDetected):
        sheltered_alert.hold_sheltered_alert(object(), decision, state)
