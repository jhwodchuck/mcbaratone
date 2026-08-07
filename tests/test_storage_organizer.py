from pathlib import Path
from types import SimpleNamespace

import pytest

from baritone_client.common import storage_organizer as organizer
from baritone_client.common.storage_catalog import StorageCatalog
from baritone_client.common.storage_organizer import (
    StorageJob,
    category_for_item,
    plan_storage_job,
)


def test_item_categories_are_broad_and_stable():
    assert category_for_item("minecraft:raw_iron") == "ores"
    assert category_for_item("minecraft:spruce_log") == "wood"
    assert category_for_item("minecraft:bread") == "food"
    assert category_for_item("minecraft:iron_pickaxe") == "tools"
    assert category_for_item("minecraft:leather") == "mob_drops"
    assert category_for_item("minecraft:cobblestone") == "building"
    assert category_for_item("minecraft:shulker_shell") == "rare"


def _register_verified_intake(catalog):
    catalog.register_warehouse(
        "warehouse_01",
        dimension="minecraft:overworld",
        anchor=(10, 64, 10),
        facing="north",
        expansion_direction="positive_local_x",
        aisle_width=3,
    )
    catalog.reserve_slot(
        "warehouse_01",
        "intake",
        "intake",
        0,
        paired_coordinates=((10, 64, 10), (11, 64, 10)),
        canonical_coordinate=(10, 64, 10),
        state="verified",
        metadata={"row": 0},
    )


def test_planner_migrates_largest_category_to_managed_destination(tmp_path):
    catalog = StorageCatalog(Path(tmp_path) / "catalog.sqlite3", "world-a")
    _register_verified_intake(catalog)
    source = (1, 64, 1)
    destination = (5, 64, 1)
    catalog.observe_inventory(
        source,
        [
            {"slot": 0, "id": "minecraft:raw_iron", "count": 48},
            {"slot": 1, "id": "minecraft:bread", "count": 12},
        ],
        dimension="minecraft:overworld",
        capacity_slots=27,
        purpose="legacy_storage",
    )
    catalog.observe_inventory(
        destination,
        [],
        dimension="minecraft:overworld",
        capacity_slots=54,
        label="quartermaster:ores",
        purpose="quartermaster:ores",
    )

    job = plan_storage_job(catalog)

    assert job is not None
    assert job.source == source
    assert job.destination == destination
    assert job.category == "ores"
    assert job.retire_source_when_empty


def test_planner_establishes_warehouse_intake_before_migration(tmp_path):
    catalog = StorageCatalog(Path(tmp_path) / "catalog.sqlite3", "world-a")
    catalog.observe_inventory(
        (1, 64, 1),
        [{"slot": 0, "id": "minecraft:raw_iron", "count": 48}],
        dimension="minecraft:overworld",
        capacity_slots=27,
        purpose="legacy_storage",
    )

    job = plan_storage_job(catalog)

    assert job is not None
    assert job.category == "intake"
    assert job.capacity_only


def test_category_storage_uses_and_verifies_deterministic_warehouse_slot(
    tmp_path, monkeypatch
):
    catalog = StorageCatalog(Path(tmp_path) / "catalog.sqlite3", "world-a")
    placed = []
    monkeypatch.setattr(
        organizer.harness_ops,
        "find_double_chest_spot",
        lambda _client, **_kwargs: ((0, 64, 0), (1, 64, 0)),
    )
    monkeypatch.setattr(
        organizer.harness_ops,
        "_block_at",
        lambda _client, _x, y, _z: "minecraft:stone" if y == 63 else "minecraft:air",
    )
    monkeypatch.setattr(organizer, "count_item", lambda *_args: 2)
    monkeypatch.setattr(
        organizer,
        "create_double_chest_at",
        lambda _client, first, second, **_kwargs: placed.append((first, second))
        or (first, second),
    )
    monkeypatch.setattr(
        organizer,
        "_open_snapshot",
        lambda *_args: ({"slots": [], "total_slots": 90}, 54),
    )
    monkeypatch.setattr(organizer.harness_ops, "close_container", lambda *_args: None)

    position = organizer._create_category_storage(
        SimpleNamespace(), SimpleNamespace(), catalog, "ores", "minecraft:overworld"
    )

    assert position == (0, 64, -12)
    assert placed == [((0, 64, -12), (1, 64, -12))]
    reservation = catalog.get_slot_reservation(
        "warehouse_01", "category", "ores", 0
    )
    assert reservation["state"] == "verified"
    assert reservation["canonical_coordinate"] == position


def test_each_dimension_gets_a_distinct_warehouse_identity(tmp_path, monkeypatch):
    catalog = StorageCatalog(Path(tmp_path) / "catalog.sqlite3", "world-a")
    catalog.register_warehouse(
        "warehouse_01",
        dimension="minecraft:overworld",
        anchor=(0, 64, 0),
        facing="north",
        expansion_direction="positive_local_x",
        aisle_width=3,
    )
    monkeypatch.setattr(
        organizer.harness_ops,
        "find_double_chest_spot",
        lambda _client, **_kwargs: ((20, 64, 20), (21, 64, 20)),
    )

    warehouse, _layout = organizer.ensure_warehouse_layout(
        SimpleNamespace(), catalog, "minecraft:the_nether"
    )

    assert warehouse["warehouse_id"] == "warehouse_01_the_nether"


def test_void_backed_unplaced_warehouse_is_rehomed_without_rewriting_history(
    tmp_path, monkeypatch
):
    """A stale floating anchor must not permanently block Quartermaster work."""
    catalog = StorageCatalog(Path(tmp_path) / "catalog.sqlite3", "world-a")
    catalog.register_warehouse(
        "warehouse_01",
        dimension="minecraft:overworld",
        anchor=(-28, 80, -1),
        facing="north",
        expansion_direction="positive_local_x",
        aisle_width=3,
    )
    catalog.reserve_slot(
        "warehouse_01", "intake", "intake", 0,
        paired_coordinates=((-28, 80, -1), (-27, 80, -1)),
        canonical_coordinate=(-28, 80, -1), state="blocked",
    )
    catalog.reserve_slot(
        "warehouse_01", "category", "building", 0,
        paired_coordinates=((-28, 80, -5), (-27, 80, -5)),
        canonical_coordinate=(-28, 80, -5), state="blocked",
    )
    monkeypatch.setattr(organizer.harness_ops, "_block_at", lambda *_args: "minecraft:void_air")
    monkeypatch.setattr(
        organizer.harness_ops, "find_double_chest_spot",
        lambda _client, **_kwargs: ((10, 64, 10), (11, 64, 10)),
    )

    replacement, _layout = organizer.ensure_warehouse_layout(
        SimpleNamespace(), catalog, "minecraft:overworld"
    )

    retired = catalog.get_warehouse("warehouse_01")
    assert replacement["warehouse_id"] == "warehouse_01_rehome_1"
    assert replacement["anchor"] == (10, 64, 10)
    assert replacement["metadata"]["replaces"] == "warehouse_01"
    assert retired["anchor"] == (-28, 80, -1)
    assert retired["metadata"]["lifecycle"] == "retired"
    assert retired["metadata"]["replaced_by"] == replacement["warehouse_id"]
    assert catalog.get_slot_reservation("warehouse_01", "intake", "intake", 0)["state"] == "blocked"


def test_supported_or_verified_warehouse_is_never_automatically_rehomed(tmp_path, monkeypatch):
    catalog = StorageCatalog(Path(tmp_path) / "catalog.sqlite3", "world-a")
    _register_verified_intake(catalog)
    monkeypatch.setattr(organizer.harness_ops, "_block_at", lambda *_args: "minecraft:void_air")
    monkeypatch.setattr(
        organizer.harness_ops, "find_double_chest_spot",
        lambda *_args, **_kwargs: pytest.fail("must not replace a verified warehouse"),
    )

    warehouse, _layout = organizer.ensure_warehouse_layout(
        SimpleNamespace(), catalog, "minecraft:overworld"
    )

    assert warehouse["warehouse_id"] == "warehouse_01"
    assert warehouse["metadata"].get("lifecycle") is None


def test_planner_never_touches_player_owned_storage(tmp_path):
    catalog = StorageCatalog(Path(tmp_path) / "catalog.sqlite3", "world-a")
    position = (1, 64, 1)
    catalog.observe_inventory(
        position,
        [{"slot": 0, "id": "minecraft:diamond", "count": 12}],
        dimension="minecraft:overworld",
        capacity_slots=27,
        purpose="legacy_storage",
    )
    catalog.register_container(
        position,
        dimension="minecraft:overworld",
        purpose="legacy_storage",
        metadata={"player_owned": True},
    )

    assert plan_storage_job(catalog) is None


def test_planner_never_routes_storage_coordinates_across_dimensions(tmp_path):
    catalog = StorageCatalog(Path(tmp_path) / "catalog.sqlite3", "world-a")
    catalog.observe_inventory(
        (1, 64, 1),
        [{"slot": 0, "id": "minecraft:quartz", "count": 32}],
        dimension="minecraft:the_nether",
        capacity_slots=27,
        purpose="legacy_storage",
    )

    assert plan_storage_job(
        catalog, dimension="minecraft:overworld"
    ) is None


def test_cycle_leases_creates_capacity_and_records_verified_transfer(monkeypatch):
    events = []

    class FakeCatalog:
        def acquire_lease(self, key, owner, **_kwargs):
            events.append(("acquire", key, owner))
            return True

        def release_lease(self, key, owner):
            events.append(("release", key, owner))
            return True

        def register_container(self, position, **kwargs):
            events.append(("register", position, kwargs["status"]))

        def record_event(self, position, event_type, **_kwargs):
            events.append(("event", position, event_type))

    catalog = FakeCatalog()
    state = SimpleNamespace(custom_data={})
    job = StorageJob(
        "minecraft:overworld",
        "ores",
        "legacy ores",
        source=(1, 64, 1),
        retire_source_when_empty=True,
    )
    monkeypatch.setattr(organizer, "catalog_for", lambda *_args: catalog)
    monkeypatch.setattr(
        organizer, "plan_storage_job", lambda _catalog, **_kwargs: job
    )
    monkeypatch.setattr(
        organizer,
        "_create_category_storage",
        lambda *_args: (5, 64, 1),
    )
    monkeypatch.setattr(
        organizer,
        "_transfer_category_batch",
        lambda *_args: (48, 2, True),
    )

    client = SimpleNamespace(
        transport=SimpleNamespace(
            dispatch=lambda route, _payload: (
                {"dimension": "minecraft:overworld"}
                if route == "get_state"
                else {}
            )
        )
    )
    result = organizer.run_quartermaster_cycle(client, state)

    assert result.success
    assert result.items_moved == 48
    assert result.double_chests_created == 1
    assert state.custom_data["quartermaster"] == {
        "containers_audited": 1,
        "double_chests_created": 1,
        "free_slots_added": 54,
        "catalog_entries_refreshed": 4,
        "cycles": 1,
        "items_moved": 48,
        "stacks_moved": 2,
        "legacy_chests_emptied": 1,
    }
    assert events[0][0] == "acquire"
    assert events[-1][0] == "release"
    assert ("register", (1, 64, 1), "legacy_empty") in events


def test_existing_chest_at_planned_slot_is_blocked_not_adopted(
    tmp_path, monkeypatch
):
    catalog = StorageCatalog(Path(tmp_path) / "catalog.sqlite3", "world-a")
    monkeypatch.setattr(
        organizer.harness_ops,
        "find_double_chest_spot",
        lambda _client, **_kwargs: ((0, 64, 0), (1, 64, 0)),
    )
    monkeypatch.setattr(
        organizer.harness_ops,
        "_block_at",
        lambda _client, _x, y, _z: (
            "minecraft:chest" if y == 64 else "minecraft:stone"
        ),
    )
    monkeypatch.setattr(organizer, "count_item", lambda *_args: 2)
    monkeypatch.setattr(organizer, "create_double_chest_at", lambda *_args: None)

    with pytest.raises(RuntimeError, match="planned warehouse"):
        organizer._create_category_storage(
            SimpleNamespace(), SimpleNamespace(), catalog, "ores", "minecraft:overworld"
        )

    reservation = catalog.get_slot_reservation(
        "warehouse_01", "category", "ores", 0
    )
    assert reservation["state"] == "blocked"


def test_partial_warehouse_placement_resumes_only_from_confirmed_evidence(
    tmp_path, monkeypatch
):
    catalog = StorageCatalog(Path(tmp_path) / "catalog.sqlite3", "world-a")
    present = set()
    calls = []
    monkeypatch.setattr(
        organizer.harness_ops,
        "find_double_chest_spot",
        lambda _client, **_kwargs: ((0, 64, 0), (1, 64, 0)),
    )
    monkeypatch.setattr(
        organizer.harness_ops,
        "_block_at",
        lambda _client, x, y, z: (
            "minecraft:stone"
            if y == 63
            else "minecraft:chest"
            if (x, y, z) in present
            else "minecraft:air"
        ),
    )
    monkeypatch.setattr(organizer, "count_item", lambda *_args: 2)

    def place_pair(_client, first, second, *, allowed_existing, on_placed):
        calls.append(set(allowed_existing))
        if len(calls) == 1:
            present.add(first)
            on_placed(first)
            return None
        assert first in allowed_existing
        present.add(second)
        on_placed(second)
        return first, second

    monkeypatch.setattr(organizer, "create_double_chest_at", place_pair)
    monkeypatch.setattr(
        organizer,
        "_open_snapshot",
        lambda *_args: (
            {"slots": [], "total_slots": 63 if len(present) == 1 else 90},
            27 if len(present) == 1 else 54,
        ),
    )
    monkeypatch.setattr(organizer.harness_ops, "close_container", lambda *_args: None)
    worker_state = SimpleNamespace(checkpoint_dir="Bot04")

    with pytest.raises(RuntimeError, match="place or resume"):
        organizer._create_category_storage(
            SimpleNamespace(), worker_state,
            catalog, "ores", "minecraft:overworld"
        )
    partial = catalog.get_slot_reservation(
        "warehouse_01", "category", "ores", 0
    )
    assert partial["state"] == "building"
    assert partial["metadata"]["confirmed_coordinates"] == [[0, 64, -12]]

    position = organizer._create_category_storage(
        SimpleNamespace(), worker_state,
        catalog, "ores", "minecraft:overworld"
    )

    assert position == (0, 64, -12)
    assert calls == [set(), {(0, 64, -12)}]
    verified = catalog.get_slot_reservation(
        "warehouse_01", "category", "ores", 0
    )
    assert verified["state"] == "verified"
    assert verified["metadata"]["confirmed_coordinates"] == [
        [0, 64, -12], [1, 64, -12]
    ]


def test_confirmed_partial_chest_must_still_be_single_and_empty(
    tmp_path, monkeypatch
):
    catalog = StorageCatalog(Path(tmp_path) / "catalog.sqlite3", "world-a")
    catalog.register_warehouse(
        "warehouse_01",
        dimension="minecraft:overworld",
        anchor=(0, 64, 0),
        facing="north",
        expansion_direction="positive_local_x",
        aisle_width=3,
    )
    catalog.reserve_slot(
        "warehouse_01",
        "category",
        "ores",
        0,
        paired_coordinates=((0, 64, -12), (1, 64, -12)),
        canonical_coordinate=(0, 64, -12),
        state="building",
        metadata={
            "row": 3,
            "confirmed_coordinates": [[0, 64, -12]],
        },
    )
    monkeypatch.setattr(
        organizer.harness_ops,
        "_block_at",
        lambda _client, x, y, z: (
            "minecraft:stone"
            if y == 63
            else "minecraft:chest"
            if (x, y, z) == (0, 64, -12)
            else "minecraft:air"
        ),
    )
    monkeypatch.setattr(
        organizer,
        "_open_snapshot",
        lambda *_args: (
            {"slots": [{"slot": 0, "id": "minecraft:diamond", "count": 1}]},
            27,
        ),
    )
    monkeypatch.setattr(organizer.harness_ops, "close_container", lambda *_args: None)
    worker_state = SimpleNamespace(
        _warehouse_partial_coordinates={(0, 64, -12)}
    )

    with pytest.raises(RuntimeError, match="not empty"):
        organizer._create_category_storage(
            SimpleNamespace(), worker_state, catalog, "ores", "minecraft:overworld"
        )

    reservation = catalog.get_slot_reservation(
        "warehouse_01", "category", "ores", 0
    )
    assert reservation["state"] == "blocked"
    assert "replaced" in reservation["metadata"]["blocked_reason"]


def test_restart_does_not_adopt_a_catalog_confirmed_partial_chest(
    tmp_path, monkeypatch
):
    catalog = StorageCatalog(Path(tmp_path) / "catalog.sqlite3", "world-a")
    catalog.register_warehouse(
        "warehouse_01",
        dimension="minecraft:overworld",
        anchor=(0, 64, 0),
        facing="north",
        expansion_direction="positive_local_x",
        aisle_width=3,
    )
    catalog.reserve_slot(
        "warehouse_01", "category", "ores", 0,
        paired_coordinates=((0, 64, -12), (1, 64, -12)),
        canonical_coordinate=(0, 64, -12), state="building",
        metadata={"row": 3, "confirmed_coordinates": [[0, 64, -12]]},
    )
    monkeypatch.setattr(
        organizer.harness_ops,
        "_block_at",
        lambda _client, x, y, z: (
            "minecraft:stone"
            if y == 63
            else "minecraft:chest"
            if (x, y, z) == (0, 64, -12)
            else "minecraft:air"
        ),
    )

    with pytest.raises(RuntimeError, match="planned warehouse"):
        organizer._create_category_storage(
            SimpleNamespace(), SimpleNamespace(), catalog, "ores", "minecraft:overworld"
        )

    reservation = catalog.get_slot_reservation(
        "warehouse_01", "category", "ores", 0
    )
    assert reservation["state"] == "blocked"
