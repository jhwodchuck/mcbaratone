from types import SimpleNamespace

import pytest

from baritone_client.common import combat
from baritone_client.common.tasks import PlayerDeathDetected


class CombatTransport:
    def __init__(self, health=20.0):
        self.health = health
        self.calls = []

    def dispatch(self, route, payload):
        self.calls.append((route, payload))
        if route == "get_state":
            return {"health": self.health, "block_position": {"x": 0, "y": 64, "z": 0}}
        return {}


def test_low_health_defense_never_reengages_combat(monkeypatch):
    transport = CombatTransport(health=5.0)
    client = SimpleNamespace(transport=transport)
    threat = {
        "id": 9,
        "type": "minecraft:skeleton",
        "distance": 5.0,
        "position": {"x": 5, "y": 64, "z": 0},
    }
    attacked = []
    fled = []
    monkeypatch.setattr(combat, "scan_for_threats", lambda _client: [threat])
    monkeypatch.setattr(combat, "heal_if_needed", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(combat, "safe_combat", lambda *_args, **_kwargs: attacked.append(True))
    monkeypatch.setattr(combat, "run_away", lambda _client, target: fled.append(target))
    monkeypatch.setattr(combat.time, "sleep", lambda _seconds: None)

    assert combat.defend_or_flee(client)
    assert not attacked
    assert fled == [threat]
    assert ("chat", {"message": "#stop"}) in transport.calls


def test_skeleton_is_fled_even_at_full_health(monkeypatch):
    transport = CombatTransport(health=20.0)
    client = SimpleNamespace(transport=transport)
    threat = {
        "id": 10,
        "type": "minecraft:skeleton",
        "distance": 9.0,
        "position": {"x": 9, "y": 64, "z": 0},
    }
    attacked = []
    fled = []
    monkeypatch.setattr(combat, "scan_for_threats", lambda _client: [threat])
    monkeypatch.setattr(combat, "safe_combat", lambda *_args, **_kwargs: attacked.append(True))
    monkeypatch.setattr(combat, "run_away", lambda _client, target: fled.append(target))
    monkeypatch.setattr(combat.time, "sleep", lambda _seconds: None)

    assert combat.defend_or_flee(client)
    assert not attacked
    assert fled == [threat]
    assert ("cancel", {}) in transport.calls


def test_unarmored_player_flees_zombie_instead_of_starting_melee(monkeypatch):
    transport = CombatTransport(health=20.0)
    client = SimpleNamespace(transport=transport)
    threat = {
        "id": 11,
        "type": "minecraft:zombie",
        "distance": 6.0,
        "position": {"x": 6, "y": 64, "z": 0},
    }
    attacked = []
    fled = []
    monkeypatch.setattr(combat, "scan_for_threats", lambda _client: [threat])
    monkeypatch.setattr(combat, "get_equipped_armor", lambda _client: {})
    monkeypatch.setattr(combat, "safe_combat", lambda *_args, **_kwargs: attacked.append(True))
    monkeypatch.setattr(combat, "run_away", lambda _client, target: fled.append(target))
    monkeypatch.setattr(combat.time, "sleep", lambda _seconds: None)

    assert combat.defend_or_flee(client)
    assert not attacked
    assert fled == [threat]


def test_recover_health_refuses_work_when_no_food(monkeypatch):
    transport = CombatTransport(health=5.0)
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(combat, "heal_if_needed", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(combat, "_emergency_food_count", lambda _client: 0)

    assert not combat.recover_health(client, minimum_health=12.0, timeout=1.0)
    assert ("cancel", {}) in transport.calls


def test_dead_player_yields_without_local_respawn():
    transport = CombatTransport(health=0.0)
    client = SimpleNamespace(transport=transport)

    with pytest.raises(PlayerDeathDetected):
        combat.defend_or_flee(client)

    assert not any(route == "respawn" for route, _payload in transport.calls)


def test_eat_until_hunger_uses_carried_emergency_food(monkeypatch):
    class HungerTransport(CombatTransport):
        def __init__(self):
            super().__init__(health=20.0)
            self.food_level = 6

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                return {"health": 20.0, "food_level": self.food_level}
            if route == "use_item":
                self.food_level = min(20, self.food_level + 4)
            return {}

    transport = HungerTransport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(
        combat,
        "count_item",
        lambda _client, item_id: 7 if item_id == "minecraft:rotten_flesh" else 0,
    )
    monkeypatch.setattr(combat, "select_item", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(combat.time, "sleep", lambda _seconds: None)

    assert combat.eat_until_hunger(client, minimum_food=14)
    uses = [call for call in transport.calls if call[0] == "use_item"]
    assert len(uses) == 2


def test_emergency_food_list_uses_current_raw_meat_item_ids():
    assert "minecraft:beef" in combat.EMERGENCY_FOOD_ITEMS
    assert "minecraft:porkchop" in combat.EMERGENCY_FOOD_ITEMS
    assert "minecraft:raw_beef" not in combat.EMERGENCY_FOOD_ITEMS
    assert "minecraft:raw_porkchop" not in combat.EMERGENCY_FOOD_ITEMS


def test_emergency_food_hunts_passive_target_then_recovers(monkeypatch):
    transport = CombatTransport(health=5.0)
    client = SimpleNamespace(transport=transport)
    sheep = {
        "id": 12,
        "type": "minecraft:sheep",
        "distance": 8.0,
        "position": {"x": 8, "y": 64, "z": 0},
    }
    recovery_results = iter((False, True))
    hunted = []

    def recover(*_args, **_kwargs):
        recovered = next(recovery_results)
        if recovered:
            transport.health = 12.0
        return recovered

    monkeypatch.setattr(combat, "recover_health", recover)
    monkeypatch.setattr(combat, "scan_for_threats", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(combat, "find_entity_by_type", lambda *_args, **_kwargs: sheep)
    monkeypatch.setattr(combat, "safe_combat", lambda *_args, **_kwargs: hunted.append(True) or True)
    monkeypatch.setattr(combat, "heal_if_needed", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(combat.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr("baritone_client.common.navigation.goto", lambda *_args, **_kwargs: True)

    assert combat.acquire_emergency_food(client, minimum_health=12.0)
    assert hunted == [True]


def test_emergency_food_explores_until_passive_target_loads(monkeypatch):
    transport = CombatTransport(health=5.0)
    client = SimpleNamespace(transport=transport)
    cow = {
        "id": 13,
        "type": "minecraft:cow",
        "distance": 10.0,
        "position": {"x": 10, "y": 64, "z": 0},
    }
    recovery_results = iter((False, True))
    scans = iter((None, None, cow))
    scanned_groups = []
    hunted = []
    def recover(*_args, **_kwargs):
        recovered = next(recovery_results)
        if recovered:
            transport.health = 12.0
        return recovered

    monkeypatch.setattr(combat, "recover_health", recover)
    monkeypatch.setattr(combat, "scan_for_threats", lambda *_args, **_kwargs: [])
    def find_target(_client, entity_types, **_kwargs):
        scanned_groups.append(tuple(entity_types))
        return next(scans)

    monkeypatch.setattr(combat, "find_entity_by_type", find_target)
    monkeypatch.setattr(combat, "safe_combat", lambda *_args, **_kwargs: hunted.append(True) or True)
    monkeypatch.setattr(combat, "heal_if_needed", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(combat.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr("baritone_client.common.navigation.goto", lambda *_args, **_kwargs: True)

    assert combat.acquire_emergency_food(client, minimum_health=12.0)
    assert hunted == [True]
    assert ("explore", {"x": 0, "z": 0}) in transport.calls
    assert ("chat", {"message": "#stop"}) in transport.calls
    assert ("salmon", "cod") not in scanned_groups


def test_emergency_food_does_not_treat_full_health_low_hunger_as_recovered(monkeypatch):
    class HungryTransport(CombatTransport):
        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                return {
                    "health": 20.0,
                    "food_level": 12,
                    "world_time": 1000,
                    "block_position": {"x": 0, "y": 64, "z": 0},
                }
            return {}

    client = SimpleNamespace(transport=HungryTransport(health=20.0))
    monkeypatch.setattr(combat, "recover_health", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(combat, "scan_for_threats", lambda *_args, **_kwargs: [])

    class ReachedFoodSearch(Exception):
        pass

    def reach_food_search(*_args, **_kwargs):
        raise ReachedFoodSearch()

    monkeypatch.setattr(combat, "find_entity_by_type", reach_food_search)

    with pytest.raises(ReachedFoodSearch):
        combat.acquire_emergency_food(
            client,
            minimum_health=12.0,
            minimum_food=14,
        )


def test_secure_recovery_area_requires_sustained_daylight_clearance(monkeypatch):
    transport = CombatTransport(health=20.0)
    client = SimpleNamespace(transport=transport)
    clock = iter((0.0, 0.0, 2.0, 6.0, 8.0))
    monkeypatch.setattr(combat, "scan_for_threats", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(combat.time, "time", lambda: next(clock))
    monkeypatch.setattr(combat.time, "sleep", lambda _seconds: None)

    assert combat.secure_recovery_area(
        client, timeout=90.0, clear_seconds=5.0, radius=16
    )


def test_secure_recovery_area_keeps_defense_attached_to_threat(monkeypatch):
    transport = CombatTransport(health=20.0)
    client = SimpleNamespace(transport=transport)
    threat = {"id": 7, "type": "minecraft:zombie", "distance": 4.0}
    scans = iter(([threat], [], []))
    defended = []
    clock = iter((0.0, 0.0, 1.0, 3.0, 5.0, 6.0))
    monkeypatch.setattr(combat, "scan_for_threats", lambda *_args, **_kwargs: next(scans))
    monkeypatch.setattr(combat, "defend_or_flee", lambda _client: defended.append(True) or True)
    monkeypatch.setattr(combat.time, "time", lambda: next(clock))
    monkeypatch.setattr(combat.time, "sleep", lambda _seconds: None)

    assert combat.secure_recovery_area(
        client, timeout=90.0, clear_seconds=2.0, radius=16
    )
    assert defended == [True]


def test_combat_treats_vanished_target_as_finished(monkeypatch):
    target = {
        "id": 42,
        "type": "minecraft:zombie",
        "distance": 3.0,
        "position": {"x": 3, "y": 64, "z": 0},
    }

    class VanishingTransport(CombatTransport):
        def dispatch(self, route, payload):
            if route == "attack_entity":
                raise RuntimeError("Entity not found: 42")
            return super().dispatch(route, payload)

    client = SimpleNamespace(transport=VanishingTransport())
    monkeypatch.setattr(combat, "get_nearby_entities", lambda *_args, **_kwargs: [target])
    monkeypatch.setattr(combat, "equip_best_weapon", lambda _client: True)
    monkeypatch.setattr(combat, "look_at_entity", lambda *_args: True)

    assert combat.safe_combat(client, 42, max_duration=1)


def test_melee_waits_for_attack_cooldown_before_dispatch(monkeypatch):
    target = {
        "id": 42,
        "type": "minecraft:zombie",
        "distance": 3.0,
        "position": {"x": 3, "y": 64, "z": 0},
    }
    snapshots = iter(
        (
            {
                "player": {"health": 20, "attack_cooldown": 0.4},
                "entities": [target],
            },
            {
                "player": {"health": 20, "attack_cooldown": 0.95},
                "entities": [target],
            },
            {
                "player": {"health": 20, "attack_cooldown": 0.1},
                "entities": [],
            },
        )
    )
    transport = CombatTransport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(
        combat,
        "_get_combat_snapshot",
        lambda *_args, **_kwargs: next(snapshots),
    )
    monkeypatch.setattr(combat, "equip_best_weapon", lambda _client: True)
    monkeypatch.setattr(combat, "look_at_entity", lambda *_args: True)
    monkeypatch.setattr(combat.time, "sleep", lambda _seconds: None)

    assert combat.safe_combat(client, target["id"], max_duration=1)
    attacks = [payload for route, payload in transport.calls if route == "attack_entity"]
    assert attacks == [
        {
            "entity_id": target["id"],
            "min_cooldown": combat.MELEE_ATTACK_COOLDOWN_THRESHOLD,
        }
    ]


def test_combat_aborts_for_secondary_hostile_from_canonical_policy(monkeypatch):
    target = {
        "id": 42,
        "type": "minecraft:zombie",
        "distance": 3.0,
        "position": {"x": 3, "y": 64, "z": 0},
    }
    drowned = {
        "id": 7,
        "type": "minecraft:drowned",
        "distance": 8.0,
        "position": {"x": 0, "y": 64, "z": 8},
    }
    snapshot = {
        "player": {
            "health": 20,
            "attack_cooldown": 1.0,
            "block_position": {"x": 0, "y": 64, "z": 0},
        },
        "entities": [target, drowned],
    }
    transport = CombatTransport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(combat, "_get_combat_snapshot", lambda *_args, **_kwargs: snapshot)
    monkeypatch.setattr(combat, "equip_best_weapon", lambda _client: True)

    assert not combat.safe_combat(
        client,
        target["id"],
        max_duration=1,
        abort_on_other_hostiles=True,
    )
    assert not any(route == "attack_entity" for route, _payload in transport.calls)
    assert ("cancel", {}) in transport.calls


def test_passive_hunt_aborts_if_another_hostile_enters_radius(monkeypatch):
    pig = {
        "id": 42,
        "type": "minecraft:pig",
        "distance": 6.0,
        "position": {"x": 6, "y": 64, "z": 0},
    }
    creeper = {
        "id": 7,
        "type": "minecraft:creeper",
        "distance": 8.0,
        "position": {"x": 0, "y": 64, "z": 8},
    }
    transport = CombatTransport(health=20.0)
    client = SimpleNamespace(transport=transport)
    attacked = []
    monkeypatch.setattr(combat, "get_nearby_entities", lambda *_args, **_kwargs: [pig, creeper])
    monkeypatch.setattr(combat, "equip_best_weapon", lambda _client: True)
    monkeypatch.setattr(
        combat,
        "look_at_entity",
        lambda *_args: attacked.append(True) or True,
    )

    assert not combat.safe_combat(
        client,
        pig["id"],
        max_duration=1,
        abort_on_other_hostiles=True,
    )
    assert not attacked
    assert ("cancel", {}) in transport.calls


def test_far_combat_target_gets_one_bounded_navigation_goal(monkeypatch):
    target = {
        "id": 42,
        "type": "minecraft:cow",
        "distance": 49.0,
        "position": {"x": 49, "y": 64, "z": 0},
    }
    client = SimpleNamespace(transport=CombatTransport())
    navigation_calls = []
    scans = iter(([target], []))
    scan_radii = []

    def scan(_client, radius):
        scan_radii.append(radius)
        return next(scans)

    monkeypatch.setattr(combat, "get_nearby_entities", scan)
    monkeypatch.setattr(combat, "equip_best_weapon", lambda _client: True)
    monkeypatch.setattr(
        combat,
        "goto",
        lambda _client, x, y, z, **kwargs: navigation_calls.append(
            (x, y, z, kwargs)
        )
        or True,
    )
    monkeypatch.setattr(combat.time, "sleep", lambda _seconds: None)

    assert combat.safe_combat(client, 42, max_duration=40, tracking_radius=64)
    assert len(navigation_calls) == 1
    assert navigation_calls[0][:3] == (49, 64, 0)
    assert navigation_calls[0][3]["tolerance"] == 3.0
    assert scan_radii == [64, 64]


def test_hunt_steps_onto_kill_position_before_rechecking_loot(monkeypatch):
    target = {
        "id": 42,
        "type": "minecraft:cow",
        "distance": 3.0,
        "position": {"x": 8.8, "y": 64.0, "z": -2.2},
    }
    client = SimpleNamespace(transport=CombatTransport())
    leather = {"count": 0}
    loot_goals = []
    monkeypatch.setattr(
        combat,
        "count_item",
        lambda _client, item_id: leather["count"]
        if item_id == "minecraft:leather"
        else 0,
    )
    monkeypatch.setattr(combat, "heal_if_needed", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(combat, "find_entity_by_type", lambda *_args, **_kwargs: target)
    monkeypatch.setattr(combat, "safe_combat", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(combat.time, "sleep", lambda _seconds: None)

    def collect(_client, x, y, z, **kwargs):
        loot_goals.append((x, y, z, kwargs))
        leather["count"] = 1
        return True

    monkeypatch.setattr(combat, "goto", collect)

    result = combat.hunt_mobs(
        client,
        ["cow"],
        {"minecraft:leather": 1},
        timeout=30,
    )
    assert result.success
    assert loot_goals == [
        (
            8,
            64,
            -2,
            {"timeout": 10, "check_interval": 0.25, "tolerance": 0.75},
        )
    ]


def test_passive_hunt_stops_at_daylight_return_boundary(monkeypatch):
    transport = CombatTransport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(combat, "count_item", lambda *_args: 0)
    monkeypatch.setattr(
        combat,
        "find_entity_by_type",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("late expedition must not select another target")
        ),
    )
    original_dispatch = transport.dispatch

    def dispatch(route, payload):
        if route == "get_state":
            return {
                "health": 20,
                "world_time": 9000,
                "block_position": {"x": 0, "y": 64, "z": 0},
            }
        return original_dispatch(route, payload)

    transport.dispatch = dispatch

    result = combat.hunt_mobs(
        client,
        ["cow"],
        {"minecraft:leather": 1},
        latest_world_time=9000,
    )
    assert not result.success
    assert ("cancel", {}) in transport.calls


def test_hunt_uses_explicit_exploration_center_when_no_targets(monkeypatch):
    transport = CombatTransport()
    client = SimpleNamespace(transport=transport)
    clock = iter((0.0, 0.0, 0.0, 31.0, 31.0))
    monkeypatch.setattr(combat.time, "time", lambda: next(clock, 31.0))
    monkeypatch.setattr(combat.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(combat, "count_item", lambda *_args: 0)
    monkeypatch.setattr(combat, "heal_if_needed", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(combat, "find_entity_by_type", lambda *_args, **_kwargs: None)

    result = combat.hunt_mobs(
        client,
        ["cow"],
        {"minecraft:leather": 1},
        timeout=30,
        exploration_center=(-6, -250),
    )
    assert not result.success
    assert (
        "explore",
        {"x": -6, "z": -250},
    ) in transport.calls
    assert (
        "chat",
        {"message": "#explore"},
    ) not in transport.calls


def test_hunt_eats_before_selecting_another_target(monkeypatch):
    """Hunting a non-food mob (e.g. blaze/enderman) with no carried food and
    no way to eat must stop rather than keep fighting hungry -- unlike a
    food-yielding hunt, killing a blaze can't resolve the hunger itself."""
    class HungryTransport(CombatTransport):
        def dispatch(self, route, payload):
            if route == "get_state":
                return {
                    "health": 20,
                    "food_level": 8,
                    "world_time": 2000,
                    "block_position": {"x": 0, "y": 64, "z": 0},
                }
            return super().dispatch(route, payload)

    client = SimpleNamespace(transport=HungryTransport())
    fed = []
    monkeypatch.setattr(combat, "count_item", lambda *_args: 0)
    monkeypatch.setattr(
        combat,
        "eat_until_hunger",
        lambda _client, minimum_food: fed.append(minimum_food) or False,
    )
    monkeypatch.setattr(
        combat,
        "find_entity_by_type",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("hunger must be handled before target selection")
        ),
    )

    result = combat.hunt_mobs(
        client,
        ["blaze"],
        {"minecraft:blaze_rod": 1},
        timeout=30,
    )
    assert not result.success
    assert fed == [14]


def test_hunt_continues_hunting_food_animal_without_carried_food(monkeypatch):
    """Hunting a food-yielding mob (cow/mooshroom/sheep/pig/chicken/rabbit)
    with no carried food must keep hunting instead of aborting: killing the
    target itself restocks food. Aborting here deadlocked forever live --
    Bot07 got stuck retrying "Gather 46 leather" with an empty supply chest
    and no food anywhere, since the leather hunt IS the only path to food."""
    class HungryTransport(CombatTransport):
        def dispatch(self, route, payload):
            if route == "get_state":
                return {
                    "health": 20,
                    "food_level": 8,
                    "world_time": 2000,
                    "block_position": {"x": 0, "y": 64, "z": 0},
                }
            return super().dispatch(route, payload)

    client = SimpleNamespace(transport=HungryTransport())
    fed = []
    monkeypatch.setattr(combat, "count_item", lambda *_args: 0)
    monkeypatch.setattr(
        combat,
        "eat_until_hunger",
        lambda _client, minimum_food: fed.append(minimum_food) or False,
    )

    class ReachedTargetSelection(Exception):
        pass

    def _reached_target_selection(*_args, **_kwargs):
        raise ReachedTargetSelection()

    monkeypatch.setattr(combat, "find_entity_by_type", _reached_target_selection)

    with pytest.raises(ReachedTargetSelection):
        combat.hunt_mobs(
            client,
            ["cow"],
            {"minecraft:leather": 1},
            timeout=30,
        )
    assert fed == [14]


def test_get_nearby_entities_raises_on_bridge_failure_when_requested():
    """A failed bridge query must be distinguishable from a genuinely empty
    area. Silently returning [] on a transport error made a route-timeout
    flood look like "no animals nearby" -- Bot07 hunted leather for minutes
    with a cow three blocks away because every get_entities call was timing
    out and being swallowed into an empty list."""
    class FailingTransport:
        def dispatch(self, route, payload):
            raise TimeoutError("Read route get_entities timed out")

    client = SimpleNamespace(transport=FailingTransport())

    # Default: best-effort, returns [] (preserves existing callers).
    assert combat.get_nearby_entities(client, 64) == []

    # Opt-in: surfaces the failure instead of masking it as empty.
    with pytest.raises(combat.EntityQueryError):
        combat.get_nearby_entities(client, 64, raise_on_error=True)


def test_hunt_pauses_scan_on_bridge_failure_instead_of_exploring(monkeypatch):
    """When the entity query fails mid-hunt, hunt_mobs must NOT conclude the
    area is empty and wander off exploring -- the mobs it wants may be right
    here; the bridge simply couldn't answer. It should pause and let the
    route-timeout-flood recovery restore the bridge. Confirmed live: Bot07
    explored for leather with a cow three blocks away during a bridge
    degradation."""
    class DegradedTransport(CombatTransport):
        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                return {
                    "health": 20,
                    "food_level": 20,
                    "world_time": 2000,
                    "block_position": {"x": 0, "y": 64, "z": 0},
                }
            return {}

    transport = DegradedTransport()
    client = SimpleNamespace(transport=transport)
    clock = iter((0.0, 0.0, 0.0, 31.0, 31.0))
    monkeypatch.setattr(combat.time, "time", lambda: next(clock, 31.0))
    monkeypatch.setattr(combat.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(combat, "count_item", lambda *_args: 0)
    monkeypatch.setattr(combat, "heal_if_needed", lambda *_args, **_kwargs: False)

    def _bridge_down(*_args, **_kwargs):
        raise combat.EntityQueryError("Read route get_entities timed out")

    monkeypatch.setattr(combat, "find_entity_by_type", _bridge_down)

    result = combat.hunt_mobs(
        client,
        ["cow"],
        {"minecraft:leather": 1},
        timeout=30,
        exploration_center=(-6, -250),
    )
    assert not result.success
    # Must not have wandered off exploring on a bridge failure.
    assert ("explore", {"x": -6, "z": -250}) not in transport.calls
    assert ("chat", {"message": "#explore"}) not in transport.calls
