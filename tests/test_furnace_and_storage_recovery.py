"""Two faults from one A1 stall: no fuel, and a home address 412m stale.

Both were visible in the same trace at (-8, 160, 9), on a ~24 second cycle for
hours while NETHER_AND_BLAZE waited on six iron ingots:

    Resuming loaded furnace at (-7, 160, 7) (1 minecraft:raw_iron pending)...
    Loaded furnace stalled without fuel.
    Gathering cleanup skipped distant home storage (412.6 blocks away).

The bot carried 9 oak planks and 2 oak logs, and a verified chest holding 14 of
27 slots stood at (-8, 160, 8) -- one block away.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from baritone_client.common import furnace_recovery, space_reclaim


def test_a_stalled_furnace_is_refuelled_from_carried_stock():
    """Uses the bridge's own smelt_items, which resolves the container live.

    The hand-rolled inventory_click this replaced passed a sync_id read from
    an earlier furnace snapshot. A stale one is dropped by the server without
    error, so three refuels in a row reported success while the fuel slot
    stayed empty.
    """
    clicks = []
    data = {
        "sync_id": 7,
        "slots": [
            {"slot": 0, "id": "minecraft:raw_iron", "count": 1},
            {"slot": 1, "id": "minecraft:air", "count": 0},
            {"slot": 5, "id": "minecraft:oak_planks", "count": 9},
        ],
    }
    routes = []
    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda r, p: (routes.append(r), clicks.append(p)) and {}
        )
    )

    assert furnace_recovery._load_carried_fuel(client, data)
    assert clicks == [{"fuel_slot": 5}], clicks
    assert routes == ["smelt_items"], routes
    # No sync_id: the bridge resolves the container at click time.
    assert "sync_id" not in clicks[0]


def test_furnace_slots_are_never_mistaken_for_carried_fuel():
    """Slots 0-2 are the furnace itself; shifting those would be a no-op loop."""
    data = {"slots": [{"slot": 1, "id": "minecraft:oak_planks", "count": 4}]}
    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=lambda *_a, **_k: {})
    )
    assert not furnace_recovery._load_carried_fuel(client, data)


def test_a_furnace_with_no_carried_fuel_still_reports_the_stall():
    data = {"slots": [{"slot": 4, "id": "minecraft:cobblestone", "count": 64}]}
    client = SimpleNamespace(
        transport=SimpleNamespace(dispatch=lambda *_a, **_k: {})
    )
    assert not furnace_recovery._load_carried_fuel(client, data)


def test_the_refuel_is_tried_before_giving_up():
    source = inspect.getsource(furnace_recovery.resume_active_furnace)
    stall = source[source.index("if not lit and fuel_empty:"):]
    assert stall.index("_load_carried_fuel") < stall.index("stalled without fuel")


def test_refuelling_is_bounded_so_a_dead_move_cannot_spin():
    """Dispatching the shift is not evidence the fuel landed.

    Live A1 2026-09-03: the first, unbounded version logged "Refuelled the
    stalled furnace from carried stock" once a second indefinitely while the
    fuel slot stayed empty -- the same dispatch-and-assume shape the rest of
    this work has been removing, reintroduced by the fix for it.
    """
    assert furnace_recovery.MAX_REFUEL_ATTEMPTS <= 5
    source = inspect.getsource(furnace_recovery.resume_active_furnace)
    assert "refuels < MAX_REFUEL_ATTEMPTS" in source
    assert "refuels += 1" in source


def _catalog(rows):
    return SimpleNamespace(list_containers=lambda: rows)


def test_a_nearer_catalogued_chest_is_used_when_home_is_stale(monkeypatch):
    monkeypatch.setattr(
        "baritone_client.common.storage_catalog.catalog_for",
        lambda _c: _catalog([
            {"x": -412, "y": 79, "z": -17, "capacity_slots": 27, "occupied_slots": 1},
            {"x": -8, "y": 160, "z": 8, "capacity_slots": 27, "occupied_slots": 14},
        ]),
    )
    snapshot = {"block_position": {"x": -8, "y": 160, "z": 9}}
    found = space_reclaim.nearest_usable_container(SimpleNamespace(), snapshot, 64.0)
    assert found == (-8, 160, 8), found


def test_a_full_chest_is_not_offered_as_storage(monkeypatch):
    monkeypatch.setattr(
        "baritone_client.common.storage_catalog.catalog_for",
        lambda _c: _catalog([
            {"x": -8, "y": 160, "z": 8, "capacity_slots": 27, "occupied_slots": 27},
        ]),
    )
    snapshot = {"block_position": {"x": -8, "y": 160, "z": 9}}
    assert space_reclaim.nearest_usable_container(
        SimpleNamespace(), snapshot, 64.0
    ) is None


def test_chests_beyond_the_travel_budget_are_not_offered(monkeypatch):
    """The fallback must not reintroduce the 412-block walk it exists to avoid."""
    monkeypatch.setattr(
        "baritone_client.common.storage_catalog.catalog_for",
        lambda _c: _catalog([
            {"x": -412, "y": 79, "z": -17, "capacity_slots": 27, "occupied_slots": 1},
        ]),
    )
    snapshot = {"block_position": {"x": -8, "y": 160, "z": 9}}
    assert space_reclaim.nearest_usable_container(
        SimpleNamespace(), snapshot, 64.0
    ) is None


def test_the_gathering_reserve_falls_back_instead_of_skipping():
    from baritone_client.common import resources

    source = inspect.getsource(resources)
    block = source[source.index("Gathering paused to reserve"):]
    block = block[: block.index("def manage_inventory")]
    assert "nearest_usable_container" in block
    # The old dead end must be gone, not merely bypassed.
    assert "skipped distant home storage" not in block
