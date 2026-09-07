"""Terrain and relocation regressions: movement is not settlement evidence."""

from types import SimpleNamespace

import pytest

from baritone_client.common.home_site import suitable_home_site, supported_home_ground
from baritone_client.common.build_site_recovery import relocate_build_site_search
from baritone_client.actions.homestead_site import screen_rehome, relocate_homestead
from baritone_client.actions.homestead import IncrementalHomestead
from baritone_client.common.tasks import ProgressRecoveryRequired


class Terrain:
    def __init__(self, *, missing=None, floor="minecraft:grass_block", resource_y=63):
        self.missing = missing
        self.floor = floor
        self.resource_y = resource_y
        self.calls = []
        self.transport = self

    def dispatch(self, route, payload):
        self.calls.append((route, payload))
        if route == "get_block":
            return {"id": self.floor if payload["y"] == 63 else "minecraft:air"}
        if route == "find_blocks":
            assert payload["center"] == {"x": 0, "y": 64, "z": 0}
            if self.missing in payload["blocks"]:
                return {"found": []}
            return {"found": [{"x": 2, "y": self.resource_y, "z": 0}]}
        raise AssertionError(route)


@pytest.mark.parametrize("floor", ["", "minecraft:air", "minecraft:void_air",
    "minecraft:cave_air", "minecraft:powder_snow", "minecraft:water",
    "minecraft:lava", "minecraft:magma_block", "minecraft:oak_leaves"])
def test_unsupported_or_hazardous_ground_is_never_home(floor):
    assert not supported_home_ground(floor)
    assert not suitable_home_site(Terrain(floor=floor), (0, 64, 0))


@pytest.mark.parametrize("missing", ["minecraft:dirt", "minecraft:water", "minecraft:oak_log"])
def test_dry_pad_without_local_resources_is_not_a_home(missing):
    assert not suitable_home_site(Terrain(missing=missing), (0, 64, 0))


def test_local_supported_pad_with_resources_passes_read_only_screen():
    terrain = Terrain()
    assert suitable_home_site(terrain, (0, 64, 0))
    assert {route for route, _ in terrain.calls} == {"get_block", "find_blocks"}


def test_resources_far_below_mountain_do_not_qualify_site():
    assert not suitable_home_site(Terrain(resource_y=20), (0, 64, 0))


def test_failed_survey_is_not_permission_to_settle():
    client = SimpleNamespace(transport=SimpleNamespace(
        dispatch=lambda *_: (_ for _ in ()).throw(TimeoutError())))
    assert not suitable_home_site(client, (0, 64, 0))


def test_unloaded_headroom_does_not_qualify_even_with_supported_floor():
    def dispatch(route, payload):
        return {"id": "minecraft:grass_block" if payload["y"] == 63 else "minecraft:void_air"}
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    assert not suitable_home_site(client, (0, 64, 0))


def test_relocation_prefers_nearby_level_ground_and_verifies_arrival(monkeypatch):
    current = {"x": 0, "y": 64, "z": 0}
    routes = []
    surveys = []

    def dispatch(route, payload):
        if route == "get_state":
            return {"block_position": dict(current)}
        return {"found": [{"x": 16, "y": 139, "z": 0},
                          {"x": 20, "y": 63, "z": 0}]}

    def travel(client, x, y, z, **kwargs):
        routes.append((x, y, z))
        current.update(x=x, y=y, z=z)
        return True

    def survey(client, position):
        surveys.append(position)
        return True

    monkeypatch.setattr("baritone_client.common.build_site_recovery.destination_safe", lambda *_: True)
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    assert relocate_build_site_search(client, attempt=1, goto=travel, candidate_validator=survey)
    assert routes == [(20, 64, 0)]
    assert surveys == [(20, 64, 0), (20, 64, 0)]


def test_successful_route_without_displacement_does_not_relocate(monkeypatch):
    def dispatch(route, payload):
        return ({"block_position": {"x": 0, "y": 64, "z": 0}} if route == "get_state"
                else {"found": [{"x": 20, "y": 63, "z": 0}]})
    monkeypatch.setattr("baritone_client.common.build_site_recovery.destination_safe", lambda *_: True)
    client = SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch))
    assert not relocate_build_site_search(client, attempt=1, goto=lambda *_a, **_k: True)


def test_unsuitable_heading_move_preserves_old_home_and_evidence(monkeypatch):
    helper = IncrementalHomestead.__new__(IncrementalHomestead)
    helper.client = Terrain(missing="minecraft:water")
    helper.state = SimpleNamespace(
        custom_data={"homestead_anchor": [90, 64, 0]}, record_phase_payload=lambda *_: None,
    )
    helper.current_position = lambda: (0, 64, 0)
    helper._dry_ground = lambda _: True
    home = {"anchor": [90, 64, 0], "steps": {"micro_farm": {"verified": True}}}
    monkeypatch.setattr("baritone_client.common.build_site_recovery.relocate_build_site_search", lambda *_a, **_k: False)
    monkeypatch.setattr("baritone_client.automator.camp_breaker.break_camp", lambda *_: True)
    assert not relocate_homestead(helper.client, home, "micro_farm", helper)
    assert home["anchor"] == [90, 64, 0]
    assert home["steps"]["micro_farm"]["verified"]
    assert helper.state.custom_data["homestead_anchor"] == [90, 64, 0]
    with pytest.raises(ProgressRecoveryRequired, match="retained existing anchor"):
        screen_rehome(helper, home, (0, 64, 0))
    assert home["site_relocations"] == 2
    assert helper.load()["site_relocations"] == 2
