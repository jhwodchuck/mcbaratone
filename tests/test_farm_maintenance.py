"""Post-harvest repairs and bounded selection of unusable sites."""

from types import SimpleNamespace

import pytest

from baritone_client.common import farming, home_surface
from baritone_client.automator.food_recovery_state import checkpointed_wheat_farm_origin
from baritone_client.automator.common import crop_opportunity


def test_replant_repairs_only_bare_observed_soil(monkeypatch):
    blocks = {(1, 64, 0): "minecraft:grass_block",
              (-1, 64, 0): "minecraft:farmland",
              (-1, 65, 0): "minecraft:wheat",
              (0, 64, 1): "minecraft:farmland",
              (0, 65, 1): "minecraft:water"}
    monkeypatch.setattr(farming, "_block_id", lambda _c, x, y, z: blocks.get((x,y,z), "minecraft:air"))
    planted = []
    monkeypatch.setattr(farming, "plant_farm_tiles", lambda _c, tiles: planted.extend(tiles) or len(tiles))
    assert farming.replant_empty_wheat_tiles(None, 0, 64, 0) == 1
    assert planted == [(1, 64, 0)]


@pytest.mark.parametrize("delta", [0, 1])
def test_harvest_stops_before_explicit_replant_and_requires_delta(monkeypatch, delta):
    calls = []
    clock = [0]
    def dispatch(route, payload):
        calls.append(route)
        return {}
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    monkeypatch.setattr(farming, "goto", lambda *_a, **_k: True)
    monkeypatch.setattr(farming.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(farming.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0]+seconds))
    monkeypatch.setattr(farming, "count_item", lambda *_a: 8 + (delta if clock[0] else 0))
    monkeypatch.setattr(farming, "replant_empty_wheat_tiles", lambda *_a: calls.append("replant"))
    assert farming.harvest_wheat_farm(client, 0, 64, 0) is bool(delta)
    assert calls == ["farm", "cancel", "replant"]


def test_failed_cancel_does_not_start_replant(monkeypatch):
    clock = [0]
    def dispatch(route, payload):
        if route == "cancel":
            raise RuntimeError("unknown mutation outcome")
        return {}
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    monkeypatch.setattr(farming, "goto", lambda *_a, **_k: True)
    monkeypatch.setattr(farming.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(farming.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0]+seconds))
    monkeypatch.setattr(farming, "count_item", lambda *_a: clock[0])
    monkeypatch.setattr(farming, "replant_empty_wheat_tiles", lambda *_a: pytest.fail("must not overlap processes"))
    with pytest.raises(RuntimeError):
        farming.harvest_wheat_farm(client, 0, 64, 0)


def test_site_cooldown_selects_another_known_plot_then_expires(monkeypatch):
    now = [100]
    monkeypatch.setattr("baritone_client.automator.food_recovery_state.time.time", lambda: now[0])
    state = SimpleNamespace(custom_data={
        "farm_location": [0,64,0],
        "food_worker": {"farm_plots": [{"origin": [0,64,0]}, {"origin": [32,64,0]}]},
        "crop_site_cooldowns": {"0,64,0": 200},
    })
    assert checkpointed_wheat_farm_origin(state) == (32,64,0)
    now[0] = 201
    assert checkpointed_wheat_farm_origin(state) == (0,64,0)


def test_failed_crop_attempt_records_site_specific_cooldown(monkeypatch):
    state = SimpleNamespace(custom_data={})
    monkeypatch.setattr(crop_opportunity, "_run_crop_opportunity", lambda *_a, **_k: (False,"blocked",0,0))
    monkeypatch.setattr(crop_opportunity.time, "time", lambda: 100)
    crop_opportunity.run_crop_opportunity(None, SimpleNamespace(location=(0,64,0)), 1, state=state)
    assert state.custom_data["crop_site_cooldowns"] == {"0,64,0": 1000}


def test_surface_farm_outside_home_radius_is_still_no_dig(monkeypatch):
    settings = []
    client = SimpleNamespace(_protected_home_anchor=(0,65,0), transport=SimpleNamespace(dispatch=lambda *_a: {"is_pathing": False}))
    monkeypatch.setattr(home_surface, "_read_break_setting", lambda _c: "true")
    monkeypatch.setattr(home_surface, "_write_break_setting", lambda _c, value: settings.append(value))
    @home_surface.protect_home_route(surface_work=True)
    def farm(client, x, y, z):
        assert settings == ["false"]
        guard = home_surface.home_route_floor(client, x, y + 1, z)
        assert home_surface.below_home_surface(guard, {"x": x, "y": 60, "z": z})
        return True
    assert farm(client, 32,64,0)
    assert settings == ["false", "true"]
    assert client._protected_surface_work is None
