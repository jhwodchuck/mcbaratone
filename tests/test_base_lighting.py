"""The live base gets lit, and stays lit, from local materials."""

from types import SimpleNamespace

import pytest

from baritone_client.automator import base_lighting


def _state(extra=None):
    custom = {
        "base_location": [100, 70, 100],
        "structures": {
            "starter_house": {
                "origin": [100, 70, 100],
                "furnace": [102, 71, 101],
                "bed": [104, 71, 103],
            }
        },
        "food_worker": {"farm_plots": [{"origin": [90, 70, 110]}]},
    }
    custom.update(extra or {})
    return SimpleNamespace(custom_data=custom)


class _World:
    """Ground at y=70 everywhere; a leaf canopy at y=76 west of x=95."""

    def __init__(self):
        self.placed = []
        self.torches = []
        self.water = set()
        self.transport = SimpleNamespace(dispatch=self.dispatch)

    def block(self, x, y, z):
        if (x, y, z) in self.placed or (x, y, z) in self.torches:
            return "minecraft:torch"
        if (x, z) in self.water and y == 70:
            return "minecraft:water"
        if y <= 70:
            return "minecraft:grass_block"
        if x < 95 and y == 76:
            return "minecraft:oak_leaves"
        return "minecraft:air"

    def dispatch(self, route, payload):
        if route == "get_block":
            return {"id": self.block(payload["x"], payload["y"], payload["z"])}
        if route == "find_blocks":
            return {"found": [{"x": x, "y": y, "z": z} for x, y, z in self.torches + self.placed]}
        return {}


@pytest.fixture
def world(monkeypatch):
    live = _World()
    items = {"minecraft:torch": 0}
    monkeypatch.setattr("baritone_client.common.navigation.goto", lambda *_a, **_k: True)
    monkeypatch.setattr(
        "baritone_client.common.inventory.count_item", lambda _c, item: items.get(item, 0)
    )

    def ensure(_client, _state, wanted, _anchor):
        items["minecraft:torch"] = max(items["minecraft:torch"], wanted)
        return items["minecraft:torch"]

    monkeypatch.setattr(base_lighting, "ensure_torches", ensure)

    def place(_client, x, y, z, block, allow_break=True):
        assert block == "minecraft:torch" and allow_break is False
        live.placed.append((x, y, z))
        items["minecraft:torch"] -= 1
        return True

    monkeypatch.setattr("baritone_client.common.harness_ops.place_block_exact", place)
    return SimpleNamespace(live=live, items=items)


def test_zone_covers_house_bed_furnace_and_farm_with_margin():
    x0, z0, x1, z1, y = base_lighting.lighting_zone(_state())
    assert (x0, z0, x1, z1) == (85, 95, 109, 115)
    assert y == 70


def test_far_stale_record_does_not_stretch_the_zone():
    state = _state({"homestead_anchor": [2000, 70, 2000]})
    x0, _z0, x1, _z1, _y = base_lighting.lighting_zone(state)
    assert x1 - x0 <= base_lighting.MAX_SPAN


def test_torch_spot_is_on_ground_under_canopy_and_never_on_water(world):
    assert base_lighting.torch_spot(world.live, 100, 100, 70) == (100, 71, 100)
    assert base_lighting.torch_spot(world.live, 90, 100, 70) == (90, 71, 100)  # below leaves
    world.live.water.add((101, 101))
    assert base_lighting.torch_spot(world.live, 101, 101, 70) is None


def test_covered_room_is_lit_but_the_hollow_under_the_floor_is_not(world):
    # Roof at y=74 over a room floored at y=70: the covered room cell is a
    # spot. A floor at y=67 over a hollow at y=64..66: the bot must never
    # descend there (live A1 was shot dead by a skeleton in such a cavity).
    under = world.live.block

    def block(x, y, z):
        if (x, z) == (100, 100) and y in (74, 67):
            return "minecraft:cobblestone"
        if (x, z) == (100, 100) and y < 67:
            return "minecraft:air" if y > 63 else "minecraft:stone"
        if (x, z) == (100, 100) and 67 < y <= 70:
            return "minecraft:air" if y > 67 else under(x, y, z)
        return under(x, y, z)

    world.live.block = block
    assert base_lighting.torch_spots(world.live, 100, 100, 69) == [(100, 75, 100), (100, 68, 100)]
    # Open ground keeps a single spot: no stacking torches in open air.
    assert base_lighting.torch_spots(world.live, 101, 100, 70) == [(101, 71, 100)]


def test_a_torch_on_the_floor_above_does_not_light_the_hollow_below():
    assert base_lighting._lit((0, 71, 0), [(0, 73, 0)])
    assert not base_lighting._lit((0, 71, 0), [(0, 79, 0)])


def test_house_centre_and_home_chests_are_always_checked():
    state = _state({"storage": {"supply": [90, 70, 90], "mine": [80, -50, 80]}})
    assert base_lighting.interior_columns(state) == [(103, 103), (90, 90)]


def test_light_base_places_a_bounded_batch_on_dark_cells_and_schedules_next(world):
    state = _state()
    grid = base_lighting.grid_points(base_lighting.lighting_zone(state))
    cells = list(dict.fromkeys(grid + base_lighting.interior_columns(state)))
    world.live.torches = [(cells[0][0], 71, cells[0][1])]  # one cell already lit

    placed, remaining, detail = base_lighting.light_base(world.live, state, now=1000.0)

    dark = len(cells) - 1
    assert placed == min(dark, base_lighting.BATCH)
    assert remaining == dark - placed
    assert all(spot[1] == 71 for spot in world.live.placed)
    assert (cells[0][0], 71, cells[0][1]) not in world.live.placed
    record = state.custom_data[base_lighting.LIGHTING_KEY]
    assert record["placed_total"] == placed
    assert record["next_check"] == 1000.0 + (
        base_lighting.RECHECK_PENDING if remaining else base_lighting.RECHECK_DONE
    )
    assert "placed" in detail


def test_fully_lit_base_places_nothing_and_rechecks_later(world):
    state = _state()
    grid = base_lighting.grid_points(base_lighting.lighting_zone(state))
    cells = grid + base_lighting.interior_columns(state)
    world.live.torches = [(x, 71, z) for x, z in cells]

    placed, remaining, _detail = base_lighting.light_base(world.live, state, now=1000.0)

    assert (placed, remaining) == (0, 0)
    assert state.custom_data[base_lighting.LIGHTING_KEY]["next_check"] == 1000.0 + base_lighting.RECHECK_DONE
    assert not base_lighting.lighting_due(state, now=1500.0)
    assert base_lighting.lighting_due(state, now=1000.0 + base_lighting.RECHECK_DONE)


def test_lighting_gate_tolerates_a_few_hostiles_but_not_low_health():
    ok = SimpleNamespace(health=16.0, nearby_hostiles=2)
    crowded = SimpleNamespace(health=20.0, nearby_hostiles=3)
    hurt = SimpleNamespace(health=10.0, nearby_hostiles=0)
    assert base_lighting.lighting_allowed(ok)
    assert not base_lighting.lighting_allowed(crowded)
    assert not base_lighting.lighting_allowed(hurt)


def test_ensure_torches_makes_charcoal_sticks_and_torches_from_logs(monkeypatch):
    items = {"minecraft:oak_log": 5}
    calls = []
    monkeypatch.setattr(
        "baritone_client.common.inventory.count_item", lambda _c, item: items.get(item, 0)
    )

    def craft(_client, item, qty):
        calls.append(("craft", item))
        if item == "minecraft:oak_planks" and items.get("minecraft:oak_log", 0) > 0:
            items["minecraft:oak_log"] -= 1
            items["minecraft:oak_planks"] = items.get("minecraft:oak_planks", 0) + 4
        elif item == "minecraft:stick" and items.get("minecraft:oak_planks", 0) >= 2:
            items["minecraft:oak_planks"] -= 2
            items["minecraft:stick"] = items.get("minecraft:stick", 0) + 4
        elif item == "minecraft:torch" and items.get("minecraft:charcoal", 0) and items.get("minecraft:stick", 0):
            items["minecraft:charcoal"] -= 1
            items["minecraft:stick"] -= 1
            items["minecraft:torch"] = items.get("minecraft:torch", 0) + 4
        return True

    monkeypatch.setattr("baritone_client.common.inventory.craft", craft)
    monkeypatch.setattr(
        "baritone_client.common.resources.gather_wood",
        lambda *_a, **_k: pytest.fail("enough logs are carried"),
    )
    monkeypatch.setattr(
        "baritone_client.common.livestock_food._home_furnace", lambda *_a: (1, 2, 3)
    )

    def smelt(_client, furnace, raw, fuel, output, count, **_k):
        calls.append(("smelt", raw, fuel, output, count))
        items[raw] -= count
        items[output] = items.get(output, 0) + count
        return True

    monkeypatch.setattr("baritone_client.common.harness_ops.smelt_in_furnace", smelt)

    made = base_lighting.ensure_torches(SimpleNamespace(), SimpleNamespace(), 8, (0, 0, 0))

    assert made >= 8
    assert ("smelt", "minecraft:oak_log", "minecraft:oak_planks", "minecraft:charcoal", 2) in calls
