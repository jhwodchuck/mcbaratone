"""
Tests for common/city.py - the megabase city build-out helpers used by
city_builder_forever.py (and, once wired in, phases/city_building.py).

Uses the shared MockClient/MockTransport fixtures from conftest.py so no
Minecraft/bridge connection is needed.
"""

from unittest.mock import MagicMock, patch

import pytest

from baritone_client.common import city as city_mod
from baritone_client.common.city import (
    BUILD_INSET,
    DISTRICT_CHUNKS,
    DISTRICT_ROLES,
    DISTRICT_SIZE,
    ROAD_WIDTH,
    build_box,
    build_district,
    build_from_schematic,
    build_hollow_building,
    build_ring,
    district_origin,
    district_ring_offsets,
    role_for,
)
from baritone_client.common.terraform import CHUNK_SIZE


@pytest.fixture(autouse=True)
def _no_sleep():
    """City builds sleep between selection commands; skip that in tests."""
    with patch("baritone_client.common.terraform.time.sleep"), \
         patch("baritone_client.common.city.time.sleep"):
        yield


@pytest.fixture(autouse=True)
def _clean_schematic_registry():
    """Keep the module-level schematic registry from leaking between tests."""
    original = dict(city_mod.SCHEMATIC_REGISTRY)
    city_mod.SCHEMATIC_REGISTRY.clear()
    yield
    city_mod.SCHEMATIC_REGISTRY.clear()
    city_mod.SCHEMATIC_REGISTRY.update(original)


@pytest.fixture
def city_client(mock_client):
    """
    The shared MockClient (conftest.py) has no `.process` facade, which the
    schematic path needs. Attach one here rather than editing the shared
    fixture, which other suites depend on.
    """
    mock_client.process = MagicMock()
    return mock_client


def chat_messages(client):
    return [p["message"] for route, p in client.transport.calls if route == "chat"]


# --- Layout math -----------------------------------------------------------

def test_district_size_matches_chunk_math():
    assert DISTRICT_SIZE == DISTRICT_CHUNKS * CHUNK_SIZE


def test_district_origin_centers_the_middle_district_on_the_center_point():
    ox, oz = district_origin(0, 0, 0, 0)
    # The center district should straddle the center point.
    assert ox == -DISTRICT_SIZE // 2
    assert oz == -DISTRICT_SIZE // 2
    assert ox < 0 < ox + DISTRICT_SIZE


def test_district_origins_tile_without_gaps_or_overlap():
    """Adjacent districts should sit exactly DISTRICT_SIZE apart."""
    a = district_origin(0, 0, 0, 0)
    b = district_origin(0, 0, 1, 0)
    c = district_origin(0, 0, 0, 1)
    assert b[0] - a[0] == DISTRICT_SIZE
    assert b[1] == a[1]
    assert c[1] - a[1] == DISTRICT_SIZE
    assert c[0] == a[0]


def test_center_district_is_always_the_plaza():
    assert role_for(0, 0) == "plaza"


def test_role_assignment_is_deterministic_and_in_range():
    for dcx, dcz in [(1, 0), (-3, 2), (5, -7), (12, 12)]:
        role = role_for(dcx, dcz)
        assert role in DISTRICT_ROLES
        assert role_for(dcx, dcz) == role  # stable across calls


def test_district_ring_offsets_match_ring_geometry():
    assert list(district_ring_offsets(0)) == [(0, 0)]
    ring1 = list(district_ring_offsets(1))
    assert len(ring1) == 8
    assert all(max(abs(dx), abs(dz)) == 1 for dx, dz in ring1)


# --- Build primitives ------------------------------------------------------

def test_build_box_issues_selection_and_fill(mock_client):
    assert build_box(mock_client, 0, 64, 0, 15, 70, 15, "minecraft:stone_bricks")
    msgs = chat_messages(mock_client)
    assert "#sel pos1 0 64 0" in msgs
    assert "#sel pos2 15 70 15" in msgs
    assert "#sel set minecraft:stone_bricks" in msgs
    assert "#sel clear" in msgs


def test_build_box_clamps_to_world_height_limits(mock_client):
    build_box(mock_client, 0, -500, 0, 15, 9999, 15, "minecraft:stone")
    msgs = chat_messages(mock_client)
    assert "#sel pos1 0 -64 0" in msgs   # clamped to WORLD_MIN_Y
    assert "#sel pos2 15 320 15" in msgs  # clamped to WORLD_MAX_Y


def test_build_hollow_building_shell_then_hollow_then_floor(mock_client):
    result = build_hollow_building(
        mock_client, x=0, y=64, z=0, width=10, depth=10, height=6,
        wall_block="minecraft:stone_bricks", floor_block="minecraft:smooth_stone",
    )
    assert result.success, result.reason
    msgs = chat_messages(mock_client)

    # Solid shell over the full volume.
    assert "#sel pos1 0 64 0" in msgs
    assert "#sel pos2 9 69 9" in msgs
    assert "#sel set minecraft:stone_bricks" in msgs

    # Interior hollowed with air, inset by 1 on every axis.
    assert "#sel pos1 1 65 1" in msgs
    assert "#sel pos2 8 68 8" in msgs
    assert "#sel set air" in msgs

    # Floor laid at the base layer.
    assert "#sel set minecraft:smooth_stone" in msgs

    # Fill order matters: shell must precede the hollow pass.
    fills = [m for m in msgs if m.startswith("#sel set")]
    assert fills.index("#sel set minecraft:stone_bricks") < fills.index("#sel set air")


def test_build_hollow_building_skips_hollow_pass_when_too_small(mock_client):
    """A 2x2x2 building has no interior to hollow - shouldn't emit an air fill."""
    build_hollow_building(
        mock_client, x=0, y=64, z=0, width=2, depth=2, height=2,
        wall_block="minecraft:stone_bricks", floor_block="minecraft:smooth_stone",
    )
    msgs = chat_messages(mock_client)
    # The only air fill would be the doorway, not an interior hollow pass.
    air_fills = [m for m in msgs if m == "#sel set air"]
    assert len(air_fills) <= 1


# --- Schematic hook --------------------------------------------------------

def test_build_from_schematic_returns_none_when_role_unregistered(mock_client):
    assert build_from_schematic(mock_client, "industrial", 0, 64, 0) is None


def test_build_from_schematic_invokes_builder_when_registered(city_client):
    city_mod.SCHEMATIC_REGISTRY["industrial"] = "super_smelter_v1.schem"
    result = build_from_schematic(city_client, "industrial", 10, 64, 20)
    assert result is True
    city_client.process.builder.start.assert_called_once_with(
        "super_smelter_v1.schem", x=10, y=64, z=20
    )


def test_build_from_schematic_falls_back_when_builder_raises(mock_client):
    """No `.process` facade -> schematic path must degrade to procedural, not crash."""
    city_mod.SCHEMATIC_REGISTRY["industrial"] = "super_smelter_v1.schem"
    assert build_from_schematic(mock_client, "industrial", 0, 64, 0) is None


def test_district_uses_schematic_instead_of_procedural_when_registered(city_client):
    city_mod.SCHEMATIC_REGISTRY["storage"] = "storage_hall.schem"
    procedural = MagicMock()
    with patch.object(city_mod, "role_for", return_value="storage"), \
         patch.dict(city_mod.ROLE_BUILDERS, {"storage": procedural}):
        result = build_district(city_client, 0, 0, 1, 0, target_y=64, flatten=False)

    assert result.success
    procedural.assert_not_called()
    city_client.process.builder.start.assert_called_once()


# --- District & ring assembly ---------------------------------------------

def test_build_district_flattens_then_paves_then_builds(mock_client):
    with patch.object(city_mod, "terraform_chunk") as flatten:
        result = build_district(mock_client, 0, 0, 1, 0, target_y=64, flatten=True)

    assert result.success, result.reason
    # Should terraform every chunk of the district footprint.
    assert flatten.call_count == DISTRICT_CHUNKS * DISTRICT_CHUNKS

    msgs = chat_messages(mock_client)
    assert any(m.startswith("#sel set minecraft:stone_bricks") for m in msgs)
    assert result.data["role"] in DISTRICT_ROLES
    assert result.data["district"] == (1, 0)


def test_build_district_can_skip_flattening(mock_client):
    with patch.object(city_mod, "terraform_chunk") as flatten:
        build_district(mock_client, 0, 0, 1, 0, target_y=64, flatten=False)
    flatten.assert_not_called()


def test_build_district_road_margin_uses_configured_width(mock_client):
    build_district(mock_client, 0, 0, 0, 0, target_y=64, flatten=False, with_roads=True)
    ox, oz = district_origin(0, 0, 0, 0)
    msgs = chat_messages(mock_client)
    # South edge road spans the full district width, ROAD_WIDTH deep.
    assert f"#sel pos1 {ox} 64 {oz}" in msgs
    assert f"#sel pos2 {ox + DISTRICT_SIZE - 1} 64 {oz + ROAD_WIDTH - 1}" in msgs


def test_plaza_is_built_at_the_center_district(mock_client):
    plaza = MagicMock(return_value=city_mod.TaskResult.ok("stub"))
    # ROLE_BUILDERS holds direct function references, so patch the dict entry
    # (this is also the supported way for callers to override a role builder).
    with patch.dict(city_mod.ROLE_BUILDERS, {"plaza": plaza}):
        build_district(mock_client, 0, 0, 0, 0, target_y=64, flatten=False)
    plaza.assert_called_once()


def test_role_builders_are_overridable_via_the_registry(mock_client):
    """Callers can swap in a custom builder for any role."""
    custom = MagicMock(return_value=city_mod.TaskResult.ok("custom"))
    with patch.object(city_mod, "role_for", return_value="farm"), \
         patch.dict(city_mod.ROLE_BUILDERS, {"farm": custom}):
        result = build_district(mock_client, 0, 0, 2, 0, target_y=64, flatten=False)
    custom.assert_called_once()
    assert result.success


def test_build_ring_is_resumable_via_progress_dict(mock_client):
    progress = {"next_index": 5, "total": 8}
    seen = []
    with patch.object(city_mod, "build_district") as bd:
        bd.return_value = city_mod.TaskResult.ok("stub", role="storage", origin=(0, 0), district=(0, 0))
        result = build_ring(
            mock_client, 0, 0, target_y=64, ring=1,
            progress=progress,
            on_district_done=lambda done, total, data: seen.append(done),
        )

    assert result.success
    # Ring 1 has 8 districts; resuming at index 5 leaves 3 to build.
    assert bd.call_count == 3
    assert len(seen) == 3
    assert progress["next_index"] == 8


def test_build_ring_stops_at_a_failed_district_without_advancing_progress(mock_client):
    """A failed district cannot be credited or skipped by a phase checkpoint."""
    outcomes = [
        city_mod.TaskResult.fail("boom", role="farm", origin=(0, 0), district=(1, 0)),
        city_mod.TaskResult.ok("fine", role="storage", origin=(0, 0), district=(1, 1)),
    ]
    progress = {}
    with patch.object(city_mod, "build_district", side_effect=outcomes * 4) as bd:
        result = build_ring(
            mock_client, 0, 0, target_y=64, ring=1, progress=progress
        )

    assert not result.success
    assert bd.call_count == 1
    assert progress.get("next_index", 0) == 0
    assert result.data["districts_completed"] == 0
