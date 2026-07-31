from types import SimpleNamespace

from baritone_client.automator.phases import spawn_bootstrap
from baritone_client.automator.phases import initial_gathering
from baritone_client.automator.phases.spawn_bootstrap import SpawnBootstrapHandler
from baritone_client.automator.phases.initial_gathering import InitialGatheringHandler
from baritone_client.common.base import wait_for_safe_daylight
from baritone_client.common import base, harness_ops


class SequenceTransport:
    def __init__(self, states):
        self.states = list(states)
        self.calls = []

    def dispatch(self, route, payload, **kwargs):
        self.calls.append((route, payload))
        if route == "get_state":
            if len(self.states) > 1:
                return self.states.pop(0)
            return self.states[0]
        if route == "get_inventory":
            return {"inventory": []}
        return {"status": "ok"}


class ReadyResources:
    @staticmethod
    def phase_ready_result(_phase, _message):
        return None


def test_wait_for_daylight_yields_to_death_recovery_before_respawn():
    transport = SequenceTransport(
        [
            {"world_time": 16000, "is_dead": False},
            {"world_time": 17000, "is_dead": True},
            {"world_time": 100, "is_dead": False},
        ]
    )
    client = SimpleNamespace(transport=transport)

    assert not wait_for_safe_daylight(
        client,
        max_wait=1.0,
        poll_interval=0.0,
    )
    assert not any(route == "respawn" for route, _payload in transport.calls)
    assert any(route == "cancel" for route, _payload in transport.calls)


def test_nearby_bed_sleep_must_prove_daylight(monkeypatch):
    transport = SequenceTransport(
        [
            {
                "world_time": 16000,
                "is_dead": False,
                "block_position": {"x": 0, "y": 64, "z": 0},
            },
            {"world_time": 100, "is_dead": False},
        ]
    )
    client = SimpleNamespace(transport=transport)
    monkeypatch.setattr(base, "find_nearby_block", lambda *_args, **_kwargs: (1, 64, 0))
    monkeypatch.setattr(base.time, "sleep", lambda _seconds: None)

    assert base._sleep_in_nearby_bed(client, timeout=1.0)
    assert any(route == "interact_block" for route, _payload in transport.calls)


def test_daylight_wait_attempts_shelter_only_once(monkeypatch):
    transport = SequenceTransport(
        [
            {"world_time": 16000, "is_dead": False},
            {"world_time": 17000, "is_dead": False},
            {"world_time": 100, "is_dead": False},
        ]
    )
    client = SimpleNamespace(transport=transport)
    attempts = []
    monkeypatch.setattr(
        "baritone_client.common.base.build_compact_night_shelter",
        lambda _client: attempts.append(True) or True,
    )

    assert wait_for_safe_daylight(client, max_wait=1.0, poll_interval=0.0)
    assert attempts == [True]


def test_daylight_wait_does_not_spam_failed_partial_shelter(monkeypatch):
    transport = SequenceTransport(
        [
            {"world_time": 16000, "is_dead": False},
            {"world_time": 17000, "is_dead": False},
            {"world_time": 100, "is_dead": False},
        ]
    )
    client = SimpleNamespace(transport=transport)
    attempts = []
    monkeypatch.setattr(
        "baritone_client.common.combat.scan_for_threats",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "baritone_client.common.base.build_compact_night_shelter",
        lambda _client: attempts.append(True) and False,
    )

    assert wait_for_safe_daylight(client, max_wait=1.0, poll_interval=0.0)
    assert attempts == [True]


def test_daylight_wait_defends_before_slow_shelter_work(monkeypatch):
    transport = SequenceTransport(
        [
            {"world_time": 16000, "is_dead": False, "health": 20},
            {"world_time": 100, "is_dead": False, "health": 20},
        ]
    )
    client = SimpleNamespace(transport=transport)
    threat = {"id": 3, "type": "minecraft:zombie", "distance": 6.0}
    scans = iter(([threat], []))
    defended = []
    shelter_attempts = []
    monkeypatch.setattr(
        "baritone_client.common.combat.scan_for_threats",
        lambda *_args, **_kwargs: next(scans),
    )
    monkeypatch.setattr(
        "baritone_client.common.combat.defend_or_flee",
        lambda _client: defended.append(True) or True,
    )
    monkeypatch.setattr(
        "baritone_client.common.base.build_compact_night_shelter",
        lambda _client: shelter_attempts.append(True) or True,
    )

    assert wait_for_safe_daylight(client, max_wait=1.0, poll_interval=0.0)
    assert defended == [True]
    assert shelter_attempts == []


def test_completed_night_shelter_never_engages_outside_threats(monkeypatch):
    transport = SequenceTransport(
        [
            {"world_time": 16000, "is_dead": False, "health": 20},
            {"world_time": 17000, "is_dead": False, "health": 20},
            {"world_time": 100, "is_dead": False, "health": 20},
        ]
    )
    client = SimpleNamespace(transport=transport)
    scans = []
    defended = []
    monkeypatch.setattr(
        "baritone_client.common.combat.scan_for_threats",
        lambda *_args, **_kwargs: scans.append(True) or [],
    )
    monkeypatch.setattr(
        "baritone_client.common.combat.defend_or_flee",
        lambda _client: defended.append(True) or True,
    )
    monkeypatch.setattr(
        "baritone_client.common.base.build_compact_night_shelter",
        lambda _client: True,
    )

    assert wait_for_safe_daylight(client, max_wait=1.0, poll_interval=0.0)
    assert scans == [True]
    assert defended == []


def test_compact_shelter_builds_roof_anchor_before_center(monkeypatch):
    world = {}

    class ShelterTransport:
        def dispatch(self, route, payload):
            if route == "get_state":
                return {"block_position": {"x": 0, "y": 64, "z": 0}}
            if route == "get_block":
                key = (payload["x"], payload["y"], payload["z"])
                return {"id": world.get(key, "minecraft:air")}
            return {}

    client = SimpleNamespace(transport=ShelterTransport())
    monkeypatch.setattr(
        base,
        "count_item",
        lambda _client, item_id: 20 if item_id == "minecraft:cobblestone" else 0,
    )
    monkeypatch.setattr(harness_ops, "available", lambda: True)
    monkeypatch.setattr(
        harness_ops,
        "place_block_exact",
        lambda _client, x, y, z, item_id, **_kwargs: world.__setitem__(
            (x, y, z), item_id
        )
        or True,
    )
    monkeypatch.setattr(
        "baritone_client.common.combat.scan_for_threats",
        lambda *_args, **_kwargs: [],
    )

    assert base.build_compact_night_shelter(client)
    assert world[(0, 66, 0)] == "minecraft:cobblestone"
    assert any(
        world.get(position) == "minecraft:cobblestone"
        for position in ((1, 66, 0), (-1, 66, 0), (0, 66, 1), (0, 66, -1))
    )


def test_compact_shelter_builds_foundation_before_unsupported_wall(monkeypatch):
    world = {(0, 63, 0): "minecraft:stone"}
    placements = []

    class ShelterTransport:
        def dispatch(self, route, payload):
            if route == "get_state":
                return {"block_position": {"x": 0, "y": 64, "z": 0}}
            if route == "get_block":
                key = (payload["x"], payload["y"], payload["z"])
                return {"id": world.get(key, "minecraft:air")}
            return {}

    client = SimpleNamespace(transport=ShelterTransport())
    monkeypatch.setattr(
        base,
        "count_item",
        lambda _client, item_id: 30 if item_id == "minecraft:cobblestone" else 0,
    )
    monkeypatch.setattr(harness_ops, "available", lambda: True)

    def place(_client, x, y, z, item_id, **_kwargs):
        placements.append((x, y, z))
        world[(x, y, z)] = item_id
        return True

    monkeypatch.setattr(harness_ops, "place_block_exact", place)
    monkeypatch.setattr(
        "baritone_client.common.combat.scan_for_threats",
        lambda *_args, **_kwargs: [],
    )

    assert base.build_compact_night_shelter(client)
    assert placements.index((1, 63, 0)) < placements.index((1, 64, 0))


def test_bootstrap_keeps_safe_exploration_endpoint(monkeypatch):
    transport = SequenceTransport(
        [{"world_time": 1000, "is_dead": False, "health": 20, "block_position": {"x": 1, "y": 80, "z": 2}}]
    )
    client = SimpleNamespace(
        transport=transport,
        mission=SimpleNamespace(macro=lambda *_args, **_kwargs: {"settingsApplied": 5}),
    )
    monkeypatch.setattr(spawn_bootstrap, "explore_until", lambda *_args, **_kwargs: False)

    result = SpawnBootstrapHandler().execute(client, ReadyResources(), SimpleNamespace())

    assert result.success
    assert not any(route == "goto" for route, _payload in transport.calls)
    assert any(route == "cancel" for route, _payload in transport.calls)


def test_bootstrap_rejects_aquatic_exploration_endpoint(monkeypatch):
    base_state = {
        "world_time": 1000,
        "is_dead": False,
        "health": 20,
        "block_position": {"x": 308, "y": 66, "z": 533},
    }
    aquatic_state = {
        **base_state,
        "block_position": {"x": 312, "y": 55, "z": 576},
    }

    class AquaticEndpointTransport(SequenceTransport):
        def dispatch(self, route, payload, **kwargs):
            if route == "get_block":
                self.calls.append((route, payload))
                return {"id": "minecraft:water"}
            return super().dispatch(route, payload, **kwargs)

    transport = AquaticEndpointTransport(
        [base_state, base_state, aquatic_state]
    )
    client = SimpleNamespace(
        transport=transport,
        mission=SimpleNamespace(
            macro=lambda *_args, **_kwargs: {"settingsApplied": 5}
        ),
    )
    monkeypatch.setattr(
        spawn_bootstrap,
        "explore_until",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        spawn_bootstrap,
        "reach_dry_surface",
        lambda *_args, **_kwargs: None,
        raising=False,
    )

    result = SpawnBootstrapHandler().execute(
        client,
        ReadyResources(),
        SimpleNamespace(),
    )

    assert not result.success
    assert "dry exploration endpoint" in result.reason


def test_initial_gathering_stops_when_daylight_gate_fails(monkeypatch):
    client = SimpleNamespace(transport=SequenceTransport([{"world_time": 18000, "is_dead": False}]))
    monkeypatch.setattr(initial_gathering, "sleep_through_night", lambda _client: False)
    monkeypatch.setattr(initial_gathering, "wait_for_safe_daylight", lambda _client: False)

    result = InitialGatheringHandler().execute(client, ReadyResources(), SimpleNamespace())

    assert not result.success
    assert "safe daylight" in result.reason
