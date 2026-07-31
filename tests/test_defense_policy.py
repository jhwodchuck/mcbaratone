from types import SimpleNamespace

from baritone_client.actions.combat import CombatAction
from baritone_client.common import combat
from baritone_client.common.defense import (
    DefenseMode,
    DefenseRuntime,
    assess_threats,
    choose_defense_action,
    plan_escape_candidates,
)


def _entity(entity_id, entity_type, distance, x, z, **extra):
    entity = {
        "id": entity_id,
        "type": f"minecraft:{entity_type}",
        "distance": distance,
        "position": {"x": x, "y": 64, "z": z},
        "velocity": {"x": 0, "y": 0, "z": 0},
    }
    entity.update(extra)
    return entity


def test_threat_classifier_uses_exact_registry_paths():
    threats = assess_threats(
        [
            _entity(1, "pig", 3, 3, 0),
            _entity(2, "piglin_brute", 8, 8, 0),
        ],
        {"block_position": {"x": 0, "y": 64, "z": 0}},
    )

    assert [threat.entity_type for threat in threats] == ["piglin_brute"]


def test_conditional_enderman_requires_aggression_or_contact():
    distant = _entity(1, "enderman", 8, 8, 0)
    angry = _entity(2, "enderman", 9, 9, 0, angry_at_player=True)

    threats = assess_threats(
        [distant, angry],
        {"block_position": {"x": 0, "y": 64, "z": 0}},
    )

    assert [threat.entity["id"] for threat in threats] == [2]


def test_daytime_spider_in_melee_range_overrides_stale_calm_metadata():
    """A spider at contact range is unsafe even between target updates."""
    distant = _entity(1, "spider", 8, 8, 0, is_aggressive=False)
    touching = _entity(2, "spider", 3, 3, 0, is_aggressive=False)
    state = {
        "world_time": 1000,
        "block_position": {"x": 0, "y": 64, "z": 0},
    }

    threats = assess_threats([distant, touching], state)

    assert [threat.entity["id"] for threat in threats] == [2]


def test_closing_ranged_threat_outranks_nearer_basic_melee():
    zombie = _entity(1, "zombie", 5, 5, 0)
    skeleton = _entity(2, "skeleton", 9, 9, 0)
    skeleton["velocity"]["x"] = -0.5

    threats = assess_threats(
        [zombie, skeleton],
        {"block_position": {"x": 0, "y": 64, "z": 0}},
    )

    assert threats[0].entity_type == "skeleton"
    assert threats[0].closing_speed > 0


def test_policy_evades_multiple_urgent_threats_even_when_equipped():
    threats = assess_threats(
        [
            _entity(1, "zombie", 4, 4, 0),
            _entity(2, "husk", 7, 0, 7),
        ],
        {"block_position": {"x": 0, "y": 64, "z": 0}},
    )

    decision = choose_defense_action(
        threats,
        health=20,
        armor_count=4,
        has_weapon=True,
        runtime=DefenseRuntime(),
        now=10,
    )

    assert decision.mode == DefenseMode.EVADE
    assert "multiple" in decision.reason


def test_policy_allows_only_bounded_melee_when_ready():
    threats = assess_threats(
        [_entity(1, "zombie", 5, 5, 0)],
        {"block_position": {"x": 0, "y": 64, "z": 0}},
    )

    decision = choose_defense_action(
        threats,
        health=20,
        armor_count=4,
        has_weapon=True,
        runtime=DefenseRuntime(),
        now=10,
    )

    assert decision.mode == DefenseMode.ENGAGE


def test_distant_non_closing_threat_is_alert_only():
    threats = assess_threats(
        [_entity(1, "zombie", 14, 14, 0)],
        {"block_position": {"x": 0, "y": 64, "z": 0}},
    )

    decision = choose_defense_action(
        threats,
        health=20,
        armor_count=4,
        has_weapon=False,
        runtime=DefenseRuntime(),
        now=10,
    )

    assert decision.mode == DefenseMode.ALERT


def test_distant_high_severity_threat_with_urgent_melee_forces_evasion():
    threats = assess_threats(
        [
            _entity(1, "skeleton", 14, 14, 0),
            _entity(2, "zombie", 4, 4, 0),
        ],
        {"block_position": {"x": 0, "y": 64, "z": 0}},
    )

    decision = choose_defense_action(
        threats,
        health=20,
        armor_count=4,
        has_weapon=True,
        runtime=DefenseRuntime(),
        now=10,
    )

    assert decision.mode == DefenseMode.EVADE
    assert "multiple" in decision.reason


def test_recovery_hysteresis_prevents_immediate_clear():
    runtime = DefenseRuntime()
    runtime.hold_recovery(8, now=10)

    held = choose_defense_action(
        [],
        health=20,
        armor_count=4,
        has_weapon=True,
        runtime=runtime,
        now=12,
    )
    cleared = choose_defense_action(
        [],
        health=20,
        armor_count=4,
        has_weapon=True,
        runtime=runtime,
        now=19,
    )

    assert held.mode == DefenseMode.RECOVER
    assert cleared.mode == DefenseMode.CLEAR


def test_escape_candidates_move_away_from_combined_threat_pressure():
    state = {"block_position": {"x": 0, "y": 64, "z": 0}}
    threats = assess_threats(
        [
            _entity(1, "zombie", 4, 4, 0),
            _entity(2, "skeleton", 6, 0, 6),
        ],
        state,
    )

    candidates = plan_escape_candidates(state["block_position"], threats)

    assert candidates[0].x < 0
    assert candidates[0].z < 0


class EscapeTransport:
    def __init__(self, threat, *, hazardous_primary=False):
        self.threat = threat
        self.hazardous_primary = hazardous_primary
        self.calls = []
        self.entity_reads = 0

    def dispatch(self, route, payload):
        self.calls.append((route, payload))
        if route == "get_state":
            return {
                "health": 20,
                "world_time": 13000,
                "block_position": {"x": -6, "y": 64, "z": 0},
            }
        if route == "get_entities":
            self.entity_reads += 1
            if self.entity_reads == 1:
                return {"entities": [self.threat]}
            return {"entities": []}
        if route == "get_block":
            if (
                self.hazardous_primary
                and payload["x"] == -30
                and payload["y"] == 64
                and payload["z"] == 0
            ):
                return {"id": "minecraft:lava"}
            if payload["y"] == 63:
                return {"id": "minecraft:stone"}
            return {"id": "minecraft:air"}
        return {}


def test_run_away_verifies_target_is_no_longer_nearby():
    threat = _entity(1, "zombie", 10, 4, 0)
    transport = EscapeTransport(threat)
    client = SimpleNamespace(transport=transport)

    assert combat.run_away(client, threat, timeout=1)
    assert any(route == "goal" for route, _payload in transport.calls)


def test_run_away_rejects_lava_endpoint_before_pathing():
    threat = _entity(1, "zombie", 10, 4, 0)
    transport = EscapeTransport(threat, hazardous_primary=True)
    client = SimpleNamespace(transport=transport)

    assert combat.run_away(client, threat, timeout=1)
    goals = [payload for route, payload in transport.calls if route == "goal"]
    assert goals
    assert goals[0] != {"x": -30, "y": 64, "z": 0}


def test_defense_remains_in_recovery_after_successful_escape(monkeypatch):
    threat = _entity(1, "zombie", 5, 5, 0)

    class Transport:
        def __init__(self):
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                return {
                    "health": 20,
                    "block_position": {"x": 0, "y": 64, "z": 0},
                }
            return {}

    client = SimpleNamespace(transport=Transport())
    scans = iter(([threat], []))
    healed = []
    monkeypatch.setattr(combat, "scan_for_threats", lambda *_args, **_kwargs: next(scans))
    monkeypatch.setattr(combat, "get_equipped_armor", lambda _client: {})
    monkeypatch.setattr(combat, "run_away", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        combat,
        "heal_if_needed",
        lambda *_args, **_kwargs: healed.append(True) or False,
    )

    assert combat.defend_or_flee(client)
    assert combat.defend_or_flee(client)
    assert healed == [True]
    assert client._mcbaratone_defense_runtime.mode == DefenseMode.RECOVER


def test_unknown_entity_state_stops_work_instead_of_assuming_clear(monkeypatch):
    class Transport:
        def __init__(self):
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                return {
                    "health": 20,
                    "block_position": {"x": 0, "y": 64, "z": 0},
                }
            return {}

    client = SimpleNamespace(transport=Transport())
    monkeypatch.setattr(
        combat,
        "scan_for_threats",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            combat.EntityQueryError("bridge timeout")
        ),
    )

    assert combat.defend_or_flee(client)
    assert ("chat", {"message": "#stop"}) in client.transport.calls
    assert ("cancel", {}) in client.transport.calls
    assert client._mcbaratone_defense_runtime.mode == DefenseMode.ALERT


def test_defense_consumes_atomic_bridge_snapshot_without_split_reads():
    class Transport:
        def __init__(self):
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_combat_snapshot":
                return {
                    "snapshot_version": 1,
                    "tick": 1234,
                    "player": {
                        "health": 20,
                        "armor_count": 4,
                        "block_position": {"x": 0, "y": 64, "z": 0},
                    },
                    "entities": [],
                }
            # The drowning reflex reads the head block (dry here); this is a
            # deliberate safety read, not a split of the atomic threat snapshot.
            if route == "get_block":
                return {"id": "minecraft:air"}
            raise AssertionError(f"unexpected split observation: {route}")

    client = SimpleNamespace(transport=Transport())

    assert not combat.defend_or_flee(client)
    # Threat state still comes solely from the atomic snapshot; the only
    # additional read is the head-block check for drowning.
    assert client.transport.calls == [
        ("get_combat_snapshot", {"radius": 16}),
        ("get_block", {"x": 0, "y": 65, "z": 0}),
    ]


def test_bridge_aggression_metadata_controls_conditional_mob_policy():
    state = {"world_time": 13000, "entity_id": 99}
    calm = _entity(1, "enderman", 2, 2, 0)
    calm["is_aggressive"] = False
    angry = dict(calm, is_aggressive=True, target_id=99)

    assert assess_threats([calm], state) == []
    assert assess_threats([angry], state)[0].entity["target_id"] == 99


def test_failed_melee_reassesses_and_flees_new_highest_threat(monkeypatch):
    zombie = _entity(1, "zombie", 4, 4, 0)
    creeper = _entity(2, "creeper", 3, 0, 3)
    player = {
        "health": 20,
        "armor_count": 4,
        "block_position": {"x": 0, "y": 64, "z": 0},
    }
    snapshots = iter(
        (
            {"player": player, "entities": [zombie]},
            {"player": player, "entities": [zombie, creeper]},
        )
    )

    class Transport:
        def __init__(self):
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            return {}

    client = SimpleNamespace(transport=Transport())
    fled = []
    monkeypatch.setattr(
        combat,
        "_get_combat_snapshot",
        lambda *_args, **_kwargs: next(snapshots),
    )
    monkeypatch.setattr(combat, "equip_best_weapon", lambda _client: True)
    monkeypatch.setattr(combat, "safe_combat", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        combat,
        "run_away",
        lambda _client, target: fled.append(target) or True,
    )

    assert combat.defend_or_flee(client)
    assert fled == [creeper]


def test_combat_action_safe_combat_delegates_to_canonical_module(monkeypatch):
    client = SimpleNamespace()
    context = SimpleNamespace(client=client)
    calls = []
    monkeypatch.setattr(
        combat,
        "safe_combat",
        lambda delegated_client, target_id, **kwargs: calls.append(
            (delegated_client, target_id, kwargs)
        )
        or True,
    )

    assert CombatAction().safe_combat(
        context,
        42,
        retreat_health=11,
        max_duration=9,
    )
    assert calls == [
        (client, 42, {"retreat_health": 11, "max_duration": 9})
    ]


def test_combat_action_healing_delegates_to_canonical_module(monkeypatch):
    client = SimpleNamespace()
    context = SimpleNamespace(client=client)
    calls = []
    monkeypatch.setattr(
        combat,
        "heal_if_needed",
        lambda delegated_client, threshold: calls.append(
            (delegated_client, threshold)
        )
        or True,
    )

    assert CombatAction().heal_if_needed(context, threshold=13)
    assert calls == [(client, 13)]
