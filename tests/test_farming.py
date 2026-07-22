from types import SimpleNamespace

from baritone_client.common import farming


def _client(blocks=None, dispatch_extra=None):
    """Client whose get_block reads from a mutable {(x,y,z): id} map."""
    blocks = blocks if blocks is not None else {}
    calls = []

    def dispatch(route, payload=None):
        calls.append((route, payload))
        payload = payload or {}
        if route == "get_block":
            key = (payload.get("x"), payload.get("y"), payload.get("z"))
            return {"id": blocks.get(key, "minecraft:air")}
        if dispatch_extra is not None:
            result = dispatch_extra(route, payload, blocks)
            if result is not None:
                return result
        return {}

    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    return client, calls, blocks


def test_ensure_farm_water_returns_true_when_already_present(monkeypatch):
    client, calls, _ = _client(blocks={(0, 64, 0): "minecraft:water"})
    monkeypatch.setattr(
        farming, "select_item",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not touch inventory if water exists")),
    )
    assert farming.ensure_farm_water(client, 0, 64, 0) is True
    assert not any(r == "place_block" for r, _ in calls)


def test_ensure_farm_water_places_bucket_when_already_carried(monkeypatch):
    blocks = {}
    def extra(route, payload, blocks_map):
        if route == "place_block" and payload.get("item") == "minecraft:water_bucket":
            blocks_map[(payload["x"], payload["y"], payload["z"])] = "minecraft:water"
            return {"placed": True}
        return None
    client, calls, blocks = _client(blocks=blocks, dispatch_extra=extra)

    monkeypatch.setattr(farming, "count_item", lambda _c, item: 1 if item == "minecraft:water_bucket" else 0)
    monkeypatch.setattr(farming, "select_item", lambda *_a, **_k: True)
    monkeypatch.setattr(farming, "goto", lambda *_a, **_k: True)

    assert farming.ensure_farm_water(client, 10, 64, 10) is True
    assert ("place_block", {"x": 10, "y": 65, "z": 10, "item": "minecraft:water_bucket"}) in calls


def test_ensure_farm_water_fills_bucket_first_when_needed(monkeypatch):
    blocks = {(20, 60, 20): "minecraft:water"}
    def extra(route, payload, blocks_map):
        if route == "place_block" and payload.get("item") == "minecraft:water_bucket":
            blocks_map[(payload["x"], payload["y"], payload["z"])] = "minecraft:water"
            return {"placed": True}
        return None
    client, calls, blocks = _client(blocks=blocks, dispatch_extra=extra)

    monkeypatch.setattr(farming, "find_nearby_block", lambda *_a, **_k: (20, 60, 20))
    filled = {"n": 0}
    def count_item(_c, item):
        if item == "minecraft:bucket":
            return 1
        if item == "minecraft:water_bucket":
            return filled["n"]
        return 0
    monkeypatch.setattr(farming, "count_item", count_item)
    monkeypatch.setattr(farming, "select_item", lambda *_a, **_k: True)
    monkeypatch.setattr(farming, "goto", lambda *_a, **_k: True)
    monkeypatch.setattr(farming.time, "sleep", lambda _s: filled.__setitem__("n", 1))

    assert farming.ensure_farm_water(client, 0, 64, 0) is True


def test_ensure_farm_water_fails_with_no_source_and_no_bucket(monkeypatch):
    client, _calls, _ = _client()
    monkeypatch.setattr(farming, "count_item", lambda *_a: 0)
    monkeypatch.setattr(farming, "find_nearby_block", lambda *_a, **_k: None)
    assert farming.ensure_farm_water(client, 0, 64, 0) is False


def test_establish_wheat_farm_returns_none_when_water_fails(monkeypatch):
    client, _calls, _ = _client()
    monkeypatch.setattr(farming, "ensure_farm_water", lambda *_a, **_k: False)
    assert farming.establish_wheat_farm(client, 0, 64, 0) is None


def test_establish_wheat_farm_tills_and_plants_tiles(monkeypatch):
    # All tiles start as grass_block; farming.py tills then plants wheat above.
    blocks = {}
    for dx in range(-2, 3):
        for dz in range(-2, 3):
            if dx == 0 and dz == 0:
                continue
            blocks[(dx, 64, dz)] = "minecraft:grass_block"

    def extra(route, payload, blocks_map):
        if route == "interact_block":
            x, y, z = payload["x"], payload["y"], payload["z"]
            current = blocks_map.get((x, y, z), "minecraft:air")
            if current in ("minecraft:dirt", "minecraft:grass_block"):
                blocks_map[(x, y, z)] = "minecraft:farmland"
            elif current == "minecraft:farmland":
                blocks_map[(x, y + 1, z)] = "minecraft:wheat"
            return {}
        return None

    client, calls, blocks = _client(blocks=blocks, dispatch_extra=extra)
    monkeypatch.setattr(farming, "ensure_farm_water", lambda *_a, **_k: True)
    monkeypatch.setattr(farming, "goto", lambda *_a, **_k: True)
    monkeypatch.setattr(farming, "select_item", lambda *_a, **_k: True)
    monkeypatch.setattr(farming, "craft", lambda *_a, **_k: True)
    monkeypatch.setattr(farming, "count_item", lambda _c, item: 99 if "hoe" in item or "seed" in item else 0)
    monkeypatch.setattr(farming.time, "sleep", lambda _s: None)

    result = farming.establish_wheat_farm(client, 0, 64, 0, size=5)
    assert result == (0, 64, 0)
    # At least one tile was actually tilled then planted.
    assert any(b == "minecraft:wheat" for b in blocks.values())


def test_harvest_wheat_farm_confirms_via_wheat_increase(monkeypatch):
    client, calls, _ = _client()
    monkeypatch.setattr(farming, "goto", lambda *_a, **_k: True)
    counts = iter([0, 0, 3])
    monkeypatch.setattr(farming, "count_item", lambda *_a: next(counts, 3))
    monkeypatch.setattr(farming.time, "sleep", lambda _s: None)

    assert farming.harvest_wheat_farm(client, 0, 64, 0) is True
    assert ("farm", {"range": 8}) in calls
    assert ("cancel", {}) in calls


def test_harvest_wheat_farm_fails_when_no_wheat_appears(monkeypatch):
    client, _calls, _ = _client()
    monkeypatch.setattr(farming, "goto", lambda *_a, **_k: True)
    monkeypatch.setattr(farming, "count_item", lambda *_a: 0)
    monkeypatch.setattr(farming.time, "sleep", lambda _s: None)
    t = {"v": 0.0}
    def monotonic():
        t["v"] += 30.0
        return t["v"]
    monkeypatch.setattr(farming.time, "monotonic", monotonic)

    assert farming.harvest_wheat_farm(client, 0, 64, 0) is False


def test_harvest_wheat_farm_fails_if_cannot_reach(monkeypatch):
    client, _calls, _ = _client()
    monkeypatch.setattr(farming, "goto", lambda *_a, **_k: False)
    monkeypatch.setattr(
        farming, "count_item",
        lambda *_a: (_ for _ in ()).throw(AssertionError("must not harvest if never reached")),
    )
    assert farming.harvest_wheat_farm(client, 0, 64, 0) is False
