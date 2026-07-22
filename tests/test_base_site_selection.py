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

    def find_flat_ground_stub(*_args, **kwargs):
        # A saved in-progress house origin must be reused, not researched --
        # only the unrelated farm-placement search (footprint=5) may call
        # find_flat_ground here.
        if kwargs.get("footprint") != 5:
            raise AssertionError("a saved in-progress origin must be reused")
        return (100, 64, 100)

    monkeypatch.setattr(base_construction, "recover_health", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(base_construction, "wait_for_safe_daylight", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(base_construction, "find_flat_ground", find_flat_ground_stub)
    monkeypatch.setattr(
        base_construction,
        "build_good_house",
        lambda _client, x, y, z: built.append((x, y, z)) or True,
    )
    monkeypatch.setattr(base_construction, "setup_base", lambda *_args: (True, None))
    monkeypatch.setattr(base_construction, "establish_wheat_farm", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(base_construction, "get_player_pos", lambda *_args: (0, 64, 0))

    state = State()
    result = BaseConstructionHandler().execute(SimpleNamespace(), Resources(), state)

    assert result.success
    assert built == [(-9, 78, -122)]
    assert "base_build_origin" not in state.custom_data
    assert state.custom_data["base_location"] == (-9, 78, -122)


def test_base_phase_persists_established_wheat_farm_location(monkeypatch):
    """A successfully established farm must be recorded on custom_data so
    later phases (food/leather fallbacks) can find it without re-searching."""
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

        def update_position(self, *_args):
            return None

        def save_checkpoint(self, *_args):
            return None

    monkeypatch.setattr(base_construction, "recover_health", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(base_construction, "wait_for_safe_daylight", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(base_construction, "find_flat_ground", lambda *_args, **_kwargs: (12, 70, 34))
    monkeypatch.setattr(base_construction, "build_good_house", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(base_construction, "setup_base", lambda *_args, **_kwargs: (True, None))
    monkeypatch.setattr(base_construction, "establish_wheat_farm", lambda *_args, **_kwargs: (12, 70, 34))
    monkeypatch.setattr(base_construction, "get_player_pos", lambda *_args: (0, 64, 0))

    state = State()
    result = BaseConstructionHandler().execute(SimpleNamespace(), Resources(), state)

    assert result.success
    assert state.custom_data["wheat_farm"] == {"origin": [12, 70, 34]}


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


def test_recovered_house_progress_can_complete_base_without_rebuild(monkeypatch):
    resources = SimpleNamespace(
        phase_ready_result=lambda *_args, **_kwargs: None,
        check_phase_requirements=lambda *_args, **_kwargs: {},
        get_summary=lambda: {"inventory": {}},
        refresh_inventory=lambda: None,
        ensure_phase_supplies=lambda *_args, **_kwargs: SimpleNamespace(success=True),
    )

    class State:
        def __init__(self):
            self.custom_data = {
                "base_build_origin": [11, 70, 20],
                "base_construction_repair_attempts": 1,
            }
            self.saved = 0

        def update_position(self, *_args):
            return None

        def save_checkpoint(self, *_args):
            self.saved += 1

    calls = []
    handler = BaseConstructionHandler()
    monkeypatch.setattr(base_construction, "recover_health", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(base_construction, "wait_for_safe_daylight", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(base_construction, "build_good_house", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        base_construction.BaseConstructionHandler,
        "_summarize_starter_house_progress",
        lambda *_args, **_kwargs: {
            "floor": 46,
            "shell": 64,
            "roof": 21,
            "floor_total": 49,
            "shell_total": 70,
            "roof_total": 49,
            "door_present": True,
        },
    )
    monkeypatch.setattr(
        base_construction.BaseConstructionHandler,
        "_ensure_starter_house_entryway",
        lambda *_args, **_kwargs: calls.append("entryway") or True,
    )
    monkeypatch.setattr(base_construction, "setup_base", lambda *_args, **_kwargs: (True, None))
    monkeypatch.setattr(base_construction, "get_player_pos", lambda *_args: (0, 64, 0))

    state = State()
    client = SimpleNamespace()
    result = handler.execute(client, resources, state)

    assert result.success
    assert "entryway" in calls
    assert state.custom_data.get("base_construction_repair_attempts") is None
    assert "base_build_origin" not in state.custom_data


def test_old_house_with_strong_shell_and_roof_skips_endless_floor_repair():
    progress = {
        "floor": 33,
        "shell": 67,
        "roof": 47,
        "floor_total": 49,
        "shell_total": 70,
        "roof_total": 49,
        "door_present": False,
    }

    assert BaseConstructionHandler._should_continue_from_recovered_house(
        progress, attempt=201
    )


def test_unrecoverable_house_returns_retry_required(monkeypatch):
    resources = SimpleNamespace(
        phase_ready_result=lambda *_args, **_kwargs: None,
        check_phase_requirements=lambda *_args, **_kwargs: {},
        get_summary=lambda: {"inventory": {}},
        refresh_inventory=lambda: None,
        ensure_phase_supplies=lambda *_args, **_kwargs: SimpleNamespace(success=True),
    )

    class State:
        def __init__(self):
            self.custom_data = {"base_build_origin": [11, 70, 20]}
            self.saved = 0

        def update_position(self, *_args):
            return None

        def save_checkpoint(self, *_args):
            self.saved += 1

    monkeypatch.setattr(base_construction, "recover_health", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(base_construction, "wait_for_safe_daylight", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(base_construction, "build_good_house", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        base_construction.BaseConstructionHandler,
        "_summarize_starter_house_progress",
        lambda *_args, **_kwargs: {
            "floor": 10,
            "shell": 1,
            "roof": 0,
            "floor_total": 49,
            "shell_total": 70,
            "roof_total": 49,
            "door_present": False,
        },
    )
    monkeypatch.setattr(
        base_construction.BaseConstructionHandler,
        "_ensure_starter_house_entryway",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("entryway should not be attempted without recoverable progress")
        ),
    )
    state = State()
    result = BaseConstructionHandler().execute(SimpleNamespace(), resources, state)

    assert not result.success
    assert result.reason == "Starter house was not completed; retry required"
    assert state.saved == 1
