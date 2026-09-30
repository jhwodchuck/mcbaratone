from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from baritone_client.common import combat, combat_melee, cornered_defense
from baritone_client.common.stationary_defense import (
    fight_without_pursuit,
    stationary_defense_active,
)
from baritone_client.common.tasks import PlayerDeathDetected


def test_restriction_is_nested_client_and_thread_local():
    client, other = object(), object()

    def inner(value, _target):
        assert stationary_defense_active(client)
        assert stationary_defense_active(value)
        return True

    def outer(value, _target):
        assert stationary_defense_active(value)
        assert not stationary_defense_active(other)
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert not executor.submit(stationary_defense_active, value).result()
        assert fight_without_pursuit(other, inner, {})
        assert not stationary_defense_active(other)
        assert stationary_defense_active(value)
        return True

    assert fight_without_pursuit(client, outer, {})
    assert not stationary_defense_active(client)


@pytest.mark.parametrize("error", [RuntimeError("failed"), PlayerDeathDetected("dead")])
def test_restriction_restored_and_errors_propagate(error):
    client = object()

    def fail(value, _target):
        assert stationary_defense_active(value)
        raise error

    with pytest.raises(type(error)):
        fight_without_pursuit(client, fail, {})
    assert not stationary_defense_active(client)


def combat_fixture(monkeypatch, distance=8.0, mob="zombie"):
    calls = []
    target = {
        "id": 42, "type": f"minecraft:{mob}", "distance": distance,
        "health": 20, "position": {"x": distance, "y": 64, "z": 0},
    }
    state = {"health": 20, "food_level": 20, "armor_count": 0,
             "attack_cooldown": 1, "is_pathing": False,
             "block_position": {"x": 0, "y": 64, "z": 0}}
    client = SimpleNamespace(transport=SimpleNamespace(
        dispatch=lambda route, payload: calls.append((route, payload)) or {}
    ))
    monkeypatch.setattr(combat, "_get_combat_snapshot", lambda *_a, **_k: {
        "player": state, "entities": [target],
    })
    monkeypatch.setattr(combat, "equip_best_weapon", lambda *_a: True)
    monkeypatch.setattr(combat_melee, "_prepare_combat_shield", lambda *_a: False)
    monkeypatch.setattr(combat.time, "sleep", lambda _seconds: None)

    def approach(*_args, **_kwargs):
        calls.append(("approach", {}))
        target["distance"] = 3
        return True

    def strike(*_args, **_kwargs):
        calls.append(("strike", {}))
        target["health"] = 0
        return {"attacked": True}

    monkeypatch.setattr(combat, "goto", approach)
    monkeypatch.setattr(combat_melee, "execute_melee_strike", strike)
    return client, target, calls


@pytest.mark.parametrize("distance", [4.5, 6.0, 14.0, float("nan"), float("inf")])
def test_cornered_fight_refuses_pursuit(monkeypatch, distance):
    client, target, calls = combat_fixture(monkeypatch, distance)
    events = []
    monkeypatch.setattr(combat.combat_telemetry, "emit_event", lambda event, **fields:
                        events.append({"event": event, **fields}))
    assert not fight_without_pursuit(
        client, combat.safe_combat, target["id"], no_retreat=True, max_duration=1
    )
    assert not any(route in {"approach", "strike"} for route, _ in calls)
    assert ("cancel", {}) in calls
    assert any(event["event"] == "combat_end"
               and event.get("reason") == "cornered_pursuit_refused"
               for event in events)


def test_adjacent_cornered_attacker_can_still_be_struck(monkeypatch):
    client, target, calls = combat_fixture(monkeypatch, 3.0)
    assert fight_without_pursuit(
        client, combat.safe_combat, target["id"], no_retreat=True, max_duration=1
    )
    assert ("strike", {}) in calls
    assert not any(route == "approach" for route, _ in calls)


def test_ordinary_combat_can_still_approach(monkeypatch):
    client, target, calls = combat_fixture(monkeypatch)
    assert combat.safe_combat(client, target["id"], max_duration=1)
    assert ("approach", {}) in calls
    assert ("strike", {}) in calls


@pytest.mark.parametrize("carried_weapon", [False, True])
def test_stationary_ranged_defense_never_approaches(monkeypatch, carried_weapon):
    from baritone_client.common import combat_ranged

    client, target, calls = combat_fixture(monkeypatch, 14.0, "skeleton")
    fired = []

    def fire(*_args):
        fired.append(True)
        if carried_weapon:
            target["health"] = 0
        return carried_weapon

    monkeypatch.setattr(combat_ranged, "fire_best_ranged_attack", fire)
    monkeypatch.setattr(combat_ranged, "ranged_attack_in_flight", lambda *_a: False)
    assert fight_without_pursuit(
        client, combat.safe_combat, target["id"], no_retreat=True, max_duration=1
    ) is carried_weapon
    assert fired == [True]
    assert not any(route in {"approach", "strike"} for route, _ in calls)


@pytest.mark.parametrize("reason", ["no_safe_endpoint", "navigation_failed"])
def test_failed_escape_paths_never_approach(monkeypatch, reason):
    client, _target, calls = combat_fixture(monkeypatch)

    def no_escape(value, _threat):
        value._last_escape_failure_reason = reason
        return False

    monkeypatch.setattr(combat, "run_away", no_escape)
    assert combat.defend_or_flee(client)
    assert combat.defend_or_flee(client)
    assert not any(route in {"approach", "strike"} for route, _ in calls)


def test_alternative_attacker_after_failed_relocation_is_stationary(monkeypatch):
    client, target, _calls = combat_fixture(monkeypatch, 6.0)
    creeper = {**target, "id": 7, "type": "minecraft:creeper", "distance": 4}
    assessments = combat.assess_threats([creeper, target], {"health": 20})
    primary = next(item for item in assessments if item.entity["id"] == 7)
    runtime = combat.DefenseRuntime()
    seen = []

    def fight(value, _target, **_kwargs):
        seen.append(stationary_defense_active(value))
        return False

    assert cornered_defense.handle_non_engageable_escape_failure(
        client, primary, assessments, runtime,
        relocate=lambda *_a: False, fight=fight,
    )
    assert seen == [True]
    assert not stationary_defense_active(client)
