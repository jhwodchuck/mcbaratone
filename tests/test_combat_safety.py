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


def test_eat_until_hunger_yields_death_to_top_level_recovery():
    client = SimpleNamespace(transport=CombatTransport(health=0.0))

    with pytest.raises(PlayerDeathDetected):
        combat.eat_until_hunger(client, minimum_food=14)


def test_emergency_food_list_uses_current_raw_meat_item_ids():
    assert "minecraft:beef" in combat.EMERGENCY_FOOD_ITEMS
    assert "minecraft:porkchop" in combat.EMERGENCY_FOOD_ITEMS
    assert "minecraft:tropical_fish" in combat.EMERGENCY_FOOD_ITEMS
    assert "minecraft:raw_beef" not in combat.EMERGENCY_FOOD_ITEMS
    assert "minecraft:raw_porkchop" not in combat.EMERGENCY_FOOD_ITEMS


def test_emergency_recovery_ignores_hostile_sealed_far_below_player():
    state = {"block_position": {"x": -7, "y": 72, "z": 33}}
    cave_creeper = {
        "type": "minecraft:creeper",
        "distance": 10.4,
        "position": {"x": -4, "y": 62, "z": 31},
    }
    surface_creeper = {
        "type": "minecraft:creeper",
        "distance": 10.4,
        "position": {"x": -4, "y": 70, "z": 31},
    }

    assert not combat._threat_can_reach_player(cave_creeper, state)
    assert combat._threat_can_reach_player(surface_creeper, state)


def test_aquatic_food_uses_dynamic_follow_until_melee_range(monkeypatch):
    transport = CombatTransport(health=20.0)
    client = SimpleNamespace(transport=transport)
    scans = iter(
        (
            [{"id": 22, "type": "minecraft:tropical_fish", "distance": 8.0}],
            [{"id": 22, "type": "minecraft:tropical_fish", "distance": 3.5}],
        )
    )
    monkeypatch.setattr(combat, "get_nearby_entities", lambda *_args, **_kwargs: next(scans))
    monkeypatch.setattr(combat, "scan_for_threats", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(combat.time, "sleep", lambda _seconds: None)

    assert combat._approach_aquatic_food(
        client,
        22,
        "minecraft:tropical_fish",
    )
    assert ("chat", {"message": "#follow entity tropical_fish"}) in transport.calls
    assert transport.calls[-2:] == [
        ("chat", {"message": "#stop"}),
        ("cancel", {}),
    ]


def test_aquatic_hunt_surfaces_until_head_reaches_air(monkeypatch):
    class SurfaceTransport(CombatTransport):
        def __init__(self):
            super().__init__(health=18.0)
            self.head_blocks = iter(("minecraft:water", "minecraft:air"))

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                return {
                    "health": self.health,
                    "block_position": {"x": 4, "y": 61, "z": 8},
                }
            if route == "get_block":
                return {"id": next(self.head_blocks)}
            return {}

    transport = SurfaceTransport()
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(combat.time, "sleep", lambda _seconds: None)

    assert combat._surface_after_aquatic_hunt(client)
    assert ("chat", {"message": "#surface"}) in transport.calls
    assert transport.calls[-2:] == [
        ("chat", {"message": "#stop"}),
        ("cancel", {}),
    ]


class SubmergedTransport(CombatTransport):
    def __init__(self, head_id="minecraft:water"):
        super().__init__(health=20.0)
        self.head_id = head_id

    def dispatch(self, route, payload):
        self.calls.append((route, payload))
        if route == "get_state":
            return {"health": self.health, "block_position": {"x": 0, "y": 62, "z": 0}}
        if route == "get_block":
            return {"id": self.head_id}
        return {}


def test_submersion_reflex_ignores_first_tick_then_surfaces(monkeypatch):
    client = SimpleNamespace(transport=SubmergedTransport("minecraft:water"))
    surfaced = []
    monkeypatch.setattr(
        combat,
        "_surface_after_aquatic_hunt",
        lambda _c, **_k: surfaced.append(True) or True,
    )
    state = {"block_position": {"x": 0, "y": 62, "z": 0}}

    # First fully-submerged tick is below threshold -> no surfacing yet.
    assert combat.escape_water_if_submerged(client, state) is False
    assert client._submersion_ticks == 1
    assert surfaced == []

    # Second consecutive tick reaches the threshold -> surface and reset.
    assert combat.escape_water_if_submerged(client, state) is True
    assert surfaced == [True]
    assert client._submersion_ticks == 0


def test_submersion_reflex_resets_when_head_clears(monkeypatch):
    client = SimpleNamespace(
        transport=SubmergedTransport("minecraft:air"), _submersion_ticks=5
    )
    surfaced = []
    monkeypatch.setattr(
        combat,
        "_surface_after_aquatic_hunt",
        lambda _c, **_k: surfaced.append(True) or True,
    )
    state = {"block_position": {"x": 0, "y": 70, "z": 0}}

    # Head is in air (e.g. swimming on the surface) -> reflex is a no-op.
    assert combat.escape_water_if_submerged(client, state) is False
    assert client._submersion_ticks == 0
    assert surfaced == []


def test_defend_or_flee_surfaces_before_assessing_threats(monkeypatch):
    # Pre-seed one submersion tick so this call reaches the surface threshold.
    client = SimpleNamespace(
        transport=SubmergedTransport("minecraft:water"), _submersion_ticks=1
    )
    surfaced = []
    monkeypatch.setattr(combat, "_get_combat_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(
        combat,
        "_surface_after_aquatic_hunt",
        lambda _c, **_k: surfaced.append(True) or True,
    )
    # Threat assessment must never run while the bot is drowning.
    monkeypatch.setattr(
        combat,
        "scan_for_threats",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("must surface before assessing threats")
        ),
    )

    assert combat.defend_or_flee(client) is True
    assert surfaced == [True]


def test_near_death_recovery_eventually_explores_instead_of_holding_forever(
    monkeypatch,
):
    """Regression for the live Bot09 stall: health=3.5, food=10, no threats,
    no loaded food target. must_hold_for_critical_food(critical_health_floor
    default) is True the whole time, so a caller that returned False on the
    first hold would loop this forever. The bounded hold_cycles grace period
    must fall through to the same bounded exploration a merely-hurt bot uses.
    """

    class NearDeathTransport(CombatTransport):
        def __init__(self):
            super().__init__(health=3.5)

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                return {
                    "health": self.health,
                    "food_level": 10,
                    "world_time": 1000,
                    "block_position": {"x": 0, "y": 64, "z": 0},
                }
            return {}

    client = SimpleNamespace(transport=NearDeathTransport())
    monkeypatch.setattr(combat, "recover_health", lambda *_a, **_k: False)
    monkeypatch.setattr(combat, "scan_for_threats", lambda *_a, **_k: [])
    monkeypatch.setattr(combat, "get_nearby_entities", lambda *_a, **_k: [])
    monkeypatch.setattr(combat.time, "sleep", lambda _s: None)

    class ReachedExploration(Exception):
        pass

    def explored(*_a, **_k):
        if _a and _a[0] == "explore":
            raise ReachedExploration()
        return {}

    original_dispatch = client.transport.dispatch

    def wrapped_dispatch(route, payload):
        if route == "explore":
            raise ReachedExploration()
        return original_dispatch(route, payload)

    client.transport.dispatch = wrapped_dispatch

    with pytest.raises(ReachedExploration):
        combat.acquire_emergency_food(client, minimum_health=12.0, timeout=60)


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
    renewable_sources = []

    def recover(*_args, **_kwargs):
        recovered = next(recovery_results)
        if recovered:
            transport.health = 12.0
        return recovered

    monkeypatch.setattr(combat, "recover_health", recover)
    monkeypatch.setattr(combat, "scan_for_threats", lambda *_args, **_kwargs: [])
    herd = [
        {**sheep, "id": 12 + index, "distance": 8.0 + index}
        for index in range(3)
    ]
    # health=5.0 is below _CRITICAL_HUNT_HEALTH, so the target search uses
    # the shrunk 16-block radius rather than the normal 64.
    monkeypatch.setattr(
        combat,
        "get_nearby_entities",
        lambda _client, radius: herd if radius == 16 else [],
    )
    monkeypatch.setattr(combat, "safe_combat", lambda *_args, **_kwargs: hunted.append(True) or True)
    monkeypatch.setattr(combat, "heal_if_needed", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(combat.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr("baritone_client.common.navigation.goto", lambda *_args, **_kwargs: True)

    assert combat.acquire_emergency_food(
        client,
        minimum_health=12.0,
        renewable_source_callback=lambda animal_type, location: renewable_sources.append(
            (animal_type, location)
        ),
    )
    assert hunted == [True]
    assert renewable_sources == [("sheep", (8, 64, 0))]
    assert ("chat", {"message": "#set allowSprint false"}) in transport.calls
    assert ("chat", {"message": "#set allowSprint true"}) in transport.calls


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
    land_scans = {"count": 0}
    hunted = []
    def recover(*_args, **_kwargs):
        recovered = next(recovery_results)
        if recovered:
            transport.health = 12.0
        return recovered

    monkeypatch.setattr(combat, "recover_health", recover)
    monkeypatch.setattr(combat, "scan_for_threats", lambda *_args, **_kwargs: [])
    herd = [
        {**cow, "id": 13 + index, "distance": 10.0 + index}
        for index in range(3)
    ]

    # health=5.0 is below _CRITICAL_HUNT_HEALTH, so the target search uses
    # the shrunk 16-block radius rather than the normal 64.
    def nearby(_client, radius):
        if radius != 16:
            return []
        land_scans["count"] += 1
        return herd if land_scans["count"] >= 3 else []

    monkeypatch.setattr(combat, "get_nearby_entities", nearby)
    monkeypatch.setattr(combat, "safe_combat", lambda *_args, **_kwargs: hunted.append(True) or True)
    monkeypatch.setattr(combat, "heal_if_needed", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(combat.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr("baritone_client.common.navigation.goto", lambda *_args, **_kwargs: True)

    assert combat.acquire_emergency_food(client, minimum_health=12.0)
    assert hunted == [True]
    exploration_calls = [
        payload for route, payload in transport.calls if route == "explore"
    ]
    assert exploration_calls
    assert all(payload != {"x": 0, "z": 0} for payload in exploration_calls)
    assert ("chat", {"message": "#stop"}) in transport.calls


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

    monkeypatch.setattr(
        combat,
        "get_nearby_entities",
        lambda _client, radius: reach_food_search() if radius == 64 else [],
    )

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


def test_hunt_does_not_classify_requested_hostile_as_other_hostile(monkeypatch):
    client = SimpleNamespace(transport=CombatTransport())
    zombie = {
        "id": 42,
        "type": "minecraft:zombie",
        "distance": 5.0,
        "position": {"x": 5, "y": 64, "z": 0},
    }
    monkeypatch.setattr(combat, "count_item", lambda *_args: 0)
    monkeypatch.setattr(combat, "heal_if_needed", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        combat, "get_nearby_entities", lambda *_args, **_kwargs: [zombie]
    )

    class ReachedTargetSelection(Exception):
        pass

    monkeypatch.setattr(
        combat,
        "find_entity_by_type",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ReachedTargetSelection()
        ),
    )

    with pytest.raises(ReachedTargetSelection):
        combat.hunt_mobs(
            client,
            ["zombie"],
            {"minecraft:rotten_flesh": 1},
            timeout=30,
            abort_on_other_hostiles=True,
        )


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


def test_survival_tick_surfaces_submerged_player(monkeypatch):
    client = SimpleNamespace(
        transport=SubmergedTransport("minecraft:water"), _submersion_ticks=1
    )
    surfaced = []
    monkeypatch.setattr(
        combat,
        "_surface_after_aquatic_hunt",
        lambda _c, **_k: surfaced.append(True) or True,
    )
    state = {"block_position": {"x": 0, "y": 62, "z": 0}}

    assert combat.survival_tick(client, state) is True
    assert surfaced == [True]


def test_survival_tick_noop_when_dry():
    client = SimpleNamespace(transport=SubmergedTransport("minecraft:air"))
    assert combat.survival_tick(
        client, {"block_position": {"x": 0, "y": 70, "z": 0}}
    ) is False


def test_submerged_too_long_times_out_then_fires(monkeypatch):
    client = SimpleNamespace(transport=SubmergedTransport("minecraft:water"))
    clock = iter([100.0, 109.0])
    monkeypatch.setattr(combat.time, "time", lambda: next(clock))
    state = {"block_position": {"x": 0, "y": 62, "z": 0}}

    # First call starts the submersion timer, not yet over the limit.
    assert combat._submerged_too_long(client, state, max_seconds=8.0) is False
    assert client._submerged_since == 100.0
    # 9s later -> over the 8s limit.
    assert combat._submerged_too_long(client, state, max_seconds=8.0) is True


def test_submerged_too_long_resets_when_head_clears():
    client = SimpleNamespace(
        transport=SubmergedTransport("minecraft:air"), _submerged_since=100.0
    )
    assert (
        combat._submerged_too_long(
            client, {"block_position": {"x": 0, "y": 70, "z": 0}}, max_seconds=8.0
        )
        is False
    )
    assert client._submerged_since is None


def test_safe_combat_surfaces_and_aborts_when_submerged_too_long(monkeypatch):
    client = SimpleNamespace(transport=CombatTransport(health=20.0))
    monkeypatch.setattr(combat, "_get_combat_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(combat, "get_nearby_entities", lambda *_a, **_k: [])
    monkeypatch.setattr(combat, "equip_best_weapon", lambda _c: True)
    monkeypatch.setattr(combat, "_submerged_too_long", lambda *_a, **_k: True)
    monkeypatch.setattr(combat.time, "sleep", lambda _s: None)
    surfaced = []
    monkeypatch.setattr(
        combat,
        "_surface_after_aquatic_hunt",
        lambda _c, **_k: surfaced.append(True) or True,
    )

    assert combat.safe_combat(client, target_id=5) is False
    assert surfaced == [True]


def test_approach_aquatic_food_surfaces_when_submerged_too_long(monkeypatch):
    client = SimpleNamespace(transport=SubmergedTransport("minecraft:water"))
    monkeypatch.setattr(combat, "_submerged_too_long", lambda *_a, **_k: True)
    monkeypatch.setattr(combat.time, "sleep", lambda _s: None)

    assert (
        combat._approach_aquatic_food(client, target_id=7, entity_type="minecraft:cod")
        is False
    )


def test_repeated_failed_evasion_escalates_to_fighting_back(monkeypatch):
    """Regression for a live cornering: an unarmored bot in mountainous
    terrain got a zombie stuck in melee range for dozens of consecutive
    cycles -- armor_count<3 forces EVADE unconditionally, run_away kept
    returning False ("no terrain-safe escape endpoint found" -- every
    candidate route was rejected for having open air/ledges nearby), and the
    bot slowly died doing nothing but failing to flee. Pure evasion with a
    demonstrated 0% success rate against a threat must eventually escalate
    to fighting, since continuing it is strictly worse than even an
    unfavorable fight."""
    transport = CombatTransport(health=20.0)
    client = SimpleNamespace(transport=transport)
    threat = {
        "id": 42,
        "type": "minecraft:zombie",
        "distance": 8.0,
        "position": {"x": 8, "y": 64, "z": 0},
    }
    monkeypatch.setattr(combat, "scan_for_threats", lambda *_a, **_k: [threat])
    monkeypatch.setattr(combat, "get_equipped_armor", lambda _c: {})  # 0/4 armor
    monkeypatch.setattr(combat, "run_away", lambda *_a, **_k: False)
    monkeypatch.setattr(combat.time, "sleep", lambda _s: None)
    fought = []
    monkeypatch.setattr(
        combat,
        "safe_combat",
        lambda *_a, **_k: fought.append(True) or True,
    )

    # Same runtime persists across calls (stashed on the client), so repeated
    # ticks against the SAME threat id accumulate failures.
    for _ in range(3):
        assert combat.defend_or_flee(client)
    assert not fought  # not yet -- below the escalation threshold

    assert combat.defend_or_flee(client)
    assert fought == [True]  # 4th consecutive failure escalates


def test_evasion_failure_count_resets_against_a_different_threat(monkeypatch):
    """Failures against threat A must not silently escalate a fresh
    encounter with threat B."""
    transport = CombatTransport(health=20.0)
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(combat, "get_equipped_armor", lambda _c: {})
    monkeypatch.setattr(combat, "run_away", lambda *_a, **_k: False)
    monkeypatch.setattr(combat.time, "sleep", lambda _s: None)
    fought = []
    monkeypatch.setattr(
        combat, "safe_combat", lambda *_a, **_k: fought.append(True) or True
    )

    threat_a = {
        "id": 1, "type": "minecraft:zombie", "distance": 8.0,
        "position": {"x": 8, "y": 64, "z": 0},
    }
    monkeypatch.setattr(combat, "scan_for_threats", lambda *_a, **_k: [threat_a])
    for _ in range(3):
        combat.defend_or_flee(client)

    threat_b = {
        "id": 2, "type": "minecraft:zombie", "distance": 8.0,
        "position": {"x": -8, "y": 64, "z": 0},
    }
    monkeypatch.setattr(combat, "scan_for_threats", lambda *_a, **_k: [threat_b])
    assert combat.defend_or_flee(client)
    assert not fought  # a new threat starts its own failure count at 1


def test_a_successful_evade_never_escalates(monkeypatch):
    transport = CombatTransport(health=20.0)
    client = SimpleNamespace(transport=transport)
    threat = {
        "id": 7, "type": "minecraft:zombie", "distance": 8.0,
        "position": {"x": 8, "y": 64, "z": 0},
    }
    monkeypatch.setattr(combat, "scan_for_threats", lambda *_a, **_k: [threat])
    monkeypatch.setattr(combat, "get_equipped_armor", lambda _c: {})
    monkeypatch.setattr(combat, "run_away", lambda *_a, **_k: True)
    monkeypatch.setattr(combat.time, "sleep", lambda _s: None)
    fought = []
    monkeypatch.setattr(
        combat, "safe_combat", lambda *_a, **_k: fought.append(True) or True
    )

    for _ in range(6):
        combat.defend_or_flee(client)

    assert not fought


def test_safe_combat_no_retreat_attacks_despite_low_health(monkeypatch):
    """Regression: the escalation path in defend_or_flee passes
    no_retreat=True specifically because a fixed retreat_health floor kept
    getting undercut -- health crashes unpredictably during the failed
    flee attempts that precede escalation, so by the time safe_combat is
    called it is often already below whatever floor was configured, and it
    was retreating on the very first health check without ever attacking.
    Live: this happened at 4.8hp (floor=6.0) and again at 1.999hp
    (floor=2.0). no_retreat=True must skip that gate and actually attack."""
    transport = CombatTransport(health=1.5)
    client = SimpleNamespace(transport=transport)
    target = {"id": 5, "type": "minecraft:zombie", "distance": 3.0}
    monkeypatch.setattr(combat, "equip_best_weapon", lambda *_a, **_k: None)
    monkeypatch.setattr(combat, "_get_combat_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(
        combat, "get_nearby_entities", lambda *_a, **_k: [target]
    )
    monkeypatch.setattr(combat, "_submerged_too_long", lambda *_a, **_k: False)
    monkeypatch.setattr(combat, "look_at_entity", lambda *_a, **_k: True)
    monkeypatch.setattr(combat, "_attack_cooldown", lambda *_a, **_k: 999.0)
    monkeypatch.setattr(combat.time, "sleep", lambda _s: None)

    def dispatch(route, payload):
        transport.calls.append((route, payload))
        if route == "get_state":
            return {"health": transport.health, "block_position": {"x": 0, "y": 64, "z": 0}}
        if route == "attack_entity":
            return {"attacked": True}
        return {}

    transport.dispatch = dispatch

    combat.safe_combat(client, 5, retreat_health=6.0, no_retreat=True, max_duration=0.2)

    assert ("attack_entity", {"entity_id": 5, "min_cooldown": combat.MELEE_ATTACK_COOLDOWN_THRESHOLD}) in transport.calls
    assert not any(call[0] == "cancel" for call in transport.calls)


def test_safe_combat_still_retreats_by_default_at_low_health(monkeypatch):
    transport = CombatTransport(health=1.5)
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(combat, "equip_best_weapon", lambda *_a, **_k: None)
    monkeypatch.setattr(combat, "_get_combat_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(combat, "get_nearby_entities", lambda *_a, **_k: [])
    monkeypatch.setattr(combat, "_submerged_too_long", lambda *_a, **_k: False)

    assert combat.safe_combat(client, 5, retreat_health=6.0) is False
    assert ("cancel", {}) in transport.calls
    assert not any(call[0] == "attack_entity" for call in transport.calls)


def test_emergency_food_does_not_chase_distant_target_at_critical_health(monkeypatch):
    """Regression: must_hold_for_critical_food only holds below health 6.0, so
    a bot at e.g. 7.3hp is treated as "safe enough" to proceed and would
    otherwise walk toward the nearest singleton target regardless of distance
    -- with no further health check until arrival. Confirmed live: Bot08
    repeatedly walked 48-64m toward a chicken/salmon at ~7hp and died to
    combat or drowning partway there. Below _CRITICAL_HUNT_HEALTH the search
    radius shrinks to 16 (the same perimeter already trusted for the
    immediate-threat scan), so a distant-only target must not be selected."""
    transport = CombatTransport(health=7.3)
    client = SimpleNamespace(transport=transport)
    distant_chicken = {
        "id": 40,
        "type": "minecraft:chicken",
        "distance": 60.0,
        "position": {"x": 60, "y": 64, "z": 0},
    }
    monkeypatch.setattr(combat, "recover_health", lambda *_a, **_k: False)
    monkeypatch.setattr(combat, "scan_for_threats", lambda *_a, **_k: [])
    hunted = []
    monkeypatch.setattr(
        combat,
        "get_nearby_entities",
        lambda _client, radius: [distant_chicken] if radius == 64 else [],
    )
    monkeypatch.setattr(combat, "safe_combat", lambda *_a, **_k: hunted.append(True) or True)
    monkeypatch.setattr(combat.time, "sleep", lambda _s: None)

    combat.acquire_emergency_food(client, minimum_health=12.0, timeout=1.0)

    assert not hunted


def _evade_client(monkeypatch, mob_type, *, escaped=False):
    """Client wired so defend_or_flee always takes the EVADE branch and
    run_away always fails, so repeated ticks accumulate evade failures."""
    transport = CombatTransport(health=20.0)
    client = SimpleNamespace(transport=transport)
    threat = {
        "id": 77,
        "type": f"minecraft:{mob_type}",
        "distance": 5.5,
        "position": {"x": 5, "y": 64, "z": 0},
    }
    monkeypatch.setattr(combat, "scan_for_threats", lambda *_a, **_k: [threat])
    monkeypatch.setattr(combat, "get_equipped_armor", lambda _c: {})
    monkeypatch.setattr(combat, "run_away", lambda *_a, **_k: escaped)
    monkeypatch.setattr(combat.time, "sleep", lambda _s: None)
    return client, transport


def test_repeated_evasion_never_melees_a_creeper(monkeypatch):
    """A meleed creeper detonates -- that is exactly why its ThreatProfile is
    always_evade/EXPLOSIVE. The evade-escalation added for cornered zombies
    originally fired on ANY failed evasion, so it live-fired against creepers
    364x on Bot07 and 367x on Bot08. It must relocate instead of fighting."""
    client, _t = _evade_client(monkeypatch, "creeper")
    fought = []
    relocated = []
    monkeypatch.setattr(combat, "safe_combat", lambda *_a, **_k: fought.append(True) or True)
    monkeypatch.setattr(
        combat, "_relocate_away_from", lambda *_a, **_k: relocated.append(True) or True
    )

    for _ in range(5):
        assert combat.defend_or_flee(client)

    assert not fought, "must never melee an EXPLOSIVE threat"
    assert relocated, "should break the standoff by relocating"


def test_repeated_evasion_still_fights_back_against_a_zombie(monkeypatch):
    """The escalation's original purpose: a zombie is only in EVADE because of
    the armor_count<3 gate, not because it is unfightable by policy. That case
    must still escalate."""
    client, _t = _evade_client(monkeypatch, "zombie")
    fought = []
    relocated = []
    monkeypatch.setattr(combat, "safe_combat", lambda *_a, **_k: fought.append(True) or True)
    monkeypatch.setattr(
        combat, "_relocate_away_from", lambda *_a, **_k: relocated.append(True) or True
    )

    for _ in range(5):
        assert combat.defend_or_flee(client)

    assert fought, "a cornered zombie should still be fought as a last resort"
    assert not relocated


class _RelocateTransport:
    """Transport whose player walks away from the threat over successive
    get_state polls, so relocation can observe real separation gain."""

    def __init__(self, positions):
        self.calls = []
        self._positions = list(positions)

    def dispatch(self, route, payload):
        self.calls.append((route, payload))
        if route == "get_state":
            x = self._positions[0]
            if len(self._positions) > 1:
                self._positions.pop(0)
            return {"health": 20.0, "block_position": {"x": x, "y": 64, "z": 0}}
        return {}


def test_relocate_heads_directly_away_using_a_y_agnostic_goal(monkeypatch):
    """The relocation goal must not pin a Y coordinate. goto's arrival test is
    3D, so passing the player's current Y made the destination unreachable
    whenever the ground 28 blocks away sat more than `tolerance` higher or
    lower -- it returned False on every call, the caller never reset its evade
    counter, and Bot07 climbed to "evasion failed 42x" without gathering a
    single log. Two-argument #goto lets Baritone resolve a walkable column."""
    monkeypatch.setattr(combat.time, "sleep", lambda _s: None)
    # Player starts at x=0 and walks to x=-30; threat sits at x=+5.
    transport = _RelocateTransport([0, -12, -30])
    client = SimpleNamespace(transport=transport)
    threat = {"id": 5, "type": "minecraft:creeper", "position": {"x": 5, "y": 64, "z": 0}}

    assert combat._relocate_away_from(client, threat, distance=28)

    goals = [
        payload["message"]
        for route, payload in transport.calls
        if route == "chat" and payload.get("message", "").startswith("#goto")
    ]
    # Directly away from +x means -x, a full 28 blocks out, and no Y term.
    assert goals == ["#goto -28 0"]


def test_relocate_reports_failure_when_separation_never_grows(monkeypatch):
    """A bot pinned in place must report failure so the caller can try
    something else, rather than silently claiming it escaped."""
    monkeypatch.setattr(combat.time, "sleep", lambda _s: None)
    transport = _RelocateTransport([0])  # never moves
    client = SimpleNamespace(transport=transport)
    threat = {"id": 5, "type": "minecraft:creeper", "position": {"x": 5, "y": 64, "z": 0}}

    assert not combat._relocate_away_from(client, threat, distance=28, timeout=3)


def test_submerged_bot_targets_nearby_fish_immediately(monkeypatch):
    """Live: Bot07/Bot08 sat at 8.0/7.3 health in lush caves, feet in water,
    with 8-10 tropical fish inside 128 blocks -- their only available food --
    and never targeted one. The 16-block aquatic cap plus the land-search
    delay exist to stop a bot on LAND being dragged into water after a fish it
    cannot reach; a bot already submerged has nothing left to be dragged into,
    and a lush cave has no land animals to wait for."""
    from baritone_client.common import emergency_food

    looked = []

    def fake_find(_client, types, radius):
        looked.append((tuple(types), radius))
        return {"id": 7, "type": "minecraft:tropical_fish", "distance": 24.0}

    monkeypatch.setattr(combat, "find_entity_by_type", fake_find)

    target = emergency_food.select_target(
        SimpleNamespace(transport=CombatTransport()),
        [],
        current_food=17,          # well above the <=6 emergency gate
        elapsed=0.0,              # and far below the land-search delay
        timeout=240.0,
        renewable_source_callback=None,
        in_water=True,
    )

    assert target is not None and "tropical_fish" in target["type"]
    assert looked and looked[0][1] == 64, "submerged search must reach fish ~48m out"


def test_dry_bot_still_waits_before_chasing_fish(monkeypatch):
    """The original restriction must survive for a bot on land."""
    from baritone_client.common import emergency_food

    monkeypatch.setattr(
        combat, "find_entity_by_type", lambda *_a, **_k: {"id": 1, "type": "cod"}
    )

    assert emergency_food.select_target(
        SimpleNamespace(transport=CombatTransport()),
        [],
        current_food=17,
        elapsed=0.0,
        timeout=240.0,
        renewable_source_callback=None,
        in_water=False,
    ) is None


def test_player_is_in_water_reads_the_feet_block():
    class WaterTransport(CombatTransport):
        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                return {"health": 8.0, "block_position": {"x": 0, "y": 62, "z": 0}}
            if route == "get_block":
                return {"id": "minecraft:water"}
            return {}

    client = SimpleNamespace(transport=WaterTransport())
    state = {"block_position": {"x": 0, "y": 62, "z": 0}}
    assert combat._player_is_in_water(client, state)


def test_aquatic_approach_window_scales_with_distance(monkeypatch):
    """A flat 8s follow cannot cover the ~48m separation measured live in a
    lush cave, so every attempt reported "aquatic target could not be reached
    safely" and the bot starved beside its only food source."""
    from baritone_client.common import emergency_food

    seen = {}

    def fake_approach(_c, _id, _type, timeout):
        seen["timeout"] = timeout
        return False  # force the early return; we only care about the window

    monkeypatch.setattr(combat, "_approach_aquatic_food", fake_approach)
    monkeypatch.setattr(emergency_food.time, "sleep", lambda _s: None)

    emergency_food.hunt_target(
        SimpleNamespace(transport=CombatTransport()),
        {"id": 3, "type": "minecraft:tropical_fish", "distance": 48.7,
         "position": {"x": 48, "y": 62, "z": 0}},
        minimum_health=12.0,
        recovery_complete=lambda _s=None: False,
    )

    assert seen["timeout"] > 8.0, "window must grow for a distant fish"
    assert seen["timeout"] <= 30.0, "and stay bounded"
