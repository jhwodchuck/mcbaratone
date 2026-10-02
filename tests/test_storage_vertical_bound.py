"""Banking tours never descend into basements and caves to find a chest."""

from types import SimpleNamespace

from baritone_client.common import space_reclaim, storage_safety

SNAPSHOT = {
    "dimension": "minecraft:overworld",
    "block_position": {"x": -411, "y": 79, "z": -13},
}
SUPPLY = (-412, 79, -13)  # same level, full
BASEMENT = (-411, 75, -12)  # 4 below, in the hollow under the house
CAVE = (-385, 56, -13)  # 23 below, in the caves


def _catalog(monkeypatch, rows):
    monkeypatch.setattr(
        "baritone_client.common.storage_catalog.catalog_for",
        lambda *_a, **_k: SimpleNamespace(
            list_containers=lambda: [
                {"dimension": "minecraft:overworld", "x": x, "y": y, "z": z} for x, y, z in rows
            ]
        ),
    )


def test_a_tour_offers_only_chests_near_the_players_own_level(monkeypatch):
    """Live A1 2026-10-01 died walking basement chest -> cave chest."""
    _catalog(monkeypatch, [SUPPLY, BASEMENT, CAVE, (-420, 77, -10)])

    tour = list(storage_safety.nearby_storage_positions(SimpleNamespace(), SNAPSHOT))

    assert BASEMENT not in tour and CAVE not in tour
    assert tour == [SUPPLY, (-420, 77, -10)]  # nearest first, both within 3 blocks of y=79


def test_a_bot_that_is_itself_underground_still_finds_its_own_level_chests(monkeypatch):
    _catalog(monkeypatch, [SUPPLY, BASEMENT, CAVE])
    mine = {"dimension": "minecraft:overworld", "block_position": {"x": -411, "y": 75, "z": -13}}

    assert list(storage_safety.nearby_storage_positions(SimpleNamespace(), mine)) == [BASEMENT]


def test_the_vertical_bound_can_be_widened_by_a_caller_that_means_it(monkeypatch):
    _catalog(monkeypatch, [SUPPLY, BASEMENT, CAVE])

    tour = list(storage_safety.nearby_storage_positions(SimpleNamespace(), SNAPSHOT, maximum_vertical=30))

    assert set(tour) == {SUPPLY, BASEMENT, CAVE}


def test_the_nearest_chest_shortcut_ignores_a_chest_straight_below(monkeypatch):
    rows = [
        {"x": x, "y": y, "z": z, "capacity_slots": 27, "occupied_slots": 3}
        for x, y, z in (BASEMENT, (-400, 79, -13))
    ]
    monkeypatch.setattr(
        "baritone_client.common.storage_catalog.catalog_for",
        lambda *_a, **_k: SimpleNamespace(list_containers=lambda: rows),
    )
    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=lambda *_a, **_k: {"id": "minecraft:chest"})
    )

    best = space_reclaim.nearest_usable_container(client, SNAPSHOT, 96.0)

    # The basement chest is 4.1 blocks away in 3D; the level one is 11. Level wins.
    assert best == (-400, 79, -13)
