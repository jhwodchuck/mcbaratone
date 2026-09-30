from types import SimpleNamespace

import pytest

from baritone_client.common import base, combat, combat_melee, emergency_food
from baritone_client.common.combat_intent import CombatIntent, combat_intent
from baritone_client.common.tasks import PlayerDeathDetected


@pytest.mark.parametrize("family,allowed", [("cow", True), ("pig", True), ("piglin", False), ("zombie", False)])
def test_starving_food_hunt_keeps_hostile_hunger_guard(monkeypatch, family, allowed):
    target = {"id": 7, "type": f"minecraft:{family}", "health": 0, "distance": 3}
    snapshot = {"player": {"health": 20, "food_level": 6}, "entities": [target]}
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a: {}))
    monkeypatch.setattr(combat, "_get_combat_snapshot", lambda *_a, **_k: snapshot)
    monkeypatch.setattr(combat, "_submerged_too_long", lambda *_a, **_k: False)
    assert combat.safe_combat(client, 7, purpose="emergency_food", target_metadata=target) is allowed


def test_normal_combat_still_retreats_when_starving(monkeypatch):
    target = {"id": 7, "type": "minecraft:cow", "health": 0, "distance": 3}
    snapshot = {"player": {"health": 20, "food_level": 6}, "entities": [target]}
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a: {}))
    monkeypatch.setattr(combat, "_get_combat_snapshot", lambda *_a, **_k: snapshot)
    assert not combat.safe_combat(client, 7, target_metadata=target)


def test_food_approach_allows_hunger_but_still_aborts_on_hostile(monkeypatch):
    target = {"id": 7, "type": "minecraft:cow", "health": 10, "distance": 40}
    hostile = {"id": 8, "type": "minecraft:zombie", "distance": 4, "position": {"x": 4, "y": 64, "z": 0}}
    state = {"health": 20, "food_level": 6, "block_position": {"x": 0, "y": 64, "z": 0}}
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a: state))
    entities = [target]
    monkeypatch.setattr(combat, "get_nearby_entities", lambda *_a, **_k: entities)
    intervention = {"reason": None}
    kwargs = dict(target_id=7, retreat_health=12, tracking_radius=64, no_retreat=False,
                  abort_on_other_hostiles=True, intervention=intervention,
                  shielding={"active": False}, shield={"ready": False})
    with combat_intent(client, CombatIntent.for_target(7, purpose="emergency_food", target_type="cow")):
        assert not combat_melee._supervise_approach(client, **kwargs)
        entities.append(hostile)
        assert combat_melee._supervise_approach(client, **kwargs)
    assert intervention["reason"] == "secondary_hostile"


def test_emergency_hunt_tracks_selected_animal_and_reserves_loot_space(monkeypatch):
    from baritone_client.common import inventory, resources, navigation

    target = {"id": 7, "type": "minecraft:cow", "distance": 58, "position": {"x": 50, "y": 70, "z": 0}}
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a: {}))
    slots = {"free": 0}
    calls = []
    monkeypatch.setattr(inventory, "free_inventory_slots", lambda _c: slots["free"])
    def clear(_c, minimum_free_slots):
        calls.append(("space", minimum_free_slots))
        slots["free"] = minimum_free_slots
        return True
    monkeypatch.setattr(resources, "manage_inventory", clear)
    monkeypatch.setattr(combat, "safe_combat", lambda _c, _id, **kw: calls.append(("combat", kw)) or True)
    monkeypatch.setattr(navigation, "recovery_goto", lambda *_a, **_k: True)
    monkeypatch.setattr(combat, "heal_if_needed", lambda *_a, **_k: True)
    monkeypatch.setattr(combat, "recover_health", lambda *_a, **_k: True)
    monkeypatch.setattr(emergency_food.time, "sleep", lambda *_a: None)
    assert emergency_food.hunt_target(client, target, minimum_health=12, recovery_complete=lambda _s: True)
    assert calls[0] == ("space", 2)
    assert calls[1][1]["tracking_radius"] >= 64
    assert calls[1][1]["purpose"] == "emergency_food"
    assert calls[1][1]["target_metadata"] == target


def test_emergency_hunt_refuses_to_kill_without_loot_space(monkeypatch):
    from baritone_client.common import inventory, resources

    monkeypatch.setattr(inventory, "free_inventory_slots", lambda _c: 0)
    monkeypatch.setattr(resources, "manage_inventory", lambda *_a, **_k: False)
    monkeypatch.setattr(combat, "safe_combat", lambda *_a, **_k: pytest.fail("no space for drops"))
    assert emergency_food.hunt_target(SimpleNamespace(), {"id": 7, "position": {"x": 4, "y": 64, "z": 0}}, minimum_health=12, recovery_complete=lambda _s: False) is False


def test_placement_propagates_harness_death_without_fallback(monkeypatch):
    from baritone_client.common import harness_ops

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=lambda *_a: {"is_dead": False, "health": 20}))
    monkeypatch.setattr(harness_ops, "available", lambda: True)
    def died(*_a):
        raise PlayerDeathDetected("dead mid-placement")
    monkeypatch.setattr(harness_ops, "place_block_exact", died)
    monkeypatch.setattr(base, "select_item", lambda *_a, **_k: pytest.fail("must yield to recovery"))
    with pytest.raises(PlayerDeathDetected):
        base.robust_place(client, 1, 64, 1, "minecraft:dirt")


def test_support_placement_stops_before_mutating_when_dead():
    def dispatch(route, _payload):
        assert route == "get_state", "dead player must not place support blocks"
        return {"is_dead": True, "health": 0}
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    with pytest.raises(PlayerDeathDetected):
        base.safe_place_block(client, 1, 64, 1, block_id="minecraft:dirt")
