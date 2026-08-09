from types import SimpleNamespace

from baritone_client.common import farming
from baritone_client.common import inventory


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


def test_find_farm_surface_near_drops_from_platform_height():
    client, _calls, _ = _client(
        blocks={
            (546, 70, -272): "minecraft:grass_block",
            (546, 71, -272): "minecraft:air",
        }
    )

    assert farming.find_farm_surface_near(client, 546, 79, -272) == (
        546,
        70,
        -272,
    )


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


def test_farm_bucket_is_recovered_from_checkpointed_storage(monkeypatch):
    client, _calls, _ = _client()
    carried = {"minecraft:bucket": 0}
    requests = []

    monkeypatch.setattr(
        farming,
        "count_item",
        lambda _client, item: carried.get(item, 0),
    )

    def withdraw(_client, requirements, **kwargs):
        requests.append((requirements, kwargs))
        carried["minecraft:bucket"] = 1
        return 1

    monkeypatch.setattr(inventory, "withdraw_required_from_catalog", withdraw)
    state = SimpleNamespace(checkpoint_dir="checkpoint")

    assert farming._ensure_farm_bucket(client, state) is True
    assert requests[0][0] == {"minecraft:bucket": 1}
    assert requests[0][1]["max_containers"] == 12
    assert requests[0][1]["allow_recovery_access"] is True


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


def _block_id_reader(client):
    def block_id(x, y, z):
        return client.transport.dispatch("get_block", {"x": x, "y": y, "z": z}).get("id", "")

    return block_id


def test_place_farm_soil_converts_bare_rock_using_carried_dirt(monkeypatch):
    """The live A1 failure: 31 carried dirt, bare mountain, never once tried."""
    blocks = {
        # Bot stands at (10, 65, 10) on solid, untillable stone -- no
        # farmland/dirt/grass_block anywhere nearby for find_nearby_block to
        # match. One neighboring column has open air over stone, ready to
        # receive a placed dirt block.
        (10, 64, 10): "minecraft:stone",
        (11, 64, 11): "minecraft:stone",
        (11, 65, 11): "minecraft:air",
    }

    def extra(route, payload, _blocks):
        if route == "get_state":
            return {"block_position": {"x": 10, "y": 65, "z": 10}}
        return None

    client, _calls, blocks = _client(blocks=blocks, dispatch_extra=extra)
    monkeypatch.setattr(
        farming, "count_item",
        lambda _c, item: 31 if item == "minecraft:dirt" else 0,
    )
    placed = []
    monkeypatch.setattr(
        farming, "robust_place",
        lambda _c, x, y, z, item_id: (
            placed.append((x, y, z, item_id))
            or blocks.__setitem__((x, y, z), "minecraft:dirt")
            or True
        ),
    )

    result = farming.place_farm_soil(client, _block_id_reader(client))

    assert result == (11, 65, 11)
    assert placed == [(11, 65, 11, "minecraft:dirt")]


def test_place_farm_soil_gives_up_without_carried_soil(monkeypatch):
    """No dirt, coarse dirt, or grass block carried -- nothing to place."""

    def extra(route, payload, _blocks):
        if route == "get_state":
            return {"block_position": {"x": 10, "y": 65, "z": 10}}
        return None

    client, _calls, _ = _client(
        blocks={(11, 64, 11): "minecraft:stone", (11, 65, 11): "minecraft:air"},
        dispatch_extra=extra,
    )
    monkeypatch.setattr(farming, "count_item", lambda *_a: 0)

    result = farming.place_farm_soil(client, _block_id_reader(client))

    assert result is None


def test_find_natural_crop_center_prefers_soil_over_water(monkeypatch):
    client, _calls, _ = _client()
    monkeypatch.setattr(
        farming, "find_nearby_block",
        lambda _c, blocks, radius: (5, 63, 5) if "minecraft:water" not in blocks else (9, 63, 9),
    )
    monkeypatch.setattr(
        farming, "surface_soil",
        lambda _block_id, candidate, max_rise=6: candidate,
    )

    center, irrigated = farming.find_natural_crop_center(client, lambda *_a: "minecraft:air")

    assert center == (5, 63, 5)
    assert irrigated is False


def test_find_natural_crop_center_falls_back_to_water(monkeypatch):
    client, _calls, _ = _client(blocks={(9, 63, 9): "minecraft:water"})
    monkeypatch.setattr(
        farming, "find_nearby_block",
        lambda _c, blocks, radius: (9, 63, 9) if "minecraft:water" in blocks else None,
    )

    center, irrigated = farming.find_natural_crop_center(client, _block_id_reader(client))

    assert center == (9, 63, 9)
    assert irrigated is True


def test_find_natural_crop_center_returns_none_when_nothing_found(monkeypatch):
    client, _calls, _ = _client()
    monkeypatch.setattr(farming, "find_nearby_block", lambda *_a, **_k: None)

    center, irrigated = farming.find_natural_crop_center(client, lambda *_a: "minecraft:air")

    assert center is None
    assert irrigated is False
    assert farming.harvest_wheat_farm(client, 0, 64, 0) is False
