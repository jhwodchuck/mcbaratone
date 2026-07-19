from types import SimpleNamespace

from baritone_client.automator.phases import base_construction
from baritone_client.automator.phases.base_construction import BaseConstructionHandler
from baritone_client.common import base
from baritone_client.common.base import _find_flat_site_in_view


def _flat_patch(origin_x, origin_z, size=9, ground_y=63):
    return [
        {"x": x, "y": ground_y, "z": z, "id": "minecraft:grass_block"}
        for x in range(origin_x, origin_x + size)
        for z in range(origin_z, origin_z + size)
    ]


def test_flat_site_selection_returns_nearest_clear_7x7_origin():
    voxels = _flat_patch(-4, -4)

    assert _find_flat_site_in_view(voxels, (0, 64, 0), radius=4, footprint=7) == (
        -3,
        64,
        -3,
    )


def test_flat_site_selection_rejects_liquid_and_overhead_obstruction():
    voxels = _flat_patch(-3, -3, size=7)
    voxels.append({"x": 0, "y": 64, "z": 0, "id": "minecraft:water"})
    assert _find_flat_site_in_view(voxels, (0, 64, 0), radius=3, footprint=7) is None

    voxels[-1] = {"x": 0, "y": 66, "z": 0, "id": "minecraft:cobblestone"}
    assert _find_flat_site_in_view(voxels, (0, 64, 0), radius=3, footprint=7) is None


def test_flat_site_selection_allows_natural_tree_blocks_to_be_cleared():
    voxels = _flat_patch(-3, -3, size=7)
    voxels.append({"x": 0, "y": 66, "z": 0, "id": "minecraft:oak_log"})
    assert _find_flat_site_in_view(voxels, (0, 64, 0), radius=3, footprint=7) == (
        -3,
        64,
        -3,
    )


def test_base_phase_refuses_exposed_night_work(monkeypatch):
    class Resources:
        def phase_ready_result(self, *_args, **_kwargs):
            return None

        def check_phase_requirements(self, *_args, **_kwargs):
            raise AssertionError("materials must not be gathered at night")

    monkeypatch.setattr(base_construction, "recover_health", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        base_construction, "wait_for_safe_daylight", lambda *_args, **_kwargs: False
    )

    result = BaseConstructionHandler().execute(
        SimpleNamespace(), Resources(), SimpleNamespace()
    )

    assert not result.success
    assert "safe daylight" in result.reason


def test_setup_base_requires_verified_storage(monkeypatch):
    monkeypatch.setattr("baritone_client.common.base.place_crafting_table", lambda *_args: True)
    monkeypatch.setattr("baritone_client.common.base.place_furnace", lambda *_args: True)
    monkeypatch.setattr("baritone_client.common.base.place_chest", lambda *_args: False)
    monkeypatch.setattr("baritone_client.common.base.place_bed", lambda *_args: False)

    from baritone_client.common.base import setup_base

    success, location = setup_base(SimpleNamespace(), (10, 64, 20))

    assert not success
    assert location == (10, 64, 20)


def test_good_house_rechecks_daylight_before_placing_blocks(monkeypatch):
    client = SimpleNamespace()

    def inventory_count(_client, item_id):
        if item_id == "minecraft:cobblestone":
            return 64
        if item_id.endswith("_planks"):
            return 144
        return 0

    monkeypatch.setattr(base, "count_item", inventory_count)
    monkeypatch.setattr(base, "wait_for_safe_daylight", lambda _client: False)
    monkeypatch.setattr(
        base,
        "robust_place",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("house placement must not begin without daylight")
        ),
    )

    assert not base.build_good_house(client, 10, 64, 20)


def test_base_phase_reuses_saved_in_progress_house_origin(monkeypatch):
    class Resources:
        def phase_ready_result(self, *_args, **_kwargs):
            return None

        def check_phase_requirements(self, *_args, **_kwargs):
            return {}

        def get_summary(self):
            return {"inventory": {}}

        def refresh_inventory(self):
            return None

    class State:
        def __init__(self):
            self.custom_data = {"base_build_origin": [-9, 78, -122]}
            self.saved = 0

        def update_position(self, *_args):
            return None

        def save_checkpoint(self, *_args):
            self.saved += 1

    built = []
    monkeypatch.setattr(base_construction, "recover_health", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(base_construction, "wait_for_safe_daylight", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        base_construction,
        "find_flat_ground",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a saved in-progress origin must be reused")
        ),
    )
    monkeypatch.setattr(
        base_construction,
        "build_good_house",
        lambda _client, x, y, z: built.append((x, y, z)) or True,
    )
    monkeypatch.setattr(base_construction, "setup_base", lambda *_args: (True, None))
    monkeypatch.setattr(base_construction, "plant_wheat_farm", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(base_construction, "get_player_pos", lambda *_args: (0, 64, 0))

    state = State()
    result = BaseConstructionHandler().execute(SimpleNamespace(), Resources(), state)

    assert result.success
    assert built == [(-9, 78, -122)]
    assert "base_build_origin" not in state.custom_data
    assert state.custom_data["base_location"] == (-9, 78, -122)


def test_failed_house_is_preserved_without_overlaying_dirt_fallback(monkeypatch):
    class Resources:
        def phase_ready_result(self, *_args, **_kwargs):
            return None

        def check_phase_requirements(self, *_args, **_kwargs):
            return {}

        def get_summary(self):
            return {"inventory": {}}

    class State:
        custom_data = {"base_build_origin": [-9, 78, -122]}

        def save_checkpoint(self, *_args):
            return None

    monkeypatch.setattr(base_construction, "recover_health", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(base_construction, "wait_for_safe_daylight", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(base_construction, "build_good_house", lambda *_args: False)
    monkeypatch.setattr(
        base_construction,
        "build_dirt_shelter",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("a dirt fallback must not overwrite the partial house")
        ),
    )

    result = BaseConstructionHandler().execute(
        SimpleNamespace(), Resources(), State()
    )

    assert not result.success
