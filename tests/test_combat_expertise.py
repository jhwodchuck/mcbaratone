from types import SimpleNamespace

import pytest

from baritone_client.common import combat, combat_melee, navigation
from baritone_client.common.combat_intent import (
    CombatIntent,
    combat_intent,
    exclude_authorized_threats,
)
from baritone_client.common.combat_loadout import (
    choose_best_weapon,
    equip_best_weapon,
)
from baritone_client.common.combat_targeting import matches_requested_mob
from baritone_client.common.cornered_defense import fight_if_no_escape
from baritone_client.common.defense import DefenseRuntime, assess_threats
from baritone_client.common import combat_ranged
from baritone_client.common import escape_recovery


def _entity(entity_id, entity_type, distance, x=0, z=0, **extra):
    value = {
        "id": entity_id,
        "type": f"minecraft:{entity_type}",
        "distance": distance,
        "position": {"x": x, "y": 64, "z": z},
        "velocity": {"x": 0, "y": 0, "z": 0},
    }
    value.update(extra)
    return value


def test_hunt_target_matching_is_exact_not_substring_based():
    assert matches_requested_mob("minecraft:pig", ["pig"])
    assert matches_requested_mob("minecraft:pig", ["minecraft:pig"])
    assert not matches_requested_mob("minecraft:piglin", ["pig"])
    assert not matches_requested_mob("minecraft:cave_spider", ["spider"])


def test_weapon_selection_rejects_broken_tier_and_uses_target_enchantment():
    entries = [
        {
            "id": "minecraft:diamond_sword",
            "count": 1,
            "damage": 1559,
            "max_damage": 1561,
        },
        {
            "id": "minecraft:iron_sword",
            "count": 1,
            "damage": 10,
            "max_damage": 250,
            "enchantments": [{"id": "minecraft:smite", "level": 3}],
        },
        {
            "id": "minecraft:netherite_axe",
            "count": 1,
            "damage": 0,
            "max_damage": 2031,
        },
    ]

    assert (
        choose_best_weapon(entries, "minecraft:zombie")["id"] == "minecraft:iron_sword"
    )


def test_combat_loadout_fails_closed_when_live_inventory_read_fails():
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("bridge unavailable")
            )
        )
    )

    assert not equip_best_weapon(client, "minecraft:zombie")


def test_unknown_entity_targeting_player_gets_conservative_threat_profile():
    threat = _entity(
        88,
        "modded_raider",
        6,
        x=6,
        target_id=5,
        is_aggressive=True,
    )
    assessments = assess_threats(
        [threat],
        {
            "entity_id": 5,
            "block_position": {"x": 0, "y": 64, "z": 0},
        },
    )

    assert [item.entity["id"] for item in assessments] == [88]
    assert "targeting player" in assessments[0].reasons


def test_intent_excludes_one_entity_not_every_mob_of_the_same_type():
    selected = _entity(1, "zombie", 4, x=4)
    secondary = _entity(2, "zombie", 5, z=5)
    client = SimpleNamespace()
    intent = CombatIntent.for_target(
        1,
        purpose="focus_fire",
        target_type="minecraft:zombie",
    )
    assessments = assess_threats(
        [selected, secondary],
        {"block_position": {"x": 0, "y": 64, "z": 0}},
    )

    with combat_intent(client, intent):
        remaining = exclude_authorized_threats(client, assessments)

    assert [item.entity["id"] for item in remaining] == [2]


def test_projectile_policy_ignores_own_and_outbound_arrows():
    own = _entity(
        1,
        "arrow",
        4,
        x=4,
        owner_id=5,
        velocity={"x": -1, "y": 0, "z": 0},
    )
    outbound = _entity(
        2,
        "arrow",
        4,
        x=4,
        owner_id=9,
        velocity={"x": 1, "y": 0, "z": 0},
    )
    incoming = _entity(
        3,
        "arrow",
        8,
        x=8,
        owner_id=9,
        velocity={"x": -1, "y": 0, "z": 0},
    )

    assessments = assess_threats(
        [own, outbound, incoming],
        {
            "entity_id": 5,
            "block_position": {"x": 0, "y": 64, "z": 0},
        },
    )

    assert [item.entity["id"] for item in assessments] == [3]


def test_vertical_inbound_projectile_is_an_urgent_threat():
    arrow = _entity(
        4,
        "arrow",
        10,
        x=0,
        z=0,
        position={"x": 0, "y": 74, "z": 0},
        velocity={"x": 0, "y": -1, "z": 0},
    )
    assessments = assess_threats([arrow], {"block_position": {"x": 0, "y": 64, "z": 0}})
    assert [item.entity["id"] for item in assessments] == [4]
    assert assessments[0].closing_speed > 0


def test_cornered_defense_never_melees_a_projectile():
    arrow = _entity(
        4,
        "arrow",
        4,
        x=4,
        velocity={"x": -1, "y": 0, "z": 0},
    )
    primary = assess_threats([arrow], {"block_position": {"x": 0, "y": 64, "z": 0}})[0]
    client = SimpleNamespace(_last_escape_failure_reason="no_safe_endpoint")
    fought = []
    assert not fight_if_no_escape(
        client,
        primary,
        DefenseRuntime(),
        equip_weapon=lambda _client: True,
        fight=lambda *_a, **_k: fought.append(True) or True,
    )
    assert fought == []


def test_vertical_projectile_defense_escalates_to_relocation_not_melee(monkeypatch):
    arrow = _entity(
        4,
        "arrow",
        10,
        x=0,
        z=0,
        position={"x": 0, "y": 74, "z": 0},
        velocity={"x": 0, "y": -1, "z": 0},
    )
    calls = []
    fled = []
    relocated = []
    fought = []

    def dispatch(route, payload):
        calls.append((route, payload))
        return {}

    runtime = DefenseRuntime()
    runtime.record_evade_result(arrow["id"], False)
    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=dispatch),
        _last_escape_failure_reason="no_safe_endpoint",
        _mcbaratone_defense_runtime=runtime,
    )
    monkeypatch.setattr(combat, "escape_water_if_submerged", lambda *_a: False)
    monkeypatch.setattr(
        combat,
        "run_away",
        lambda _client, threat: fled.append(threat["id"]) or False,
    )
    monkeypatch.setattr(
        combat,
        "_relocate_away_from",
        lambda _client, threat: relocated.append(threat["id"]) or False,
    )
    monkeypatch.setattr(
        combat,
        "_fight_defensive_target",
        lambda *_a, **_k: fought.append(True) or True,
    )

    assert combat.defend_or_flee(
        client,
        observed_snapshot={
            "player": {
                "health": 20,
                "armor_count": 4,
                "block_position": {"x": 0, "y": 64, "z": 0},
            },
            "entities": [arrow],
        },
    )
    assert fled == [4]
    assert relocated == [4]
    assert fought == []
    assert not any(route == "attack_entity" for route, _payload in calls)


def test_projectile_closing_speed_is_relative_to_player_movement():
    arrow = _entity(
        4,
        "arrow",
        10,
        x=10,
        velocity={"x": 0.2, "y": 0, "z": 0},
    )
    threats = assess_threats(
        [arrow],
        {
            "block_position": {"x": 0, "y": 64, "z": 0},
            "velocity": {"x": 0.5, "y": 0, "z": 0},
        },
    )

    assert len(threats) == 1
    assert threats[0].closing_speed == pytest.approx(0.3)


def test_escape_requires_movement_and_complete_consecutive_snapshots(monkeypatch):
    clock = {"now": 0.0}
    snapshots = [
        {
            "player": {"health": 20, "block_position": {"x": 0, "y": 64, "z": 0}},
            "entities": [],
            "skipped_count": 0,
        },
        {
            "player": {"health": 20, "block_position": {"x": 6, "y": 64, "z": 0}},
            "entities": [],
            "skipped_count": 1,
        },
    ]

    def dispatch(route, payload):
        if route == "get_state":
            return {"health": 20}
        if route == "get_combat_snapshot":
            return (
                snapshots.pop(0)
                if snapshots
                else {
                    "player": {
                        "health": 20,
                        "block_position": {"x": 6, "y": 64, "z": 0},
                    },
                    "entities": [],
                    "skipped_count": 1,
                }
            )
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    monkeypatch.setattr(escape_recovery.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        escape_recovery.time,
        "sleep",
        lambda seconds: clock.__setitem__("now", clock["now"] + seconds),
    )

    assert not escape_recovery.verify_escape_from_threats(
        client,
        {7: 17.0},
        {"x": 0, "y": 64, "z": 0},
        timeout=1.1,
        minimum_gain=5.0,
    )


def test_boss_intent_requires_explicit_boss_permission():
    dragon = _entity(99, "ender_dragon", 5, x=5)
    assessments = assess_threats(
        [dragon], {"block_position": {"x": 0, "y": 64, "z": 0}}
    )
    client = SimpleNamespace()
    denied = CombatIntent.for_target(
        99, purpose="ordinary", target_type="minecraft:ender_dragon"
    )
    allowed = CombatIntent.for_target(
        99,
        purpose="boss",
        target_type="minecraft:ender_dragon",
        allow_boss=True,
    )
    with combat_intent(client, denied):
        assert exclude_authorized_threats(client, assessments) == assessments
    with combat_intent(client, allowed):
        assert exclude_authorized_threats(client, assessments) == []


@pytest.mark.parametrize("distance", (3, 10), ids=("melee_range", "bow_range"))
def test_ordinary_safe_combat_never_attacks_a_boss(monkeypatch, distance):
    dragon = _entity(99, "ender_dragon", distance, x=distance, health=200)
    calls = []
    reasons = []

    def dispatch(route, payload):
        calls.append((route, payload))
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    monkeypatch.setattr(
        combat,
        "_get_combat_snapshot",
        lambda *_a, **_k: {
            "player": {
                "health": 20,
                "food_level": 20,
                "attack_cooldown": 1.0,
                "block_position": {"x": 0, "y": 64, "z": 0},
            },
            "entities": [dragon],
            "skipped_count": 0,
        },
    )
    recorder = combat.combat_telemetry.get_combat_telemetry(client)
    monkeypatch.setattr(recorder, "set_disengagement_reason", reasons.append)

    assert not combat.safe_combat(
        client,
        dragon["id"],
        max_duration=1,
        target_metadata=dragon,
    )
    assert reasons == ["boss_not_authorized"]
    assert ("cancel", {}) in calls
    assert not any(
        route in {"attack_entity", "use_item"} for route, _payload in calls
    )


@pytest.mark.parametrize("action", ("melee", "ranged"))
def test_explicit_exact_boss_intent_permits_low_level_shared_action(
    monkeypatch, action
):
    distance = 3 if action == "melee" else 10
    dragon = _entity(99, "ender_dragon", distance, x=distance, health=200)
    calls = []

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "get_state":
            return {
                "dimension": "minecraft:the_end",
                "game_mode": "survival",
                "health": 20,
                "food_level": 20,
                "attack_cooldown": 1.0,
                "block_position": {"x": 0, "y": 64, "z": 0},
            }
        if route == "get_inventory":
            return {
                "inventory": [
                    {
                        "id": "minecraft:bow",
                        "count": 1,
                        "slot": 0,
                        "damage": 0,
                        "max_damage": 384,
                    },
                    {"id": "minecraft:arrow", "count": 32, "slot": 1},
                ],
                "selected_slot": 0,
            }
        if route == "attack_entity":
            return {"attacked": True}
        if route == "use_item":
            return {
                "holding": True,
                "duration_ms": 1100,
                "held_item": "minecraft:bow",
            }
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    intent = CombatIntent.for_target(
        dragon["id"],
        purpose="explicit_dragon_test",
        target_type=dragon["type"],
        allow_boss=True,
    )
    state = {
        "dimension": "minecraft:the_end",
        "game_mode": "survival",
        "health": 20,
        "food_level": 20,
        "attack_cooldown": 1.0,
        "block_position": {"x": 0, "y": 64, "z": 0},
    }

    with combat_intent(client, intent):
        if action == "melee":
            monkeypatch.setattr(
                combat, "equip_best_weapon", lambda *_a, **_k: True
            )
            result = combat_melee.execute_melee_strike(client, dragon, state)
            assert result["attacked"] is True
        else:
            monkeypatch.setattr(
                combat_ranged,
                "dispatch_held_item_use",
                lambda owned_client, duration_ms: owned_client.transport.dispatch(
                    "use_item", {"duration_ms": duration_ms}
                ),
            )
            assert combat_ranged.fire_best_ranged_attack(client, dragon)

    expected_route = "attack_entity" if action == "melee" else "use_item"
    assert any(route == expected_route for route, _payload in calls)


@pytest.mark.parametrize(
    "state",
    (
        {
            "dimension": "minecraft:the_end",
            "game_mode": "creative",
            "health": 20,
            "food_level": 20,
            "block_position": {"x": 0, "y": 64, "z": 0},
        },
        {
            "dimension": "minecraft:overworld",
            "game_mode": "survival",
            "health": 20,
            "food_level": 20,
            "block_position": {"x": 0, "y": 64, "z": 0},
        },
    ),
    ids=("creative_end", "survival_overworld"),
)
def test_authorized_boss_ranged_attack_rechecks_exact_survival_end_state(state):
    dragon = _entity(99, "ender_dragon", 10, x=10, health=200)
    calls = []

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "get_state":
            return state
        if route == "get_inventory":
            return {
                "inventory": [
                    {
                        "id": "minecraft:bow",
                        "count": 1,
                        "slot": 0,
                        "damage": 0,
                        "max_damage": 384,
                    },
                    {"id": "minecraft:arrow", "count": 32, "slot": 1},
                ],
                "selected_slot": 0,
            }
        if route == "use_item":
            raise AssertionError("boss attack escaped the fresh world/mode gate")
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    intent = CombatIntent.for_target(
        dragon["id"],
        purpose="explicit_dragon_test",
        target_type=dragon["type"],
        allow_boss=True,
    )

    with combat_intent(client, intent):
        assert not combat_ranged.fire_best_ranged_attack(client, dragon)

    assert not any(route == "use_item" for route, _payload in calls)


@pytest.mark.parametrize("action", ("melee", "ranged"))
@pytest.mark.parametrize(
    ("authorized", "state"),
    (
        (
            False,
            {
                "dimension": "minecraft:the_end",
                "game_mode": "survival",
                "health": 20,
                "food_level": 20,
                "attack_cooldown": 1.0,
                "block_position": {"x": 0, "y": 64, "z": 0},
            },
        ),
        (
            True,
            {
                "dimension": "minecraft:overworld",
                "game_mode": "survival",
                "health": 20,
                "food_level": 20,
                "attack_cooldown": 1.0,
                "block_position": {"x": 0, "y": 64, "z": 0},
            },
        ),
    ),
    ids=("missing_intent", "wrong_dimension"),
)
def test_end_crystal_actions_require_exact_boss_intent_and_survival_end(
    action, authorized, state
):
    crystal = _entity(91, "end_crystal", 6, x=6)
    calls = []

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "get_state":
            return state
        if route == "get_inventory":
            return {
                "inventory": [
                    {
                        "id": "minecraft:bow",
                        "count": 1,
                        "slot": 0,
                        "damage": 0,
                        "max_damage": 384,
                    },
                    {"id": "minecraft:arrow", "count": 32, "slot": 1},
                ],
                "selected_slot": 0,
            }
        if route in {"use_item", "attack_entity"}:
            raise AssertionError("End crystal mutation escaped boss admission")
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))

    def attempt():
        if action == "melee":
            return combat_melee.execute_melee_strike(client, crystal, state).get(
                "attacked"
            ) is True
        return combat_ranged.fire_best_ranged_attack(client, crystal)

    if authorized:
        intent = CombatIntent.for_target(
            crystal["id"],
            purpose="explicit_crystal_test",
            target_type=crystal["type"],
            allow_boss=True,
        )
        with combat_intent(client, intent):
            assert not attempt()
    else:
        assert not attempt()

    assert not any(
        route in {"use_item", "attack_entity"} for route, _payload in calls
    )


@pytest.mark.parametrize("skipped_count", (1, "unknown"))
def test_safe_combat_rejects_incomplete_or_malformed_snapshot(
    monkeypatch, skipped_count
):
    target = _entity(42, "zombie", 3, x=3, health=0)
    calls = []
    reasons = []

    def dispatch(route, payload):
        calls.append((route, payload))
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    monkeypatch.setattr(
        combat,
        "_get_combat_snapshot",
        lambda *_a, **_k: {
            "player": {
                "health": 20,
                "food_level": 20,
                "block_position": {"x": 0, "y": 64, "z": 0},
            },
            "entities": [target],
            "skipped_count": skipped_count,
        },
    )
    recorder = combat.combat_telemetry.get_combat_telemetry(client)
    monkeypatch.setattr(recorder, "set_disengagement_reason", reasons.append)

    assert not combat.safe_combat(
        client, target["id"], max_duration=1, target_metadata=target
    )
    assert reasons == ["incomplete_snapshot"]
    assert ("cancel", {}) in calls
    assert not any(route == "attack_entity" for route, _payload in calls)


@pytest.mark.parametrize("skipped_count", (1, "unknown"))
def test_defense_rejects_incomplete_or_malformed_observed_snapshot(
    monkeypatch, skipped_count
):
    calls = []
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, payload: calls.append((route, payload)) or {}
        )
    )
    monkeypatch.setattr(combat, "escape_water_if_submerged", lambda *_a: False)

    assert combat.defend_or_flee(
        client,
        observed_snapshot={
            "player": {
                "health": 20,
                "food_level": 20,
                "block_position": {"x": 0, "y": 64, "z": 0},
            },
            "entities": [],
            "skipped_count": skipped_count,
        },
    )
    assert client._last_defense_intervention == "incomplete_snapshot"
    assert ("cancel", {}) in calls


def test_exact_scored_weapon_stack_is_selected_and_verified():
    class InventoryTransport:
        def __init__(self):
            self.selected = 0
            self.entries = [
                {
                    "id": "minecraft:iron_sword",
                    "count": 1,
                    "slot": 0,
                    "damage": 249,
                    "max_damage": 250,
                },
                {
                    "id": "minecraft:iron_sword",
                    "count": 1,
                    "slot": 10,
                    "damage": 10,
                    "max_damage": 250,
                    "enchantments": [{"id": "minecraft:smite", "level": 3}],
                },
            ]

        def dispatch(self, route, payload):
            if route == "get_inventory":
                return {"inventory": list(self.entries), "selected_slot": self.selected}
            if route == "inventory_click":
                source = next(
                    item for item in self.entries if item["slot"] == payload["slot"]
                )
                target = next(
                    item for item in self.entries if item["slot"] == payload["button"]
                )
                source["slot"], target["slot"] = target["slot"], source["slot"]
            if route == "select_slot":
                self.selected = payload["slot"]
            return {}

    transport = InventoryTransport()
    assert equip_best_weapon(SimpleNamespace(transport=transport), "minecraft:zombie")
    equipped = next(item for item in transport.entries if item["slot"] == 0)
    assert equipped["damage"] == 10


def test_ranged_attack_leads_movement_and_serializes_bow_charges(
    monkeypatch, advancing_clock
):
    from baritone_client.common import combat_action

    clock = {"now": 10.0}
    calls = []

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "get_state":
            return {"health": 20}
        if route == "get_inventory":
            return {
                "inventory": [
                    {"id": "minecraft:bow", "count": 1, "slot": 0},
                    {"id": "minecraft:arrow", "count": 64, "slot": 1},
                ],
                "selected_slot": 0,
            }
        if route == "use_item":
            return {
                "holding": True,
                "duration_ms": 1100,
                "held_item": "minecraft:bow",
                "hit": {"type": "miss"},
            }
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    target = _entity(
        9,
        "skeleton",
        35,
        x=30,
        velocity={"x": 0.5, "y": 0, "z": 0},
    )
    monkeypatch.setattr(combat_ranged.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(combat_action, "time", advancing_clock())
    monkeypatch.setattr(
        "baritone_client.common.inventory.select_item", lambda *_a, **_k: True
    )

    assert combat_ranged.fire_best_ranged_attack(client, target)
    assert not combat_ranged.fire_best_ranged_attack(client, target)
    assert [route for route, _ in calls].count("use_item") == 1
    look = next(payload for route, payload in calls if route == "look_at")
    assert look["x"] == 40.0
    clock["now"] = 11.3
    assert combat_ranged.fire_best_ranged_attack(client, target)
    assert [route for route, _ in calls].count("use_item") == 2


def test_ranged_attack_rejects_unverified_bridge_hold():
    def dispatch(route, payload):
        if route == "get_state":
            return {"health": 20}
        if route == "get_inventory":
            return {
                "inventory": [
                    {"id": "minecraft:bow", "count": 1, "slot": 0},
                    {"id": "minecraft:arrow", "count": 8, "slot": 1},
                ],
                "selected_slot": 0,
            }
        if route == "use_item":
            return {}
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    target = _entity(9, "skeleton", 10, x=10)
    assert not combat_ranged.fire_best_ranged_attack(client, target)


def test_ranged_selection_prefers_healthy_bow_over_nearly_broken_one():
    entries = [
        {
            "id": "minecraft:bow",
            "slot": 0,
            "count": 1,
            "max_damage": 384,
            "damage": 380,
        },
        {
            "id": "minecraft:bow",
            "slot": 4,
            "count": 1,
            "max_damage": 384,
            "damage": 20,
        },
        {"id": "minecraft:arrow", "slot": 5, "count": 32},
    ]

    selected = combat_ranged._find_ranged_item(entries, allow_throwables=False)

    assert selected is not None
    assert selected[0]["slot"] == 4


def test_authorized_boss_navigation_ignores_only_the_intended_target(monkeypatch):
    dragon = _entity(99, "ender_dragon", 5, x=5)

    class Transport:
        def __init__(self):
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                return {
                    "health": 20,
                    "food_level": 20,
                    "is_pathing": True,
                    "block_position": {"x": 5, "y": 64, "z": 0},
                }
            if route == "get_combat_snapshot":
                return {
                    "player": {
                        "health": 20,
                        "armor_count": 4,
                        "block_position": {"x": 5, "y": 64, "z": 0},
                    },
                    "entities": [dragon],
                }
            return {}

    client = SimpleNamespace(transport=Transport())
    monkeypatch.setattr(combat, "escape_water_if_submerged", lambda *_a: False)
    intent = CombatIntent.for_target(
        99,
        purpose="boss_test",
        target_type="minecraft:ender_dragon",
        allow_boss=True,
    )

    with combat_intent(client, intent):
        assert navigation.goto(client, 5, 64, 0)


def test_target_approach_aborts_when_secondary_hostile_enters(monkeypatch):
    target = _entity(42, "cow", 20, x=20)
    creeper = _entity(7, "creeper", 6, z=6)
    calls = []
    defense_checks = []
    events = []

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "get_state":
            return {
                "health": 20,
                "food_level": 20,
                "block_position": {"x": 0, "y": 64, "z": 0},
            }
        return {}

    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=dispatch)
    )
    monkeypatch.setattr(
        combat,
        "_get_combat_snapshot",
        lambda *_a, **_k: {
            "player": {"health": 20, "attack_cooldown": 1.0},
            "entities": [target],
        },
    )
    monkeypatch.setattr(
        combat, "get_nearby_entities", lambda *_a, **_k: [target, creeper]
    )
    monkeypatch.setattr(combat, "equip_best_weapon", lambda *_a, **_k: True)
    monkeypatch.setattr(
        combat.combat_telemetry,
        "record_combat_action",
        lambda _client, action, **fields: events.append((action, fields)),
    )

    def approach(_client, _x, _y, _z, **kwargs):
        interrupted = kwargs["on_defense"]()
        defense_checks.append(interrupted)
        return not interrupted

    monkeypatch.setattr(combat, "goto", approach)

    assert not combat.safe_combat(
        client,
        target["id"],
        abort_on_other_hostiles=True,
        max_duration=5,
        target_metadata=target,
    )
    assert defense_checks == [True]
    assert ("cancel", {}) in calls
    assert not any(route == "attack_entity" for route, _payload in calls)
    assert any(
        action == "approach_interrupted"
        and fields.get("outcome") == "secondary_hostile"
        for action, fields in events
    )


def test_shared_safe_combat_uses_ranged_strategy_for_distant_archer(monkeypatch):
    skeleton = _entity(42, "skeleton", 10, x=10, health=20)
    snapshots = iter(
        (
            {"player": {"health": 20}, "entities": [skeleton]},
            {"player": {"health": 20}, "entities": [{**skeleton, "health": 0}]},
        )
    )
    calls = []

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "get_state":
            return {"health": 20}
        if route == "get_inventory":
            return {
                "inventory": [
                    {"id": "minecraft:bow", "count": 1, "slot": 0},
                    {"id": "minecraft:arrow", "count": 16, "slot": 1},
                ],
                "selected_slot": 0,
            }
        if route == "use_item":
            return {
                "holding": True,
                "duration_ms": 1100,
                "held_item": "minecraft:bow",
                "hit": {"type": "miss"},
            }
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    monkeypatch.setattr(
        combat, "_get_combat_snapshot", lambda *_a, **_k: next(snapshots)
    )

    assert combat.safe_combat(
        client,
        skeleton["id"],
        max_duration=3,
        target_metadata=skeleton,
    )
    assert (
        "use_item",
        {"hand": "MAIN_HAND", "duration_ms": 1100},
    ) in calls
    assert not any(route == "attack_entity" for route, _payload in calls)
