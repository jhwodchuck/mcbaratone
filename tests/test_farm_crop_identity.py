"""Sanitized controller tests for preserving the wheat plot's crop identity."""

from types import SimpleNamespace

from baritone_client.common import farming
from baritone_client.common import farm_crop_identity as identity


def _crop(crop_id, age):
    return {"id": crop_id, "state": {"age": age}}


def test_harvest_repairs_only_observed_mature_wheat_replaced_by_immature_crop(monkeypatch):
    position = (-1, 65, 0)
    mature_carrot = (1, 65, 0)
    blocks = {
        position: _crop("minecraft:wheat", 7),
        mature_carrot: _crop("minecraft:carrots", 7),
        (-1, 64, 0): {"id": "minecraft:farmland"},
    }
    calls = []
    wheat_count = [0]

    def dispatch(route, payload):
        calls.append((route, payload))
        if route == "get_block":
            pos = (payload["x"], payload["y"], payload["z"])
            return blocks.get(pos, {"id": "minecraft:air"})
        if route == "farm":
            blocks[position] = _crop("minecraft:carrots", 1)
            wheat_count[0] = 1
        if route == "cancel":
            return {"cancelled": True}
        if route == "get_state":
            return {"is_pathing": False}
        if route == "dig_block":
            pos = (payload["x"], payload["y"], payload["z"])
            blocks[pos] = {"id": "minecraft:air"}
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    monkeypatch.setattr(farming, "goto", lambda *_a, **_k: True)
    monkeypatch.setattr(farming, "count_item", lambda *_a: wheat_count[0])
    monkeypatch.setattr(farming, "farm_surface_safe", lambda *_a: True)
    monkeypatch.setattr(
        "baritone_client.common.farm_planting._within_block_reach",
        lambda *_a: True,
    )

    planted = []

    def plant(_client, x, y, z):
        planted.append((x, y, z))
        blocks[(x, y + 1, z)] = _crop("minecraft:wheat", 0)
        return True

    monkeypatch.setattr(farming, "_till_and_plant_tile", plant)
    monkeypatch.setattr(farming, "replant_empty_wheat_tiles", lambda *_a: 0)
    monkeypatch.setattr(identity.time, "sleep", lambda _seconds: None)

    assert identity.run_wheat_farm_harvest(client, 0, 64, 0, 8) is True
    assert blocks[position]["id"] == "minecraft:wheat"
    assert blocks[mature_carrot]["id"] == "minecraft:carrots"
    assert planted == [(-1, 64, 0)]
    digs = [payload for route, payload in calls if route == "dig_block"]
    assert digs == [{"x": -1, "y": 65, "z": 0, "face": "UP", "max_ticks": 80}]


def test_crop_snapshot_fails_closed_on_an_unknown_block_read():
    def dispatch(route, _payload):
        if route == "get_block":
            return {}
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))

    assert identity._capture_mature_wheat(client, 0, 64, 0) is None


def test_unacknowledged_cancel_never_repairs_crop_identity(monkeypatch):
    position = (-1, 65, 0)
    blocks = {position: _crop("minecraft:wheat", 7)}
    calls = []
    farm_started = [False]

    def dispatch(route, payload):
        calls.append(route)
        if route == "get_block":
            pos = (payload["x"], payload["y"], payload["z"])
            return blocks.get(pos, {"id": "minecraft:air"})
        if route == "farm":
            blocks[position] = _crop("minecraft:carrots", 1)
            farm_started[0] = True
        if route == "cancel":
            return {"cancelled": False}
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    monkeypatch.setattr(farming, "goto", lambda *_a, **_k: True)
    monkeypatch.setattr(
        farming, "count_item",
        lambda *_a: 1 if farm_started[0] else 0,
    )
    monkeypatch.setattr(farming, "farm_surface_safe", lambda *_a: True)
    monkeypatch.setattr(identity.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        farming,
        "replant_empty_wheat_tiles",
        lambda *_a: (_ for _ in ()).throw(AssertionError("must wait for confirmed stop")),
    )

    assert identity.run_wheat_farm_harvest(client, 0, 64, 0, 8) is False
    assert "dig_block" not in calls
