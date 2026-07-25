from types import SimpleNamespace

from baritone_client.automator.phases import end_game
from baritone_client.automator.state_manager import Phase, StateManager
from baritone_client.common import end


def _frames():
    return [
        {"x": index, "y": 30, "z": 10, "block": "minecraft:end_portal_frame"}
        for index in range(12)
    ]


def test_find_end_portal_requires_actual_frame_ring(monkeypatch):
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, payload: {"found": _frames()}
            if route == "find_blocks"
            else {}
        )
    )
    assert end.find_end_portal(client, timeout=1) == (6, 30, 10)


def test_activate_end_portal_verifies_eyes_and_active_blocks():
    calls = []

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "find_blocks" and payload["blocks"] == ["minecraft:end_portal_frame"]:
            return {"found": _frames()}
        if route == "get_block":
            return {"id": "minecraft:end_portal_frame", "state": {"eye": "true"}}
        if route == "find_blocks":
            return {"found": [{"x": 6, "y": 30, "z": 10, "block": "minecraft:end_portal"}]}
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    assert end.activate_end_portal(client, timeout=1) is True
    assert sum(route == "get_block" for route, _ in calls) == 12
    assert not any(route == "interact_block" for route, _ in calls)


def test_enter_end_portal_moves_into_active_block(monkeypatch):
    dimensions = iter(("minecraft:overworld", "minecraft:the_end"))

    def dispatch(route, payload):
        if route == "find_blocks":
            return {"found": [{"x": 4, "y": 31, "z": 8, "block": "minecraft:end_portal"}]}
        if route == "get_state":
            return {"dimension": next(dimensions)}
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    moved = []
    monkeypatch.setattr(
        "baritone_client.common.navigation.goto",
        lambda _c, x, y, z, **_k: moved.append((x, y, z)) or True,
    )
    monkeypatch.setattr(end.time, "sleep", lambda _seconds: None)
    assert end.enter_end_portal(client, timeout=1) is True
    assert moved == [(4, 31, 8)]


def test_dragon_fight_targets_entity_ids_and_requires_exit_portal(monkeypatch):
    entity_reads = iter(
        (
            {
                "entities": [
                    {
                        "id": 91,
                        "type": "minecraft:end_crystal",
                        "distance": 3,
                        "position": {"x": 1, "y": 65, "z": 2},
                    }
                ]
            },
            {"entities": []},
        )
    )
    calls = []

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "get_entities":
            return next(entity_reads)
        if route == "find_blocks":
            return {"found": [{"x": 0, "y": 64, "z": 0, "block": "minecraft:end_portal"}]}
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    monkeypatch.setattr("baritone_client.common.navigation.goto", lambda *_a, **_k: True)
    monkeypatch.setattr("baritone_client.common.inventory.select_item", lambda *_a, **_k: False)
    monkeypatch.setattr(end.time, "sleep", lambda _seconds: None)
    assert end.fight_ender_dragon(client, timeout=1) is True
    assert ("attack_entity", {"entity_id": 91}) in calls


def test_world_unlock_persists_real_milestones(monkeypatch, tmp_path):
    state = StateManager(tmp_path)
    handler = end_game.WorldUnlockHandler()
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, payload: {
                "block_position": {"x": 0, "y": 64, "z": 0},
                "dimension": "minecraft:overworld",
            }
        )
    )
    monkeypatch.setattr(end_game, "triangulate_stronghold", lambda _client: (100, 200))
    monkeypatch.setattr(end_game, "enter_end_portal", lambda *_a, **_k: True)
    monkeypatch.setattr(end_game, "fight_ender_dragon", lambda *_a, **_k: True)

    resources = SimpleNamespace(
        refresh_inventory=lambda: {},
        get_summary=lambda: {"inventory": {}},
    )
    assert handler._locate_stronghold(client, state, resources)
    state.custom_data["end_portal"] = [101, 30, 201]
    assert handler._enter_end(client, state, resources)
    assert handler._kill_dragon(client, state, resources)
    assert state.custom_data["stronghold_coords"] == [100, 200]
    assert state.custom_data["milestones"] == {
        "end_entered": True,
        "dragon_defeated": True,
    }
    payload = state.get_phase_payload(Phase.WORLD_UNLOCK)
    assert payload["stronghold_coords"] == [100, 200]
    assert payload["dragon_defeated"] is True


def test_gateway_traversal_requires_verified_displacement(monkeypatch):
    states = iter(
        (
            {
                "dimension": "minecraft:the_end",
                "block_position": {"x": 0, "y": 64, "z": 0},
            },
            {
                "dimension": "minecraft:the_end",
                "block_position": {"x": 1000, "y": 70, "z": 1000},
            },
        )
    )

    def dispatch(route, payload):
        if route == "get_state":
            return next(states)
        if route == "find_blocks":
            return {
                "found": [
                    {"x": 80, "y": 75, "z": 0, "block": "minecraft:end_gateway"}
                ]
            }
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    monkeypatch.setattr("baritone_client.common.inventory.select_item", lambda *_a: True)
    monkeypatch.setattr("baritone_client.common.navigation.goto", lambda *_a, **_k: True)
    assert end.traverse_end_gateway(client, timeout=1) == (80, 75, 0)


def test_end_city_discovery_requires_purpur_cluster():
    purpur = [
        {"x": i, "y": 70, "z": 30, "block": "minecraft:purpur_block"}
        for i in range(20)
    ]

    def dispatch(route, payload):
        if route == "get_state":
            return {"block_position": {"x": 0, "y": 64, "z": 0}}
        if route == "find_blocks":
            return {"found": purpur}
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    assert end.find_end_city(client, timeout=1) == (10, 70, 30)


def test_elytra_acquisition_attacks_exact_item_frame(monkeypatch):
    inventory = {"minecraft:elytra": 0}
    calls = []

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "get_entities":
            return {
                "entities": [
                    {
                        "id": 44,
                        "type": "minecraft:item_frame",
                        "distance": 2,
                        "position": {"x": 4, "y": 80, "z": 5},
                    }
                ]
            }
        if route == "attack_entity":
            inventory["minecraft:elytra"] = 1
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    monkeypatch.setattr(
        "baritone_client.common.inventory.count_item",
        lambda _client, item: inventory.get(item, 0),
    )
    monkeypatch.setattr("baritone_client.common.navigation.goto", lambda *_a, **_k: True)
    monkeypatch.setattr(end.time, "sleep", lambda _seconds: None)
    assert end.acquire_elytra(client, timeout=1)
    assert ("attack_entity", {"entity_id": 44}) in calls
