"""Tests for pure storage warehouse geometry."""

import pytest

from baritone_client.common.storage_warehouse import CATEGORY_ORDER, WarehouseLayout


@pytest.mark.parametrize(
    ("facing", "first", "paired", "building"),
    [
        ("north", (10, 64, 20), (11, 64, 20), (10, 64, 16)),
        ("east", (10, 64, 20), (10, 64, 21), (14, 64, 20)),
        ("south", (10, 64, 20), (9, 64, 20), (10, 64, 24)),
        ("west", (10, 64, 20), (10, 64, 19), (6, 64, 20)),
    ],
)
def test_slot_geometry_rotates_for_every_facing(facing, first, paired, building):
    layout = WarehouseLayout(first, facing)

    assert layout.slot("intake").canonical_coordinate == first
    assert layout.slot("intake").paired_coordinate == paired
    assert layout.slot("building").canonical_coordinate == building


def test_restart_determinism_and_inverse_metadata():
    original = WarehouseLayout((3, 70, -8), "west")
    restarted = WarehouseLayout((3, 70, -8), "west")
    slot = original.slot("ores", 4)

    assert restarted.slot("ores", 4) == slot
    assert original.coordinate_metadata(slot.canonical_coordinate) == slot.metadata()
    assert original.coordinate_metadata(slot.paired_coordinate) == slot.metadata()
    assert original.coordinate_metadata((slot.canonical_coordinate[0] - 1, 70, slot.canonical_coordinate[2])) is None


def test_aisle_width_controls_row_pitch_without_changing_pairs():
    layout = WarehouseLayout((0, 64, 0), "south", aisle_width=5)

    assert layout.row_pitch == 6
    assert layout.slot("building").canonical_coordinate == (0, 64, 6)
    assert layout.slot("building", 1).canonical_coordinate == (-3, 64, 6)
    with pytest.raises(ValueError, match="at least 3"):
        WarehouseLayout((0, 64, 0), "south", aisle_width=2)


def test_capacity_expands_with_a_separator_and_categories_remain_ordered():
    layout = WarehouseLayout((0, 64, 0), "north")

    assert layout.categories == CATEGORY_ORDER
    assert layout.slot("intake", 1).coordinates == ((3, 64, 0), (4, 64, 0))
    assert layout.slot("rare").row == CATEGORY_ORDER.index("rare")
    assert layout.slot("intake").zone == "intake"
    assert layout.slot("rare").zone == "category"


def test_planned_slots_never_overlap_and_inputs_are_validated():
    layout = WarehouseLayout((0, 64, 0), "east")
    coordinates = [coordinate for category in CATEGORY_ORDER for index in range(6) for coordinate in layout.slot(category, index).coordinates]

    assert len(coordinates) == len(set(coordinates))
    with pytest.raises(ValueError, match="nonnegative"):
        layout.slot("intake", -1)
    with pytest.raises(ValueError, match="facing"):
        WarehouseLayout((0, 64, 0), "up")
    with pytest.raises(ValueError, match="row 0"):
        WarehouseLayout((0, 64, 0), "north", categories=("building",))
