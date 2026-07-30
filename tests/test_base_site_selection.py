from types import SimpleNamespace

import pytest

from baritone_client.automator.phases import base_construction
from baritone_client.automator.phases.base_construction import BaseConstructionHandler
from baritone_client.common import base, surface_recovery
from baritone_client.common.base import _find_flat_site_in_view
from baritone_client.common.tasks import (
    ProgressRecoveryRequired,
    SurvivalRecoveryRequired,
)


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


def test_find_flat_ground_refuses_false_surface_arrival(monkeypatch):
    class Transport:
        def dispatch(self, route, payload):
            if route == "get_state":
                return {"block_position": {"x": -28, "y": 46, "z": 60}}
            if route == "get_block":
                return {
                    "id": (
                        "minecraft:stone"
                        if payload["y"] == 65
                        else "minecraft:air"
                    )
                }
            if route == "get_view":
                raise AssertionError("must not select a site while still underground")
            raise AssertionError(route)

    monkeypatch.setattr(
        "baritone_client.common.automation_utils.safe_goto",
        lambda *_args, **_kwargs: True,
    )

    assert (
        base.find_flat_ground(
            SimpleNamespace(transport=Transport()), radius=24, footprint=7
        )
        is None
    )


def test_find_flat_ground_uses_bounded_surface_recovery(monkeypatch):
    class Transport:
        def __init__(self):
            self.state_reads = 0

        def dispatch(self, route, payload):
            if route == "get_state":
                self.state_reads += 1
                y = 62 if self.state_reads == 1 else 70
                return {"block_position": {"x": 12, "y": y, "z": 8}}
            if route == "get_block":
                return {"id": "minecraft:stone" if payload["y"] == 69 else "minecraft:air"}
            if route == "get_view":
                return {"voxels": _flat_patch(8, 4)}
            if route == "chat":
                return {}
            raise AssertionError(route)

    monkeypatch.setattr(
        "baritone_client.common.automation_utils.safe_goto",
        lambda *_args, **_kwargs: False,
    )
    recovered = []
    monkeypatch.setattr(
        "baritone_client.common.surface_recovery.reach_dry_surface",
        lambda *_args, **_kwargs: recovered.append(True) or (12, 70, 8),
    )

    assert base.find_flat_ground(
        SimpleNamespace(transport=Transport()), radius=24, footprint=7
    ) == (9, 64, 5)
    assert recovered == [True]


def test_surface_recovery_aborts_if_surface_command_moves_downward(monkeypatch):
    class Transport:
        def __init__(self):
            self.state_reads = 0
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                self.state_reads += 1
                y = 62 if self.state_reads == 1 else 59
                return {"block_position": {"x": 4, "y": y, "z": 4}}
            return {}

    transport = Transport()
    monkeypatch.setattr(
        surface_recovery.time,
        "monotonic",
        iter([0.0, 0.0, 1.0]).__next__,
    )
    monkeypatch.setattr(surface_recovery.time, "sleep", lambda _seconds: None)

    assert surface_recovery.reach_dry_surface(
        SimpleNamespace(transport=transport),
        origin=(4, 62, 4),
        expected_y=70,
        goto=lambda *_args, **_kwargs: False,
        command_timeout=10.0,
    ) is None
    assert ("cancel", {}) in transport.calls


def test_surface_recovery_uses_loaded_dry_terrain_before_surface_command():
    class Transport:
        def dispatch(self, route, payload):
            if route == "find_blocks":
                return {"found": [{"x": 20, "y": 69, "z": 4}]}
            if route == "get_state":
                return {"block_position": {"x": 20, "y": 70, "z": 4}}
            if route == "get_block":
                return {"id": "minecraft:air"}
            return {}

    destinations = []
    reached = surface_recovery.reach_dry_surface(
        SimpleNamespace(transport=Transport()),
        origin=(4, 62, 4),
        expected_y=70,
        goto=lambda _client, *position, **_kwargs: destinations.append(position)
        or True,
    )

    assert reached == (20, 70, 4)
    assert destinations == [(20, 70, 4)]


def test_surface_recovery_excavates_when_all_surface_routes_fail(monkeypatch):
    from baritone_client.common import build_site_recovery

    class Transport:
        def __init__(self):
            self.state_reads = 0
            self.calls = []

        def dispatch(self, route, payload):
            self.calls.append((route, payload))
            if route == "get_state":
                self.state_reads += 1
                y = 62 if self.state_reads == 1 else 68
                return {"block_position": {"x": 424, "y": y, "z": 1}}
            return {}

    transport = Transport()
    monkeypatch.setattr(build_site_recovery.time, "sleep", lambda _seconds: None)
    clock = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(build_site_recovery.time, "monotonic", clock.__next__)

    assert build_site_recovery.excavate_surface_egress(
        SimpleNamespace(transport=transport),
        origin=(424, 62, 1),
        expected_y=70,
        timeout_per_attempt=10.0,
    ) == (424, 68, 1)
    assert any(route == "tunnel" for route, _payload in transport.calls)


def test_build_site_relocation_loads_a_different_dry_view(monkeypatch):
    from baritone_client.common import build_site_recovery

    class Transport:
        def __init__(self):
            self.state_reads = 0

        def dispatch(self, route, payload):
            if route == "get_state":
                self.state_reads += 1
                x = 0 if self.state_reads == 1 else 24
                return {"block_position": {"x": x, "y": 64, "z": 0}}
            if route == "find_blocks":
                return {"found": [{"x": 24, "y": 63, "z": 0}]}
            return {}

    destinations = []
    monkeypatch.setattr(
        build_site_recovery, "destination_safe", lambda *_args: True
    )

    assert build_site_recovery.relocate_build_site_search(
        SimpleNamespace(transport=Transport()),
        attempt=1,
        goto=lambda _client, *position, **_kwargs: destinations.append(position)
        or True,
    )
    assert destinations == [(24, 64, 0)]


def test_build_site_relocation_rejects_same_level_cave_floor(monkeypatch):
    from baritone_client.common import build_site_recovery

    class Transport:
        def __init__(self):
            self.state_reads = 0

        def dispatch(self, route, payload):
            if route == "get_state":
                self.state_reads += 1
                y = 62 if self.state_reads == 1 else 69
                x = 0 if self.state_reads == 1 else 28
                return {"block_position": {"x": x, "y": y, "z": 0}}
            if route == "find_blocks":
                return {
                    "found": [
                        {"x": 20, "y": 62, "z": 0},
                        {"x": 28, "y": 68, "z": 0},
                    ]
                }
            return {}

    destinations = []
    monkeypatch.setattr(
        build_site_recovery, "destination_safe", lambda *_args: True
    )

    assert build_site_recovery.relocate_build_site_search(
        SimpleNamespace(transport=Transport()),
        attempt=1,
        minimum_y=66,
        goto=lambda _client, *position, **_kwargs: destinations.append(position)
        or True,
    )
    assert destinations == [(28, 69, 0)]


def test_base_site_failure_yields_without_phase_retry_burn(monkeypatch):
    class Resources:
        get_summary = lambda *_args, **_kwargs: {"inventory": {}}

    class State:
        def __init__(self):
            self.custom_data = {}
            self.saved = 0

        def save_checkpoint(self, *_args):
            self.saved += 1

    monkeypatch.setattr(
        base_construction, "find_flat_ground", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        base_construction, "get_player_pos", lambda *_args: (424, 62, 1)
    )
    monkeypatch.setattr(
        base_construction, "surface_y_at", lambda *_args: 70
    )
    monkeypatch.setattr(
        "baritone_client.common.build_site_recovery.relocate_build_site_search",
        lambda *_args, **_kwargs: False,
    )
    state = State()

    with pytest.raises(ProgressRecoveryRequired):
        BaseConstructionHandler._resolve_build_location(
            SimpleNamespace(),
            Resources(),
            state,
        )

    assert state.saved == 1
    assert state.custom_data["base_site_search_attempts"] == 1


def test_unprovisioned_remote_build_site_is_unsafe_to_return_to():
    from baritone_client.common.build_site_recovery import (
        unprovisioned_remote_build_site,
    )

    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, _payload: {
                "block_position": {"x": 63, "y": 61, "z": 507},
                "food_level": 20,
            }
        )
    )

    assert unprovisioned_remote_build_site(
        client,
        site=(-214, 68, 135),
        inventory_summary={"minecraft:iron_ingot": 67},
    )
    assert not unprovisioned_remote_build_site(
        client,
        site=(-214, 68, 135),
        inventory_summary={"minecraft:bread": 4},
    )


def test_base_phase_relocates_when_local_view_has_no_build_site(monkeypatch):
    class Resources:
        phase_ready_result = lambda *_args, **_kwargs: None
        check_phase_requirements = lambda *_args, **_kwargs: {}
        get_summary = lambda *_args, **_kwargs: {"inventory": {}}
        refresh_inventory = lambda *_args, **_kwargs: None

    class State:
        def __init__(self):
            self.custom_data = {}
            self.saved = 0

        def update_position(self, *_args):
            return None

        def save_checkpoint(self, *_args):
            self.saved += 1

    sites = iter((None, (100, 64, 100)))
    relocated = []

    def find_site(*_args, **kwargs):
        return next(sites) if kwargs.get("footprint") == 7 else None

    monkeypatch.setattr(base_construction, "recover_health", lambda *_a, **_k: True)
    monkeypatch.setattr(
        base_construction, "wait_for_safe_daylight", lambda *_a, **_k: True
    )
    monkeypatch.setattr(base_construction, "find_flat_ground", find_site)
    monkeypatch.setattr(
        "baritone_client.common.build_site_recovery.relocate_build_site_search",
        lambda *_a, **_k: relocated.append(True) or True,
    )
    monkeypatch.setattr(base_construction, "build_good_house", lambda *_a: True)
    monkeypatch.setattr(base_construction, "setup_base", lambda *_a: (True, None))
    monkeypatch.setattr(
        base_construction, "establish_wheat_farm", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        base_construction, "get_player_pos", lambda *_a: (100, 64, 100)
    )

    state = State()
    result = BaseConstructionHandler().execute(
        SimpleNamespace(), Resources(), state
    )

    assert result.success
    assert relocated == [True]
    assert state.custom_data["base_location"] == (100, 64, 100)


def test_base_phase_retires_unprovisioned_remote_origin(monkeypatch):
    class Resources:
        get_summary = lambda *_args, **_kwargs: {
            "inventory": {"minecraft:iron_ingot": 67}
        }

    class State:
        def __init__(self):
            self.custom_data = {
                "base_build_origin": [-214, 68, 135],
                "base_construction_repair_attempts": 2,
            }
            self.saved = 0

        def update_position(self, *_args):
            return None

        def save_checkpoint(self, *_args):
            self.saved += 1

    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, _payload: {
                "block_position": {"x": 63, "y": 61, "z": 507},
                "food_level": 20,
            }
        )
    )
    monkeypatch.setattr(
        base_construction,
        "find_flat_ground",
        lambda *_args, **_kwargs: (64, 63, 508),
    )
    monkeypatch.setattr(
        base_construction, "get_player_pos", lambda *_args: (63, 61, 507)
    )

    state = State()
    location = BaseConstructionHandler._resolve_build_location(
        client,
        Resources(),
        state,
    )

    assert location == (64, 63, 508)
    assert state.custom_data["base_build_origin"] == [64, 63, 508]
    assert "base_construction_repair_attempts" not in state.custom_data
    assert state.saved == 2


def test_build_survival_margin_rejects_low_food():
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda *_args, **_kwargs: {
                "health": 20.0,
                "food_level": 9,
                "is_dead": False,
            }
        )
    )

    assert not base._has_build_survival_margin(client)


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


def test_remote_recovery_position_stages_at_saved_house_before_building(monkeypatch):
    class Transport:
        def __init__(self):
            self.calls = []

        def dispatch(self, route, _payload):
            self.calls.append((route, _payload))
            if route == "get_state":
                return {"block_position": {"x": -103, "y": 61, "z": 275}}
            if route == "chat":
                return {}
            raise AssertionError(route)

    resources = SimpleNamespace(
        phase_ready_result=lambda *_args, **_kwargs: None,
        check_phase_requirements=lambda *_args, **_kwargs: {},
        get_summary=lambda: {"inventory": {}},
    )

    class State:
        def __init__(self):
            self.custom_data = {"base_build_origin": [6, 114, -12]}
            self.saved = 0

        def save_checkpoint(self, *_args):
            self.saved += 1

    destinations = []
    monkeypatch.setattr(base_construction, "recover_health", lambda *_a, **_k: True)
    monkeypatch.setattr(base_construction, "wait_for_safe_daylight", lambda *_a, **_k: True)
    monkeypatch.setattr(
        base_construction, "_wait_for_path_completion", lambda *_a, **_k: True
    )
    monkeypatch.setattr(
        base_construction,
        "goto",
        lambda _client, x, y, z, **_kwargs: destinations.append((x, y, z))
        or False,
    )
    monkeypatch.setattr(
        base_construction,
        "build_good_house",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("remote player must stage before block placement")
        ),
    )

    state = State()
    result = BaseConstructionHandler().execute(
        SimpleNamespace(transport=Transport()), resources, state
    )

    assert not result.success
    assert destinations == [(9, 115, -14)]
    assert "base_construction_repair_attempts" not in state.custom_data
    assert state.custom_data["base_site_return_failures"] == 1
    assert state.saved == 1


def test_repeatedly_repaired_unreachable_house_is_relocated(monkeypatch):
    """An old partial shell must not trap the phase in an endless return loop."""

    class Resources:
        def phase_ready_result(self, *_args, **_kwargs):
            return None

        def check_phase_requirements(self, *_args, **_kwargs):
            return {}

        def get_summary(self):
            return {"inventory": {}}

    class State:
        def __init__(self):
            self.custom_data = {
                "base_build_origin": [235, 64, 113],
                "base_construction_repair_attempts": 5,
            }
            self.saved = 0

        def save_checkpoint(self, *_args):
            self.saved += 1

    monkeypatch.setattr(base_construction, "recover_health", lambda *_a, **_k: True)
    monkeypatch.setattr(
        base_construction, "wait_for_safe_daylight", lambda *_a, **_k: True
    )
    monkeypatch.setattr(
        BaseConstructionHandler,
        "_stage_at_build_site",
        classmethod(lambda *_a, **_k: False),
    )

    state = State()
    result = BaseConstructionHandler().execute(
        SimpleNamespace(), Resources(), state
    )

    assert not result.success
    assert "base_build_origin" not in state.custom_data
    assert "base_construction_repair_attempts" not in state.custom_data
    assert state.saved == 1


def test_second_failed_return_relocates_even_without_build_repairs(monkeypatch):
    class Resources:
        phase_ready_result = lambda *_args, **_kwargs: None
        check_phase_requirements = lambda *_args, **_kwargs: {}
        get_summary = lambda *_args, **_kwargs: {"inventory": {}}

    class State:
        custom_data = {
            "base_build_origin": [-12, 65, 56],
            "base_site_return_failures": 1,
        }

        def save_checkpoint(self, *_args):
            return None

    monkeypatch.setattr(base_construction, "recover_health", lambda *_a, **_k: True)
    monkeypatch.setattr(
        base_construction, "wait_for_safe_daylight", lambda *_a, **_k: True
    )
    monkeypatch.setattr(
        BaseConstructionHandler,
        "_stage_at_build_site",
        classmethod(lambda *_a, **_k: False),
    )

    state = State()
    result = BaseConstructionHandler().execute(
        SimpleNamespace(), Resources(), state
    )

    assert not result.success
    assert "base_build_origin" not in state.custom_data
    assert "base_site_return_failures" not in state.custom_data


def test_stage_at_build_site_accepts_loose_band_when_goto_stops_short(monkeypatch):
    """Baritone can stop short of goto's strict 4m goal yet leave the builder
    close enough to place the shell. _stage_at_build_site must trust the loose
    staging band, not goto's tolerance. Confirmed live: Bot08 looped failing a
    6.4m return it had effectively already completed."""
    # House origin (6, 114, -12); staging target (9, 115, -14). The player ends
    # up ~2m from the staging point after goto reports failure -> within band.
    positions = iter([
        {"block_position": {"x": 50, "y": 114, "z": -12}},   # entry: ~41m, not staged
        {"block_position": {"x": 7, "y": 114, "z": -13}},     # after goto: within band
    ])

    class Transport:
        def dispatch(self, route, _payload):
            if route == "get_state":
                return next(positions)
            return {}

    client = SimpleNamespace(transport=Transport())
    monkeypatch.setattr(base_construction, "goto", lambda *_a, **_k: False)

    assert BaseConstructionHandler._stage_at_build_site(client, 6, 114, -12) is True


def test_stage_at_build_site_fails_when_goto_leaves_player_out_of_band(monkeypatch):
    """If goto fails and the player is still far from the house, staging must
    still fail so the phase does not attempt block placement from afar."""
    positions = iter([
        {"block_position": {"x": 50, "y": 114, "z": -12}},   # entry: not staged
        {"block_position": {"x": 48, "y": 114, "z": -12}},    # after goto: still far
    ])

    class Transport:
        def dispatch(self, route, _payload):
            if route == "get_state":
                return next(positions)
            return {}

    client = SimpleNamespace(transport=Transport())
    monkeypatch.setattr(base_construction, "goto", lambda *_a, **_k: False)

    assert BaseConstructionHandler._stage_at_build_site(client, 6, 114, -12) is False


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


def test_recovered_house_door_repairs_missing_support(monkeypatch):
    class DoorTransport:
        def __init__(self):
            self.support = "minecraft:air"
            self.door = "minecraft:air"

        def dispatch(self, route, payload):
            if route == "get_block":
                position = (payload["x"], payload["y"], payload["z"])
                if position == (10, 69, 20):
                    return {"id": self.support}
                if position == (10, 70, 20):
                    return {"id": self.door, "state": {"facing": "north"}}
                return {"id": "minecraft:air"}
            if route == "place_block":
                if self.support == "minecraft:air":
                    raise RuntimeError("door target has no support")
                self.door = payload["block"]
            return {}

    transport = DoorTransport()
    client = SimpleNamespace(transport=transport)
    support_repairs = []

    monkeypatch.setattr(
        "baritone_client.common.door_recovery.move_to_door_staging",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(base, "select_item", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        base,
        "count_item",
        lambda _client, item_id: int(item_id == "minecraft:cobblestone"),
    )
    monkeypatch.setattr(
        base,
        "robust_place",
        lambda _client, x, y, z, item_id: (
            support_repairs.append((x, y, z, item_id)),
            setattr(transport, "support", item_id),
            True,
        )[-1],
    )
    monkeypatch.setattr(base.time, "sleep", lambda _seconds: None)

    assert base._place_north_wall_door(
        client,
        10,
        70,
        20,
        "minecraft:oak_door",
    )
    assert support_repairs == [(10, 69, 20, "minecraft:cobblestone")]


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


def test_low_hunger_house_failure_yields_without_charging_repair_attempt(monkeypatch):
    class Transport:
        def dispatch(self, route, _payload):
            if route == "chat":
                return {}
            assert route == "get_state"
            return {"health": 20, "food_level": 6}

    resources = SimpleNamespace(
        phase_ready_result=lambda *_args, **_kwargs: None,
        check_phase_requirements=lambda *_args, **_kwargs: {},
        get_summary=lambda: {"inventory": {}},
    )

    class State:
        def __init__(self):
            self.custom_data = {"base_build_origin": [11, 70, 20]}

        def save_checkpoint(self, *_args):
            return None

    monkeypatch.setattr(base_construction, "recover_health", lambda *_a, **_k: True)
    monkeypatch.setattr(base_construction, "wait_for_safe_daylight", lambda *_a, **_k: True)
    monkeypatch.setattr(base_construction, "build_good_house", lambda *_a, **_k: False)
    monkeypatch.setattr(base_construction, "eat_until_hunger", lambda *_a, **_k: False)
    monkeypatch.setattr(base_construction, "acquire_emergency_food", lambda *_a, **_k: False)

    state = State()
    with pytest.raises(SurvivalRecoveryRequired):
        BaseConstructionHandler().execute(
            SimpleNamespace(transport=Transport()), resources, state
        )

    assert "base_construction_repair_attempts" not in state.custom_data
    assert state.custom_data["base_survival_recovery_failures"] == 1


def test_low_health_after_house_failure_yields_before_repair_attempt(monkeypatch):
    transport = SimpleNamespace(
        dispatch=lambda route, _payload: {"health": 9, "food_level": 20}
    )
    monkeypatch.setattr(base_construction, "recover_health", lambda *_a, **_k: False)
    monkeypatch.setattr(
        base_construction, "acquire_emergency_food", lambda *_a, **_k: False
    )
    monkeypatch.setattr(
        base_construction,
        "eat_until_hunger",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("full hunger must not be treated as the health recovery")
        ),
    )

    with pytest.raises(SurvivalRecoveryRequired):
        BaseConstructionHandler()._recover_build_survival_or_yield(
            SimpleNamespace(transport=transport)
        )


def test_repeated_survival_abort_relocates_unsafe_build_origin(monkeypatch):
    class State:
        def __init__(self):
            self.custom_data = {
                "base_build_origin": [283, 64, 43],
                "base_survival_recovery_failures": 2,
            }
            self.saved = 0

        def save_checkpoint(self, *_args):
            self.saved += 1

    transport = SimpleNamespace(
        dispatch=lambda route, _payload: {"health": 20, "food_level": 11}
    )
    monkeypatch.setattr(base_construction, "eat_until_hunger", lambda *_a, **_k: False)
    monkeypatch.setattr(
        base_construction, "acquire_emergency_food", lambda *_a, **_k: False
    )
    relocation = {}

    def relocate(*_args, **kwargs):
        relocation.update(kwargs)
        return True

    monkeypatch.setattr(
        "baritone_client.common.build_site_recovery.relocate_build_site_search",
        relocate,
    )

    state = State()
    with pytest.raises(SurvivalRecoveryRequired):
        BaseConstructionHandler()._recover_build_survival_or_yield(
            SimpleNamespace(transport=transport),
            state=state,
            inventory_summary={},
        )

    assert "base_build_origin" not in state.custom_data
    assert "base_survival_recovery_failures" not in state.custom_data
    assert relocation["attempt_limit"] == 2
    assert state.saved == 1


def test_surface_ascent_falls_back_to_a_nearby_column(monkeypatch):
    """Insisting on the player's own column stranded Bot09: it stood in
    shallow water at y=62 under a vine canopy, surface_y_at returned the top
    of the vines (y=68), and Baritone cannot climb a vine column -- so the only
    offered goal was unreachable and BASE_CONSTRUCTION failed on repeat. A
    couple of blocks sideways is usually ordinary walkable ground."""
    from baritone_client.common import site_selection

    attempts = []

    class Transport:
        def dispatch(self, route, payload):
            if route == "get_state":
                return {"block_position": {"x": 371, "y": 62, "z": 46}}
            if route == "get_block":
                x, y, z = payload["x"], payload["y"], payload["z"]
                if (x, z) == (371, 46):           # the vine shaft
                    if 63 <= y <= 67:
                        return {"id": "minecraft:vine"}
                    if y == 62:
                        return {"id": "minecraft:water"}
                    return {"id": "minecraft:air"}
                return {"id": "minecraft:grass_block" if y == 67 else "minecraft:air"}
            if route == "get_view":
                return {"voxels": []}
            return {}

    def fake_goto(_client, x, y, z, **_kw):
        attempts.append((x, y, z))
        # Only the sideways column is reachable.
        return (x, z) != (371, 46)

    monkeypatch.setattr(
        "baritone_client.common.automation_utils.safe_goto", fake_goto
    )
    monkeypatch.setattr(
        site_selection, "_restore_surface_navigation_policy", lambda _c: None
    )

    site_selection.find_flat_ground(
        SimpleNamespace(transport=Transport()), radius=8, footprint=1
    )

    assert attempts, "should have attempted an ascent"
    assert attempts[0] == (371, 68, 46), "own column first"
    assert any(a[0] != 371 or a[2] != 46 for a in attempts), (
        "must fall back to a nearby column instead of giving up"
    )
