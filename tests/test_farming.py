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
            (546, 71, -272): "minecraft:short_grass",
        }
    )
    assert farming.find_farm_surface_near(client, 546, 79, -272) == (
        546,
        70,
        -272,
    )


def test_find_farm_surface_near_uses_bounded_view_for_nearby_soil():
    voxels = [
        {"x": 555, "y": 71, "z": -264, "id": "minecraft:grass_block"},
        {"x": 555, "y": 72, "z": -264, "id": "minecraft:wildflowers"},
    ]
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, _payload: {"voxels": voxels}
            if route == "get_view"
            else {"id": "minecraft:air"}
        )
    )

    assert farming.find_farm_surface_near(
        client, 553, 74, -268, horizontal_radius=20
    ) == (555, 71, -264)


def test_find_farm_surface_near_prefers_a_usable_patch_over_isolated_soil():
    voxels = [
        {"x": 1, "y": 64, "z": 0, "id": "minecraft:grass_block"},
        {"x": 1, "y": 65, "z": 0, "id": "minecraft:air"},
    ]
    for x in range(5, 8):
        for z in range(-1, 2):
            voxels.extend(
                [
                    {"x": x, "y": 64, "z": z, "id": "minecraft:grass_block"},
                    {"x": x, "y": 65, "z": z, "id": "minecraft:air"},
                ]
            )
    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=lambda _route, _payload: {"voxels": voxels})
    )

    assert farming.find_farm_surface_near(
        client, 0, 65, 0, horizontal_radius=8
    ) == (5, 64, 0)

def test_ensure_farm_water_places_bucket_when_already_carried(monkeypatch):
    blocks = {
        (10, 63, 10): "minecraft:stone",
        (10, 64, 10): "minecraft:grass_block",
    }
    def extra(route, payload, blocks_map):
        if route == "dig_block":
            blocks_map[(payload["x"], payload["y"], payload["z"])] = "minecraft:air"
            return {"started": True}
        if route == "use_item":
            blocks_map[(10, 64, 10)] = "minecraft:water"
            return {"accepted": True}
        return None
    client, calls, blocks = _client(blocks=blocks, dispatch_extra=extra)

    monkeypatch.setattr(farming, "count_item", lambda _c, item: 1 if item == "minecraft:water_bucket" else 0)
    monkeypatch.setattr(farming, "select_item", lambda *_a, **_k: True)
    monkeypatch.setattr(farming, "goto", lambda *_a, **_k: True)
    monkeypatch.setattr(farming.time, "sleep", lambda _seconds: None)

    assert farming.ensure_farm_water(client, 10, 64, 10) is True
    assert ("look_at", {"x": 10.5, "y": 63.5, "z": 10.5}) in calls
    assert (
        "dig_block",
        {"x": 10, "y": 64, "z": 10, "face": "UP", "max_ticks": 160},
    ) in calls
    assert ("use_item", {"duration_ms": 0}) in calls


def test_ensure_farm_water_clears_replaceable_center_vegetation(monkeypatch):
    blocks = {
        (10, 63, 10): "minecraft:stone",
        (10, 64, 10): "minecraft:grass_block",
        (10, 65, 10): "minecraft:wildflowers",
    }

    def extra(route, payload, blocks_map):
        target = (payload.get("x"), payload.get("y"), payload.get("z"))
        if route == "attack_block":
            blocks_map[target] = "minecraft:air"
            return {"started": True}
        if route == "dig_block":
            blocks_map[target] = "minecraft:air"
            return {"started": True}
        if route == "use_item":
            blocks_map[(10, 64, 10)] = "minecraft:water"
            return {"accepted": True}
        return None

    client, calls, _blocks = _client(blocks=blocks, dispatch_extra=extra)
    monkeypatch.setattr(
        farming,
        "count_item",
        lambda _client, item: 1 if item == "minecraft:water_bucket" else 0,
    )
    monkeypatch.setattr(farming, "select_item", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(farming, "goto", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(farming.time, "sleep", lambda _seconds: None)

    assert farming.ensure_farm_water(client, 10, 64, 10) is True
    assert ("attack_block", {"x": 10, "y": 65, "z": 10}) in calls


def test_ensure_farm_water_fills_bucket_first_when_needed(monkeypatch):
    blocks = {
        (20, 60, 20): "minecraft:water",
        (0, 63, 0): "minecraft:stone",
        (0, 64, 0): "minecraft:grass_block",
    }
    uses = {"count": 0}
    def extra(route, payload, blocks_map):
        if route == "dig_block":
            blocks_map[(payload["x"], payload["y"], payload["z"])] = "minecraft:air"
            return {"started": True}
        if route == "use_item":
            uses["count"] += 1
            if uses["count"] == 2:
                blocks_map[(0, 64, 0)] = "minecraft:water"
            return {"accepted": True}
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
    selections = []
    monkeypatch.setattr(
        farming,
        "select_item",
        lambda _client, item, **kwargs: selections.append((item, kwargs)) or True,
    )
    monkeypatch.setattr(farming, "goto", lambda *_a, **_k: True)
    monkeypatch.setattr(farming.time, "sleep", lambda _s: filled.__setitem__("n", 1))

    assert farming.ensure_farm_water(client, 0, 64, 0) is True
    assert all(options.get("allow_swap") is True for _item, options in selections)


def test_ensure_farm_water_repairs_elevated_source_that_floods_crops(monkeypatch):
    blocks = {
        (10, 63, 10): "minecraft:stone",
        (10, 64, 10): "minecraft:cobblestone",
        (10, 65, 10): "minecraft:water",
    }
    inventory = {"minecraft:bucket": 1, "minecraft:water_bucket": 0}
    selected = {"item": None}
    selections = []

    def extra(route, payload, blocks_map):
        target = (payload.get("x"), payload.get("y"), payload.get("z"))
        if route == "dig_block" and str(selected["item"]).endswith("_pickaxe"):
            blocks_map[target] = "minecraft:air"
            return {"started": True}
        if route == "use_item" and selected["item"] == "minecraft:bucket":
            blocks_map[(10, 65, 10)] = "minecraft:air"
            inventory["minecraft:bucket"] -= 1
            inventory["minecraft:water_bucket"] += 1
            return {"accepted": True}
        if route == "use_item" and selected["item"] == "minecraft:water_bucket":
            blocks_map[(10, 64, 10)] = "minecraft:water"
            inventory["minecraft:water_bucket"] -= 1
            inventory["minecraft:bucket"] += 1
            return {"accepted": True}
        return None

    client, calls, _blocks = _client(blocks=blocks, dispatch_extra=extra)
    monkeypatch.setattr(
        farming, "count_item", lambda _client, item: inventory.get(item, 0)
    )
    def select(_client, item, **_kwargs):
        if item not in {
            "minecraft:bucket",
            "minecraft:water_bucket",
            "minecraft:iron_pickaxe",
        }:
            return False
        selected["item"] = item
        selections.append(item)
        return True

    monkeypatch.setattr(farming, "select_item", select)
    monkeypatch.setattr(farming, "goto", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(farming.time, "sleep", lambda _seconds: None)

    assert farming.ensure_farm_water(client, 10, 64, 10) is True
    assert blocks[(10, 64, 10)] == "minecraft:water"
    assert blocks[(10, 65, 10)] == "minecraft:air"
    assert ("look_at", {"x": 10.5, "y": 65.5, "z": 10.5}) in calls
    assert ("look_at", {"x": 10.5, "y": 63.5, "z": 10.5}) in calls
    assert "minecraft:iron_pickaxe" in selections
    assert selections.index("minecraft:iron_pickaxe") < selections.index(
        "minecraft:water_bucket"
    )


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
    arrivals = []
    monkeypatch.setattr(
        farming,
        "goto",
        lambda _client, x, y, z, **kwargs: arrivals.append(
            ((x, y, z), kwargs)
        )
        or True,
    )
    monkeypatch.setattr(farming, "select_item", lambda *_a, **_k: True)
    monkeypatch.setattr(farming, "craft", lambda *_a, **_k: True)
    monkeypatch.setattr(farming, "count_item", lambda _c, item: 99 if "hoe" in item or "seed" in item else 0)
    monkeypatch.setattr(farming.time, "sleep", lambda _s: None)

    result = farming.establish_wheat_farm(client, 0, 64, 0, size=5)
    assert result == (0, 64, 0)
    assert all(
        options == {"timeout": 20, "tolerance": 3.5}
        for _position, options in arrivals
    )
    # At least one tile was actually tilled then planted.
    assert any(b == "minecraft:wheat" for b in blocks.values())


def test_establish_wheat_farm_uses_carried_starter_seed_batch(monkeypatch):
    client, _calls, _blocks = _client()
    targets = []
    monkeypatch.setattr(farming, "ensure_farm_water", lambda *_a, **_k: True)
    monkeypatch.setattr(
        farming,
        "count_item",
        lambda _client, item: 20 if item == "minecraft:wheat_seeds" else 0,
    )
    monkeypatch.setattr(
        farming,
        "_gather_seeds",
        lambda _client, needed: targets.append(needed) or True,
    )
    monkeypatch.setattr(farming, "goto", lambda *_a, **_k: False)

    assert farming.establish_wheat_farm(client, 0, 64, 0, size=5) is None
    assert targets == [20]


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


def test_place_farm_soil_falls_back_to_the_ground_plane(monkeypatch):
    """Standing on open ground, the plot belongs one block below the bot.

    Searching only the bot's own level misses this, which is the common case
    on flat terrain -- the bot's own plane has no solid support under it.
    """
    blocks = {
        # Bot at (10, 66, 20). Nothing supports a block at y=66 (all air at
        # y=65), but (11, 65, 21) is open with stone under it at y=64.
        (11, 64, 21): "minecraft:stone",
    }

    def extra(route, payload, _blocks):
        if route == "get_state":
            return {"block_position": {"x": 10, "y": 66, "z": 20}}
        return None

    client, _calls, blocks = _client(blocks=blocks, dispatch_extra=extra)
    monkeypatch.setattr(
        farming, "count_item",
        lambda _c, item: 8 if item == "minecraft:dirt" else 0,
    )
    placed = []
    monkeypatch.setattr(
        farming, "robust_place",
        lambda _c, x, y, z, item_id: (
            placed.append((x, y, z))
            or blocks.__setitem__((x, y, z), "minecraft:dirt")
            or True
        ),
    )

    result = farming.place_farm_soil(client, _block_id_reader(client))

    assert result == (11, 65, 21)
    assert placed == [(11, 65, 21)]


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


def test_find_natural_crop_center_does_not_let_buried_dirt_mask_grass(monkeypatch):
    client, _calls, _ = _client()
    searches = []

    def find(_client, blocks, radius):
        searches.append(tuple(blocks))
        return (553, 73, -268) if "minecraft:grass_block" in blocks else None

    monkeypatch.setattr(farming, "find_nearby_block", find)
    monkeypatch.setattr(
        farming,
        "surface_soil",
        lambda _block_id, candidate, max_rise=6: candidate,
    )

    center, irrigated = farming.find_natural_crop_center(
        client, lambda *_args: "minecraft:air"
    )

    assert center == (553, 73, -268)
    assert searches[0] == ("minecraft:farmland", "minecraft:grass_block")
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
