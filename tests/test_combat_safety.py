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
    monkeypatch.setattr(
        combat,
        "get_nearby_entities",
        lambda _client, radius: herd if radius == 64 else [],
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

    def nearby(_client, radius):
        if radius != 64:
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
    assert ("explore", {"x": 0, "z": 0}) in transport.calls
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
